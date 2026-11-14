"""Démo hors ligne : rejoue un scénario avec une horloge accélérée et affiche la chronologie."""

from __future__ import annotations

from ..config import AppConfig
from ..sms_contract import SmsStatus
from .runner import ScenarioRunner, demo_config
from .scenarios import get_scenario


def run_headless_demo(
    scenario_name: str, countdown_s: float | None, reply_delay_s: float | None, cancel_after_s: float | None
) -> int:
    scenario = get_scenario(scenario_name)
    config: AppConfig = demo_config(**({"countdown_seconds": countdown_s} if countdown_s else {}))
    runner = ScenarioRunner(scenario, config)
    state = {"cancel_at": None, "reply_at": None}

    def watch(run: ScenarioRunner) -> None:
        view = run.core.engine.view()
        if view is None:
            return
        incident = run.core.engine.incidents()[-1]
        if cancel_after_s is not None and view.can_cancel and state["cancel_at"] is None:
            if run.now_s - incident.created_monotonic >= cancel_after_s:
                state["cancel_at"] = run.now_s
                run.core.engine.cancel("annulation simulée par la démo")
        if reply_delay_s is not None and incident.sms_requested_wall and state["reply_at"] is None:
            if (run.clock.wall() - incident.sms_requested_wall).total_seconds() >= reply_delay_s:
                state["reply_at"] = run.now_s
                run.inject_reply()

    step = 1.0 / scenario.fps
    t = 0.0
    while t <= scenario.duration_s:
        runner.at(t, watch)
        t += step
    result = runner.run(until_s=scenario.duration_s)
    print(f"Scénario : {scenario.name} — {scenario.description}")
    print(f"Attendu : {scenario.expected}\n")
    print(result.log_text)
    print(f"\nVoix prononcées : {len(result.voice.spoken)}")
    print(f"SMS soumis (simulés) : {len(result.gateway.submitted_requests)}")
    print(f"Incidents : {', '.join(result.incident_ids) or 'aucun'}")
    submitted = [i for i in result.core.engine.incidents() if i.sms_status is SmsStatus.SIMULATED]
    return 0 if (submitted or cancel_after_s is not None or not result.incident_ids) else 1
