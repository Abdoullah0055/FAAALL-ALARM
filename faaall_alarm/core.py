"""Cœur applicatif sans interface : détection, moteur d'incident, voix, SMS et journaux.

Utilisé tel quel par l'application Windows (avec des threads et des exécuteurs) et par les tests et
la démo hors ligne (exécuteur synchrone et horloge contrôlable).
"""

from __future__ import annotations

from concurrent.futures import Executor

from .clock import Clock
from .config import AppConfig, DetectionThresholds
from .detection import DetectionPipeline
from .incident import IncidentEngine, IncidentState, incident_settings_from
from .logbook import DetectionLog, HistoryStore
from .model import (
    POSTURE_DISPLAY_PRIORITY, FrameAnalysis, FrameObservation, Posture, SourceKind, SourceStatus,
)
from .privacy import SensitiveMasker, default_masker
from .sms_contract import (
    AlertRequest, InterpretationUnavailable, ReplyInterpreter, SmsAvailability, SmsGateway, SmsStatus,
)
from .voice import Voice, VoiceResult

_FINAL_SMS_STATUSES = (SmsStatus.DELIVERED, SmsStatus.FAILED)


class _CorePorts:
    """Ports du moteur d'incident : exécutent voix, SMS et interprétation hors du fil appelant."""

    def __init__(self, core: "MonitoringCore") -> None:
        self._core = core

    def speak(self, incident_id: str, text: str) -> None:
        self._core.executor.submit(self._speak, incident_id, text)

    def _speak(self, incident_id: str, text: str) -> None:
        try:
            result = self._core.voice.speak(text)
        except Exception as error:  # noqa: BLE001
            result = VoiceResult(False, type(error).__name__)
        self._core.engine.on_voice_result(incident_id, result.ok, result.error)

    def submit_sms(self, request: AlertRequest) -> None:
        self._core.executor.submit(self._submit, request)

    def _submit(self, request: AlertRequest) -> None:
        submission = self._core.gateway.submit_alert(request)
        self._core.engine.on_sms_result(request.incident_id, submission)

    def interpret_reply(self, incident_id: str, message_id: str, text: str) -> None:
        self._core.executor.submit(self._interpret, incident_id, message_id, text)

    def _interpret(self, incident_id: str, message_id: str, text: str) -> None:
        try:
            interpretation = self._core.interpreter.interpret(text)
        except InterpretationUnavailable:
            interpretation = None
        except Exception:  # noqa: BLE001 - sortie invalide ou service en panne
            interpretation = None
        self._core.engine.on_reply_interpreted(incident_id, message_id, interpretation)

    def log(self, message: str) -> None:
        self._core.log.add(message, self._core.current_video_time_s)

    def record(self, event: str, **fields: object) -> None:
        self._core.history.record(event, **fields)


class MonitoringCore:
    def __init__(
        self,
        config: AppConfig,
        clock: Clock,
        voice: Voice,
        gateway: SmsGateway,
        interpreter: ReplyInterpreter,
        history: HistoryStore,
        executor: Executor,
        source_kind: SourceKind,
        thresholds: DetectionThresholds | None = None,
        log: DetectionLog | None = None,
        masker: SensitiveMasker | None = None,
    ) -> None:
        self.clock, self.voice, self.gateway, self.interpreter = clock, voice, gateway, interpreter
        self.history, self.executor, self.source_kind = history, executor, source_kind
        self.masker = masker or default_masker()
        self.log = log or DetectionLog(clock, self.masker)
        self.config = config
        self._register_secrets(config)
        self.pipeline = DetectionPipeline(thresholds)
        self.pipeline.configure_zones(_rectangle(config.monitored_zone), [tuple(z) for z in config.lying_zones])
        self.engine = IncidentEngine(
            clock, _CorePorts(self), lambda: incident_settings_from(self.config),
            merge_window_s=self.pipeline.thresholds.merge_window_s,
        )
        self.current_video_time_s: float | None = None
        self.source_status = SourceStatus.ACTIVE
        self.last_posture: Posture = Posture.NO_PERSON
        self.availability = SmsAvailability.UNVERIFIED
        self._last_reply_poll_monotonic = float("-inf")
        self._last_status_poll_monotonic = float("-inf")
        self._source_alert_logged = False

    # -- configuration -------------------------------------------------------------------------------
    def apply_config(self, config: AppConfig) -> None:
        self.config = config
        self._register_secrets(config)
        self.pipeline.configure_zones(_rectangle(config.monitored_zone), [tuple(z) for z in config.lying_zones])
        self.log.add("Réglages appliqués")

    def _register_secrets(self, config: AppConfig) -> None:
        for value in (config.contact_number, config.twilio_number, config.alert_address):
            self.masker.register(value)

    # -- analyse des images ----------------------------------------------------------------------------
    def process(self, observation: FrameObservation, video_time_s: float | None = None) -> FrameAnalysis:
        self.current_video_time_s = video_time_s
        analysis = self.pipeline.process(observation)
        self.engine.note_person_count(len(analysis.persons))
        self.last_posture = _display_posture(analysis)
        for event in analysis.events:
            self.log.add(event.detail or event.kind.value, video_time_s)
            self.engine.handle_event(event)
        self.engine.tick()
        return analysis

    def set_source_status(self, status: SourceStatus) -> None:
        if status is self.source_status:
            return
        self.source_status = status
        self.engine.set_camera_available(status is not SourceStatus.LOST)
        if status is SourceStatus.ENDED:
            self.log.add("Fin du fichier vidéo", self.current_video_time_s)
        elif status is SourceStatus.LOST:
            self.last_posture = Posture.INDETERMINATE
            if self.engine.view() is None:
                self.log.add("Surveillance indisponible : caméra inactive")

    def tick(self) -> None:
        """À appeler régulièrement, indépendamment de la cadence d'analyse."""
        self.engine.tick()

    # -- arrière-plan : réponses et statuts ------------------------------------------------------------
    def poll_background(self, reply_interval_s: float | None = None) -> None:
        """Lit les réponses et rafraîchit le statut du SMS. Les appels sortent du fil appelant."""
        now = self.clock.monotonic()
        interval = reply_interval_s if reply_interval_s is not None else self.config.reply_poll_interval_s
        if now - self._last_reply_poll_monotonic >= interval:
            self._last_reply_poll_monotonic = now
            self.executor.submit(self._poll_replies)
        if now - self._last_status_poll_monotonic >= interval:
            self._last_status_poll_monotonic = now
            self.executor.submit(self._poll_sms_status)

    def _poll_replies(self) -> None:
        view = self.engine.view()
        if view is None or view.state is not IncidentState.HANDLED:
            return
        incident = next(i for i in self.engine.incidents() if i.incident_id == view.incident_id)
        since = incident.sms_requested_wall
        if since is None:
            return
        try:
            for sms in self.gateway.fetch_replies(since):
                self.engine.handle_incoming_sms(sms)
        except Exception as error:  # noqa: BLE001
            self.log.add(f"Lecture des réponses indisponible : {type(error).__name__}")

    def _poll_sms_status(self) -> None:
        for incident in self.engine.incidents():
            if incident.provider_ref and incident.sms_status not in _FINAL_SMS_STATUSES:
                try:
                    status = self.gateway.get_status(incident.provider_ref)
                except Exception:  # noqa: BLE001
                    continue
                self.engine.on_sms_status(incident.incident_id, status)

    def verify_sms_availability(self) -> SmsAvailability:
        try:
            self.availability = self.gateway.check_availability()
        except Exception:  # noqa: BLE001
            self.availability = SmsAvailability.UNAVAILABLE
        return self.availability

    # -- affichage ---------------------------------------------------------------------------------------
    def status_line(self, camera_label_override: str | None = None) -> str:
        if camera_label_override is not None:
            camera = camera_label_override
        elif self.source_status is SourceStatus.LOST:
            camera = "Caméra inactive"
        elif self.source_status is SourceStatus.ENDED:
            camera = "Lecture terminée"
        elif self.source_kind is SourceKind.WEBCAM:
            camera = "Caméra active"
        else:
            camera = "Lecture MP4 · mode test"
        parts = [camera, self.availability.value]
        if not self.voice.is_available():
            parts.append("Voix indisponible")
        return " · ".join(parts)


def _rectangle(values: list[float]) -> tuple[float, float, float, float] | None:
    return tuple(values) if len(values) == 4 else None  # type: ignore[return-value]


def _display_posture(analysis: FrameAnalysis) -> Posture:
    if not analysis.persons:
        return Posture.NO_PERSON
    postures = {person.posture for person in analysis.persons}
    return next(p for p in POSTURE_DISPLAY_PRIORITY if p in postures)
