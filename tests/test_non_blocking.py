"""Les appels lents (voix, SMS, lecture des réponses, interprétation) ne bloquent ni l'analyse ni le décompte."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from faaall_alarm.core import MonitoringCore
from faaall_alarm.incident import IncidentState
from faaall_alarm.logbook import HistoryStore
from faaall_alarm.model import SourceKind
from faaall_alarm.simulation.runner import RecordingVoice, ScenarioRunner, demo_config
from faaall_alarm.simulation.scenarios import get_scenario
from faaall_alarm.sms_contract import SmsAvailability, SmsStatus, SmsSubmission
from faaall_alarm.sms_simulated import SimulatedReplyInterpreter, SimulatedSmsGateway


class BlockedGateway(SimulatedSmsGateway):
    """Passerelle dont l'envoi, la lecture des réponses et le statut restent bloqués jusqu'à `release`."""

    def __init__(self, clock) -> None:
        super().__init__(clock)
        self.release = threading.Event()
        self.entered = threading.Event()

    def check_availability(self) -> SmsAvailability:
        self.release.wait(10)
        return super().check_availability()

    def submit_alert(self, request) -> SmsSubmission:
        self.entered.set()
        self.release.wait(10)
        return super().submit_alert(request)

    def fetch_replies(self, since):
        self.release.wait(10)
        return super().fetch_replies(since)


def test_envoi_sms_bloque_ne_gele_ni_l_analyse_ni_l_etat_de_l_incident(tmp_path):
    runner = ScenarioRunner(get_scenario("chute_debout"))
    gateway = BlockedGateway(runner.clock)
    executor = ThreadPoolExecutor(max_workers=3)
    poll_executor = ThreadPoolExecutor(max_workers=2)
    core = MonitoringCore(
        demo_config(), runner.clock, RecordingVoice(), gateway, SimulatedReplyInterpreter(),
        HistoryStore(tmp_path / "h.jsonl", "test", runner.clock), executor, SourceKind.SIMULATION,
        poll_executor=poll_executor,
    )
    scenario, durations = get_scenario("chute_debout"), []
    try:
        for t in scenario.frame_times():
            runner.clock.advance_to(float(t))
            started = time.perf_counter()
            core.process(scenario.observation_at(float(t)), float(t))
            core.poll_background(reply_interval_s=0.1)
            durations.append(time.perf_counter() - started)
            if float(t) > 17.0:
                break
        assert gateway.entered.wait(2), "l'échéance aurait dû lancer la soumission"
        view = core.engine.view()
        assert view.state is IncidentState.HANDLED and view.sms_status is SmsStatus.PENDING
        # Envoi bloqué depuis des secondes, mais chaque image a été traitée sans attendre le réseau.
        assert max(durations) < 0.5, f"image la plus lente : {max(durations):.2f} s"
        assert core.engine.view().can_close  # l'écran garde ses commandes pendant la panne
    finally:
        gateway.release.set()
        executor.shutdown(wait=True)
        poll_executor.shutdown(wait=True)
    assert core.engine.view().sms_status is SmsStatus.SIMULATED  # le résultat revient une fois débloqué


def test_un_sondage_bloque_ne_s_empile_pas(tmp_path):
    """Un appel réseau qui dépasse l'intervalle de sondage n'en lance pas un second en parallèle."""
    runner = ScenarioRunner(get_scenario("chute_debout"))
    gateway = BlockedGateway(runner.clock)
    started = []
    original = gateway.fetch_replies
    gateway.fetch_replies = lambda since: (started.append(1), original(since))[1]
    poll_executor = ThreadPoolExecutor(max_workers=2)
    core = MonitoringCore(
        demo_config(), runner.clock, RecordingVoice(), gateway, SimulatedReplyInterpreter(),
        HistoryStore(tmp_path / "h.jsonl", "test", runner.clock), ThreadPoolExecutor(max_workers=1),
        SourceKind.SIMULATION, poll_executor=poll_executor,
    )
    scenario = get_scenario("chute_debout")
    try:
        for t in scenario.frame_times():
            runner.clock.advance_to(float(t))
            core.process(scenario.observation_at(float(t)), float(t))
            core.poll_background(reply_interval_s=0.05)
            if float(t) > 22.0:
                break
        time.sleep(0.2)
        assert len(started) == 1, f"{len(started)} sondages lancés en parallèle"
    finally:
        gateway.release.set()
        poll_executor.shutdown(wait=True)
        core.executor.shutdown(wait=True)
