"""Outils communs : ports factices, horloge contrôlable et exécution des scénarios simulés."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pytest

from faaall_alarm.clock import ManualClock
from faaall_alarm.detection import DetectionPipeline
from faaall_alarm.incident import IncidentEngine, IncidentSettings
from faaall_alarm.model import DetectionEvent, EventKind, FallCause, Posture
from faaall_alarm.privacy import default_masker
from faaall_alarm.simulation.runner import DEMO_CONTACT_NUMBER, DEMO_TWILIO_NUMBER
from faaall_alarm.simulation.scenarios import Scenario, get_scenario
from faaall_alarm.sms_contract import AlertRequest

# Les tests ne dépendent d'aucun SMS réel : aucune configuration Twilio ne doit servir ici.
for variable in ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "OPENAI_API_KEY"):
    os.environ.pop(variable, None)


@pytest.fixture(autouse=True)
def _reset_masker():
    default_masker().clear_registered()
    yield
    default_masker().clear_registered()


@dataclass
class FakePorts:
    """Ports factices : enregistrent l'ordre des appels. La voix et le SMS se terminent à la demande."""

    calls: list[str] = field(default_factory=list)
    spoken: list[tuple[str, str]] = field(default_factory=list)
    submitted: list[AlertRequest] = field(default_factory=list)
    interpretation_requests: list[tuple[str, str, str]] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)
    records: list[tuple[str, dict]] = field(default_factory=list)
    fail_submit_with: Exception | None = None

    def speak(self, incident_id: str, text: str) -> None:
        self.calls.append("speak")
        self.spoken.append((incident_id, text))

    def submit_sms(self, request: AlertRequest) -> None:
        self.calls.append("submit_sms")
        if self.fail_submit_with:
            raise self.fail_submit_with
        self.submitted.append(request)

    def interpret_reply(self, incident_id: str, message_id: str, text: str) -> None:
        self.calls.append("interpret_reply")
        self.interpretation_requests.append((incident_id, message_id, text))

    def log(self, message: str) -> None:
        self.logs.append(message)

    def record(self, event: str, **fields: object) -> None:
        self.calls.append(f"record:{event}")
        self.records.append((event, fields))

    @property
    def log_text(self) -> str:
        return "\n".join(self.logs)


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def ports() -> FakePorts:
    return FakePorts()


def default_settings(**overrides) -> IncidentSettings:
    values = dict(
        countdown_seconds=10.0, first_name="Marie", address="123, rue Exemple, Montréal", contact_name="Sophie",
        contact_number=DEMO_CONTACT_NUMBER, twilio_number=DEMO_TWILIO_NUMBER, voice_enabled=True,
        voice_message="Message d'alerte de test",
    )
    values.update(overrides)
    return IncidentSettings(**values)


@pytest.fixture
def engine(clock, ports) -> IncidentEngine:
    return IncidentEngine(clock, ports, default_settings)


def fall_event(track: int = 1, cause: FallCause = FallCause.INDIVIDUAL, t: float = 0.0) -> DetectionEvent:
    return DetectionEvent(EventKind.FALL_CONFIRMED, t, (track,), cause=cause, detail="chute")


def recovered_event(track: int = 1, t: float = 0.0) -> DetectionEvent:
    return DetectionEvent(EventKind.PERSON_RECOVERED, t, (track,), detail="reprise")


def analyse(scenario: Scenario, **pipeline_kwargs) -> list[DetectionEvent]:
    """Passe un scénario dans le détecteur seul et retourne tous les événements émis."""
    pipeline = DetectionPipeline(lying_zones=scenario.lying_zones, **pipeline_kwargs)
    events: list[DetectionEvent] = []
    for t in scenario.frame_times():
        if scenario.camera_available(float(t)):
            events += pipeline.process(scenario.observation_at(float(t))).events
    return events


def posture_timeline(scenario: Scenario, person_index: int = 0, **pipeline_kwargs) -> list[tuple[float, Posture]]:
    """Posture affichée image par image pour une personne (par ordre de création de trace)."""
    pipeline = DetectionPipeline(lying_zones=scenario.lying_zones, **pipeline_kwargs)
    timeline: list[tuple[float, Posture]] = []
    for t in scenario.frame_times():
        if not scenario.camera_available(float(t)):
            continue
        persons = sorted(pipeline.process(scenario.observation_at(float(t))).persons, key=lambda p: p.track_id)
        if len(persons) > person_index:
            timeline.append((float(t), persons[person_index].posture))
    return timeline


def compress(values: list) -> list:
    """Supprime les répétitions consécutives : [A, A, B, A] -> [A, B, A]."""
    return [value for index, value in enumerate(values) if index == 0 or value != values[index - 1]]


def kinds(events: list[DetectionEvent], kind: EventKind) -> list[DetectionEvent]:
    return [event for event in events if event.kind is kind]


@pytest.fixture
def scenario():
    return get_scenario
