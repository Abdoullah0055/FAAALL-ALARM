"""Moteur d'incident : décompte, annulation, clôture, réarmement et décision d'envoyer le SMS.

Toutes les entrées publiques sont sérialisées par un verrou : la décision d'envoyer à l'échéance
et une annulation concurrente ne peuvent pas s'entrelacer. Les appels lents (voix, SMS,
interprétation) passent par des ports asynchrones et rappellent le moteur à la fin.
"""

from __future__ import annotations

import secrets
import threading
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Callable, Protocol

from .clock import Clock
from .config import AppConfig
from .model import DetectionEvent, EventKind, FallCause
from .sms_contract import (
    AlertRequest, IncomingSms, ReplyInterpretation, ReplyState, SmsStatus, SmsSubmission,
)

HEADLINE = "Chute possible"


class IncidentState(str, Enum):
    COUNTDOWN = "countdown"
    HANDLED = "handled"  # alerte traitée : SMS soumis ou simulé, incident toujours ouvert
    CANCELLED = "cancelled"
    CLOSED = "closed"


class ReplyRouting(str, Enum):
    ATTACHED = "attached"
    NEEDS_REVIEW = "needs_review"
    DUPLICATE = "duplicate"
    IGNORED_SENDER = "ignored_sender"
    IGNORED_OLDER = "ignored_older"


@dataclass(frozen=True)
class IncidentSettings:
    countdown_seconds: float = 10.0
    first_name: str = ""
    address: str = ""
    contact_name: str = ""
    contact_number: str = ""
    twilio_number: str = ""
    voice_enabled: bool = True
    voice_message: str = ""
    reply_window_s: float = 1800.0  # une réponse doit arriver dans les 30 min suivant l'envoi
    reply_ambiguity_window_s: float = 86400.0  # autre soumission dans les 24 h : rattachement ambigu


def incident_settings_from(config: AppConfig) -> IncidentSettings:
    return IncidentSettings(
        countdown_seconds=config.countdown_seconds, first_name=config.monitored_first_name,
        address=config.alert_address, contact_name=config.contact_name,
        contact_number=config.contact_number, twilio_number=config.twilio_number,
        voice_enabled=config.voice_enabled, voice_message=config.voice_message,
    )


class IncidentPorts(Protocol):
    def speak(self, incident_id: str, text: str) -> None:
        """Prononce le message sans bloquer. Le résultat revient par `on_voice_result`."""

    def submit_sms(self, request: AlertRequest) -> None:
        """Soumet sans bloquer. Le résultat revient par `on_sms_result`."""

    def interpret_reply(self, incident_id: str, message_id: str, text: str) -> None:
        """Interprète sans bloquer. Le résultat revient par `on_reply_interpreted`."""

    def log(self, message: str) -> None:
        """Ajoute une ligne au journal de détection (déjà sans donnée sensible)."""

    def record(self, event: str, **fields: object) -> None:
        """Enregistre un événement dans l'historique local. `durable=True` force l'écriture sur disque."""


@dataclass
class ReplyRecord:
    state: ReplyState | None = None
    delay_minutes: int | None = None
    received_at: datetime | None = None
    needs_review: bool = False
    analysing: bool = False


@dataclass
class Incident:
    incident_id: str
    created_wall: datetime
    created_monotonic: float
    cause: FallCause
    first_name: str | None
    deadline_monotonic: float
    countdown_seconds: float
    fallen_tracks: set[int] = field(default_factory=set)
    recovered_tracks: set[int] = field(default_factory=set)
    state: IncidentState = IncidentState.COUNTDOWN
    voice_ok: bool | None = None
    sms_status: SmsStatus | None = None
    sms_requested_wall: datetime | None = None
    sms_error: str | None = None
    provider_ref: str | None = None
    reply: ReplyRecord = field(default_factory=ReplyRecord)
    pending_replies: dict[str, datetime] = field(default_factory=dict)
    outcome: str = ""
    settings: IncidentSettings = field(default_factory=IncidentSettings)  # figés à l'ouverture


@dataclass(frozen=True)
class IncidentView:
    """Photographie de l'alerte active pour l'interface. N'existe que pendant une alerte active."""

    incident_id: str
    state: IncidentState
    headline: str
    cause: FallCause
    remaining_s: float | None
    can_cancel: bool
    can_close: bool
    voice_ok: bool | None
    sms_status: SmsStatus | None
    reply: ReplyRecord
    contact_name: str
    camera_available: bool
    created_wall: datetime


def _digits(number: str) -> str:
    return "".join(character for character in number if character.isdigit())


class IncidentEngine:
    def __init__(
        self, clock: Clock, ports: IncidentPorts, settings: Callable[[], IncidentSettings],
        merge_window_s: float = 6.0,
    ) -> None:
        self._clock = clock
        self._ports = ports
        self._settings = settings
        self._merge_window_s = merge_window_s
        self._lock = threading.RLock()
        self._incidents: list[Incident] = []
        self._suppressed_tracks: set[int] = set()
        self._suspected_tracks: set[int] = set()
        self._submitted_keys: set[tuple[str, str]] = set()
        self._seen_message_ids: set[str] = set()
        self._person_counts: deque[tuple[float, int]] = deque()
        self._camera_available = True
        self._sequence = 0

    # -- événements de détection --------------------------------------------------------------------
    def handle_event(self, event: DetectionEvent) -> None:
        with self._lock:
            if event.kind is EventKind.FALL_SUSPECTED:
                self._suspected_tracks.update(event.track_ids)
            elif event.kind is EventKind.SUSPICION_CLEARED:
                self._suspected_tracks.difference_update(event.track_ids)
            elif event.kind is EventKind.FALL_CONFIRMED:
                self._suspected_tracks.difference_update(event.track_ids)
                self._on_fall_confirmed(event)
            elif event.kind is EventKind.PERSON_RECOVERED:
                self._on_person_recovered(event)
            elif event.kind is EventKind.COLLISION_WITHOUT_FALL:
                self._ports.record("collision_sans_chute", tracks=len(event.track_ids))

    def note_person_count(self, count: int) -> None:
        """Nombre de personnes suivies sur l'image courante, pour décider si le prénom est attribuable."""
        with self._lock:
            now = self._clock.monotonic()
            self._person_counts.append((now, count))
            while self._person_counts and now - self._person_counts[0][0] > 10.0:
                self._person_counts.popleft()

    def set_camera_available(self, available: bool) -> None:
        with self._lock:
            if available == self._camera_available:
                return
            self._camera_available = available
            active = self._active()
            if not available:
                suffix = f" : l'incident {active.incident_id} continue" if active else ""
                self._ports.log(f"Caméra inactive{suffix}")
            else:
                self._ports.log("Caméra de nouveau active")
            self._ports.record("camera", available=available)

    # -- actions de l'utilisateur et du temps ---------------------------------------------------------
    def tick(self) -> None:
        """À appeler régulièrement : indépendant de la cadence d'analyse des images."""
        with self._lock:
            incident = self._active()
            if incident is None or incident.state is not IncidentState.COUNTDOWN:
                return
            if self._clock.monotonic() >= incident.deadline_monotonic:
                self._submit(incident)

    def cancel(self, reason: str = "annulation manuelle") -> bool:
        with self._lock:
            incident = self._active()
            if incident is None or incident.state is not IncidentState.COUNTDOWN:
                return False
            incident.state = IncidentState.CANCELLED
            incident.outcome = reason
            self._remember_unrecovered(incident)
            self._ports.log(f"Alerte annulée ({reason}) : aucun SMS envoyé · {incident.incident_id}")
            self._ports.record("incident_annule", incident=incident.incident_id, motif=reason)
            return True

    def close(self, reason: str = "clôture manuelle") -> bool:
        with self._lock:
            incident = self._active()
            if incident is None or incident.state is not IncidentState.HANDLED:
                return False
            self._finish(incident, reason)
            return True

    def close_cancelled(self) -> None:
        """Clôture manuelle d'un incident annulé (rien n'est affiché, utile aux tests et à l'historique)."""
        with self._lock:
            for incident in self._incidents:
                if incident.state is IncidentState.CANCELLED:
                    incident.state = IncidentState.CLOSED

    # -- retours des ports asynchrones ----------------------------------------------------------------
    def on_voice_result(self, incident_id: str, ok: bool, error: str = "") -> None:
        with self._lock:
            incident = self._find(incident_id)
            if incident is None:
                return
            incident.voice_ok = ok
            self._ports.log("Voix prononcée" if ok else f"Voix indisponible : {error or 'moteur absent'}")
            self._ports.record("voix", incident=incident_id, ok=ok)

    def on_sms_result(self, incident_id: str, submission: SmsSubmission) -> None:
        with self._lock:
            incident = self._find(incident_id)
            if incident is None:
                return
            incident.sms_status = submission.status
            incident.sms_error = submission.error
            incident.provider_ref = submission.provider_ref or incident.provider_ref
            messages = {
                SmsStatus.SIMULATED: "SMS simulé consigné (aucun envoi réel)",
                SmsStatus.PENDING: "SMS soumis au fournisseur, livraison à confirmer",
                SmsStatus.DELIVERED: "SMS livré",
                SmsStatus.FAILED: f"SMS en échec : {submission.error or 'refusé'} (l'alerte locale reste disponible)",
                SmsStatus.UNKNOWN: "Résultat réseau incertain : livraison inconnue, aucun renvoi automatique",
            }
            self._ports.log(messages[submission.status])
            self._ports.record("sms", incident=incident_id, statut=submission.status.value)

    def on_sms_status(self, incident_id: str, status: SmsStatus) -> None:
        with self._lock:
            incident = self._find(incident_id)
            if incident is None or incident.sms_status is SmsStatus.DELIVERED or incident.sms_status is status:
                return
            incident.sms_status = status
            self._ports.log(f"Statut du SMS : {status.value}")
            self._ports.record("sms", incident=incident_id, statut=status.value)

    def handle_incoming_sms(self, sms: IncomingSms) -> ReplyRouting:
        """Rattache une réponse à l'incident ouvert, avant tout appel à l'IA. Le texte n'est jamais journalisé."""
        with self._lock:
            if sms.message_id in self._seen_message_ids:
                return ReplyRouting.DUPLICATE
            self._seen_message_ids.add(sms.message_id)
            active_incident = self._active()
            settings = active_incident.settings if active_incident else self._settings()
            if _digits(sms.sender) != _digits(settings.contact_number) or (
                _digits(sms.recipient) != _digits(settings.twilio_number)
            ):
                self._ports.log("SMS reçu d'un expéditeur ou d'un numéro inattendu : ignoré")
                self._ports.record("sms_etranger_ignore")
                return ReplyRouting.IGNORED_SENDER
            incident = self._active()
            if incident is None or incident.state is not IncidentState.HANDLED or incident.sms_requested_wall is None:
                self._ports.log("Réponse reçue, à vérifier (aucun incident ouvert avec alerte soumise)")
                self._ports.record("reponse_a_verifier", motif="aucun incident ouvert")
                return ReplyRouting.NEEDS_REVIEW
            elapsed_s = (sms.received_at - incident.sms_requested_wall).total_seconds()
            if elapsed_s <= 0 or elapsed_s > settings.reply_window_s or self._ambiguous_with_other_alert(
                incident, sms, settings
            ):
                incident.reply.needs_review = True
                self._ports.log("Réponse reçue, à vérifier (rattachement ambigu à l'incident)")
                self._ports.record("reponse_a_verifier", incident=incident.incident_id, motif="ambigu")
                return ReplyRouting.NEEDS_REVIEW
            if incident.reply.received_at is not None and sms.received_at <= incident.reply.received_at:
                self._ports.log("Réponse plus ancienne que la mise à jour affichée : ignorée")
                return ReplyRouting.IGNORED_OLDER
            incident.pending_replies[sms.message_id] = sms.received_at
            incident.reply.analysing = True
            self._ports.log("Réponse du proche reçue, analyse en cours")
            self._ports.interpret_reply(incident.incident_id, sms.message_id, sms.body)
            return ReplyRouting.ATTACHED

    def on_reply_interpreted(
        self, incident_id: str, message_id: str, interpretation: ReplyInterpretation | None
    ) -> None:
        """`interpretation` vaut None quand l'analyse a échoué : l'écran affiche « à vérifier »."""
        with self._lock:
            incident = self._find(incident_id)
            received_at = incident.pending_replies.pop(message_id, None) if incident else None
            if incident is None or received_at is None:
                return
            incident.reply.analysing = bool(incident.pending_replies)
            if incident.reply.received_at is not None and received_at < incident.reply.received_at:
                self._ports.log("Interprétation d'une réponse plus ancienne : ignorée")
                return
            incident.reply.received_at = received_at
            if interpretation is None or interpretation.state is ReplyState.TO_CHECK:
                incident.reply.state, incident.reply.delay_minutes = None, None
                incident.reply.needs_review = True
                self._ports.log("Réponse reçue, à vérifier")
                self._ports.record("reponse", incident=incident_id, etat="à vérifier")
                return
            incident.reply.needs_review = False
            incident.reply.state = interpretation.state
            incident.reply.delay_minutes = interpretation.announced_delay_minutes
            delay = (
                f"délai annoncé : environ {interpretation.announced_delay_minutes} min"
                if interpretation.announced_delay_minutes is not None else "délai non précisé"
            )
            self._ports.log(f"Réponse du proche : {interpretation.state.value}, {delay}")
            self._ports.record(
                "reponse", incident=incident_id, etat=interpretation.state.value,
                delai_min=interpretation.announced_delay_minutes,
            )

    # -- lecture ------------------------------------------------------------------------------------------
    def view(self) -> IncidentView | None:
        with self._lock:
            incident = self._active()
            if incident is None:
                return None
            remaining = None
            if incident.state is IncidentState.COUNTDOWN:
                remaining = max(0.0, incident.deadline_monotonic - self._clock.monotonic())
            return IncidentView(
                incident_id=incident.incident_id, state=incident.state, headline=HEADLINE,
                cause=incident.cause, remaining_s=remaining,
                can_cancel=incident.state is IncidentState.COUNTDOWN,
                can_close=incident.state is IncidentState.HANDLED, voice_ok=incident.voice_ok,
                sms_status=incident.sms_status, reply=_copy_reply(incident.reply),
                contact_name=incident.settings.contact_name, camera_available=self._camera_available,
                created_wall=incident.created_wall,
            )

    def incidents(self) -> list[Incident]:
        with self._lock:
            return list(self._incidents)

    # -- interne ----------------------------------------------------------------------------------------------
    def _active(self) -> Incident | None:
        for incident in reversed(self._incidents):
            if incident.state in (IncidentState.COUNTDOWN, IncidentState.HANDLED):
                return incident
        return None

    def _find(self, incident_id: str) -> Incident | None:
        return next((i for i in self._incidents if i.incident_id == incident_id), None)

    def _on_fall_confirmed(self, event: DetectionEvent) -> None:
        tracks = set(event.track_ids)
        if tracks <= self._suppressed_tracks:
            self._ports.log("Même chute toujours au sol : aucun nouvel incident")
            return
        cause = event.cause or FallCause.UNDETERMINED
        active = self._active()
        if active is not None:
            self._merge_into(active, tracks, cause)
            return
        self._open_incident(tracks, cause)

    def _merge_into(self, incident: Incident, tracks: set[int], cause: FallCause) -> None:
        new_tracks = tracks - incident.fallen_tracks
        if not new_tracks:
            return
        incident.fallen_tracks |= new_tracks
        within_window = self._clock.monotonic() - incident.created_monotonic <= self._merge_window_s
        if cause is FallCause.COLLISION and incident.cause is not FallCause.COLLISION:
            incident.cause = cause
        elif incident.cause is FallCause.INDIVIDUAL and within_window:
            incident.cause = FallCause.UNDETERMINED  # deux personnes tombées sans contact établi
        self._ports.log(f"Chute rattachée à l'incident en cours : {incident.incident_id} (pas de nouvelle alerte)")
        self._ports.record("chute_rattachee", incident=incident.incident_id)

    def _open_incident(self, tracks: set[int], cause: FallCause) -> None:
        settings = self._settings()
        now = self._clock.monotonic()
        self._sequence += 1
        wall = self._clock.wall()
        incident = Incident(
            incident_id=f"INC-{self._sequence:03d}-{wall:%H}h{wall:%M}-{secrets.token_hex(3)}", created_wall=wall, created_monotonic=now,
            cause=cause, first_name=self._attributable_first_name(cause, settings),
            deadline_monotonic=now + settings.countdown_seconds, countdown_seconds=settings.countdown_seconds,
            fallen_tracks=set(tracks), settings=settings,
        )
        self._incidents.append(incident)
        self._ports.log(
            f"{HEADLINE} · {cause.value} · incident {incident.incident_id} · décompte {settings.countdown_seconds:g} s"
        )
        self._ports.record(
            "incident_ouvert", incident=incident.incident_id, cause=cause.value, decompte_s=settings.countdown_seconds,
        )
        if settings.voice_enabled:
            self._ports.speak(incident.incident_id, settings.voice_message)  # une seule fois, à l'ouverture
        else:
            incident.voice_ok = False

    def _attributable_first_name(self, cause: FallCause, settings: IncidentSettings) -> str | None:
        """Le prénom n'est utilisé que si une seule personne a été vue récemment et que la cause est claire."""
        crowded = any(count >= 2 for _, count in self._person_counts)
        if cause is FallCause.INDIVIDUAL and not crowded and settings.first_name:
            return settings.first_name
        return None

    def _submit(self, incident: Incident) -> None:
        settings = incident.settings  # numéros et décompte figés pendant la durée de l'incident
        key = (incident.incident_id, settings.contact_number)
        if key in self._submitted_keys:
            return
        self._submitted_keys.add(key)
        incident.state = IncidentState.HANDLED
        incident.sms_status = SmsStatus.PENDING
        incident.sms_requested_wall = self._clock.wall()
        self._ports.record("sms_intention", durable=True, incident=incident.incident_id)
        self._ports.log("Échéance du décompte : demande d'envoi du SMS")
        request = AlertRequest(
            incident_id=incident.incident_id, occurred_at=incident.created_wall, cause=incident.cause,
            first_name=incident.first_name, address=settings.address,
            recipient_number=settings.contact_number, sender_number=settings.twilio_number,
        )
        try:
            self._ports.submit_sms(request)
        except Exception as error:  # noqa: BLE001 - le port est un point d'extension externe
            incident.sms_status = SmsStatus.FAILED
            incident.sms_error = type(error).__name__
            self._ports.log(f"SMS en échec avant l'appel réseau : {type(error).__name__}")

    def _on_person_recovered(self, event: DetectionEvent) -> None:
        tracks = set(event.track_ids)
        self._suppressed_tracks -= tracks
        incident = self._active()
        if incident is None:
            return
        incident.recovered_tracks |= tracks & incident.fallen_tracks
        if incident.fallen_tracks <= incident.recovered_tracks and not self._suspected_tracks:
            if incident.state is IncidentState.COUNTDOWN:
                self.cancel("reprise de posture fiable")
            else:
                self._finish(incident, "reprise de posture fiable")

    def _finish(self, incident: Incident, reason: str) -> None:
        incident.state = IncidentState.CLOSED
        incident.outcome = reason
        self._remember_unrecovered(incident)
        self._ports.log(f"Incident {incident.incident_id} clos ({reason})")
        self._ports.record("incident_clos", incident=incident.incident_id, motif=reason)

    def _remember_unrecovered(self, incident: Incident) -> None:
        """Une personne encore au sol ne peut pas déclencher de nouvel incident avant sa reprise."""
        self._suppressed_tracks |= incident.fallen_tracks - incident.recovered_tracks

    def _ambiguous_with_other_alert(self, incident: Incident, sms: IncomingSms, settings: IncidentSettings) -> bool:
        """Un SMS ordinaire n'indique pas son alerte d'origine : une autre soumission dans les 24 h qui
        précèdent la réponse rend le rattachement ambigu. Une conversation déjà rattachée à cet incident
        (première réponse acceptée) reste la sienne."""
        if incident.reply.received_at is not None:
            return False
        for other in self._incidents:
            if other is incident or other.sms_requested_wall is None:
                continue
            age = (sms.received_at - other.sms_requested_wall).total_seconds()
            if 0 <= age <= settings.reply_ambiguity_window_s:
                return True
        return False


def _copy_reply(reply: ReplyRecord) -> ReplyRecord:
    return ReplyRecord(reply.state, reply.delay_minutes, reply.received_at, reply.needs_review, reply.analysing)
