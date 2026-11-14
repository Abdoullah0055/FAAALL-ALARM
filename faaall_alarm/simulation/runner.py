"""Pilotage hors ligne d'un scénario : horloge contrôlable, voix factice et SMS simulé, sans périphérique."""

from __future__ import annotations

import tempfile
from concurrent.futures import Executor, Future
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from ..clock import ManualClock
from ..config import AppConfig, DetectionThresholds
from ..core import MonitoringCore
from ..logbook import HistoryStore
from ..model import SourceKind, SourceStatus
from ..sms_contract import ReplyInterpreter, SmsGateway
from ..sms_simulated import SimulatedReplyInterpreter, SimulatedSmsGateway
from ..voice import VoiceResult
from .scenarios import Scenario

DEMO_CONTACT_NUMBER = "+15145550123"  # numéros factices réservés aux exemples
DEMO_TWILIO_NUMBER = "+15145550199"
DEMO_REPLY_TEXT = "Je suis en route, j'arrive dans environ 20 minutes"


def demo_config(**overrides) -> AppConfig:
    values = dict(
        monitored_first_name="Marie", alert_address="123, rue Exemple, Montréal", contact_name="Sophie",
        contact_number=DEMO_CONTACT_NUMBER, twilio_number=DEMO_TWILIO_NUMBER, countdown_seconds=10.0,
    )
    values.update(overrides)
    return AppConfig(**values)


class ImmediateExecutor(Executor):
    """Exécute chaque tâche tout de suite : rend les tests déterministes."""

    def submit(self, fn, /, *args, **kwargs) -> Future:
        future: Future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:  # noqa: BLE001
            future.set_exception(error)
        return future


class RecordingVoice:
    """Voix factice : mémorise les messages prononcés, peut simuler un moteur indisponible."""

    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.spoken: list[str] = []

    def is_available(self) -> bool:
        return self.available

    def speak(self, text: str) -> VoiceResult:
        self.spoken.append(text)
        return VoiceResult(True) if self.available else VoiceResult(False, "voix simulée indisponible")


@dataclass
class RunResult:
    core: MonitoringCore
    clock: ManualClock
    voice: RecordingVoice
    gateway: SmsGateway
    log_text: str = ""
    incident_ids: list[str] = field(default_factory=list)


class ScenarioRunner:
    """Rejoue un scénario image par image. `at(t, action)` programme une action utilisateur ou externe."""

    def __init__(
        self,
        scenario: Scenario,
        config: AppConfig | None = None,
        thresholds: DetectionThresholds | None = None,
        voice: RecordingVoice | None = None,
        gateway: SmsGateway | None = None,
        interpreter: ReplyInterpreter | None = None,
        history_path: Path | None = None,
        source_kind: SourceKind = SourceKind.SIMULATION,
    ) -> None:
        self.scenario = scenario
        self.clock = ManualClock(start_wall=datetime(2026, 1, 15, 14, 32, 0))
        base_config = config or demo_config()
        zones = [list(zone) for zone in scenario.lying_zones]  # les zones de repos font partie de la scène
        self.config = replace(base_config, lying_zones=zones or base_config.lying_zones)
        self.voice = voice or RecordingVoice()
        self.gateway = gateway or SimulatedSmsGateway(self.clock)
        history = HistoryStore(
            history_path or Path(tempfile.mkdtemp(prefix="faaall-history-")) / "history.jsonl", "test", self.clock
        )
        self.core = MonitoringCore(
            self.config, self.clock, self.voice, self.gateway, interpreter or SimulatedReplyInterpreter(),
            history, ImmediateExecutor(), source_kind, thresholds,
        )
        self.core.verify_sms_availability()
        self._actions: list[tuple[float, Callable[["ScenarioRunner"], None]]] = []

    @property
    def now_s(self) -> float:
        return self.clock.monotonic()

    def at(self, t: float, action: Callable[["ScenarioRunner"], None]) -> "ScenarioRunner":
        self._actions.append((t, action))
        self._actions.sort(key=lambda item: item[0])
        return self

    def run(self, until_s: float | None = None) -> RunResult:
        end = min(until_s if until_s is not None else self.scenario.duration_s, self.scenario.duration_s + 60.0)
        step = 1.0 / self.scenario.fps
        t = 0.0
        while t <= end + 1e-9:
            self.clock.advance_to(t)
            self.core.current_video_time_s = t
            self._fire_due_actions(t)
            if self.scenario.camera_available(t):
                self.core.set_source_status(SourceStatus.ACTIVE)
                if t < self.scenario.duration_s:
                    self.core.process(self.scenario.observation_at(t), video_time_s=t)
            else:
                self.core.set_source_status(SourceStatus.LOST)
            self.core.tick()
            self.core.poll_background(reply_interval_s=1.0)
            t += step
        return RunResult(self.core, self.clock, self.voice, self.gateway, self.core.log.export_text(),
                         [i.incident_id for i in self.core.engine.incidents()])

    def _fire_due_actions(self, t: float) -> None:
        while self._actions and self._actions[0][0] <= t + 1e-9:
            _, action = self._actions.pop(0)
            action(self)

    def inject_reply(self, text: str = DEMO_REPLY_TEXT, sender: str | None = None) -> None:
        assert isinstance(self.gateway, SimulatedSmsGateway)
        self.gateway.inject_reply(text, sender or self.config.contact_number, self.config.twilio_number)
