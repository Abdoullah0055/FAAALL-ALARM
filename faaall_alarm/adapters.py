"""Adaptateurs entre les modules du lot 2 (SMS Twilio, OpenAI) et les protocoles du moteur d'incident.

Le moteur parle `SmsGateway` et `ReplyInterpreter` (voir sms_contract.py). Le lot 2 fournit
`SmsService` (sms.py) et `OpenAI` / `interpret_simulated` (replies.py), avec ses propres types.
Ces classes traduisent les types et les fuseaux horaires sans ajouter de logique métier.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

from .model import FallCause
from .replies import Care, Interpretation, OpenAI, interpret_simulated
from .sms import Alert, Delivery, Incoming, SmsService
from .sms_contract import (
    AlertRequest, IncomingSms, InterpretationUnavailable, ReplyInterpretation, ReplyState, SmsAvailability,
    SmsStatus, SmsSubmission,
)

_DELIVERY_TO_STATUS = {
    Delivery.SIMULATED: SmsStatus.SIMULATED,
    Delivery.PENDING: SmsStatus.PENDING,
    Delivery.DELIVERED: SmsStatus.DELIVERED,
    Delivery.FAILED: SmsStatus.FAILED,
    Delivery.UNKNOWN: SmsStatus.UNKNOWN,
}
_CARE_TO_STATE = {
    Care.ON_WAY: ReplyState.EN_ROUTE,
    Care.CONFIRMED: ReplyState.CONFIRMED,
    Care.UNAVAILABLE: ReplyState.UNAVAILABLE,
    Care.REVIEW: ReplyState.TO_CHECK,
}
_AVAILABILITY = {
    "SMS simulé": SmsAvailability.SIMULATED,
    "SMS opérationnel": SmsAvailability.OPERATIONAL,
    "SMS indisponible": SmsAvailability.UNAVAILABLE,
}


def _local_naive(moment: datetime) -> datetime:
    """Heure locale sans fuseau : celle que manipule le moteur (horloge civile)."""
    return moment.astimezone().replace(tzinfo=None)


class SmsServiceGateway:
    """`SmsGateway` du moteur, adossé à `SmsService`. Les appels sont bloquants : fil de travail uniquement."""

    def __init__(self, service: SmsService) -> None:
        self.service = service
        self._incident_by_ref: dict[str, str] = {}

    def check_availability(self) -> SmsAvailability:
        return _AVAILABILITY.get(self.service.check(), SmsAvailability.UNVERIFIED)

    def submit_alert(self, request: AlertRequest) -> SmsSubmission:
        alert = Alert(
            incident_id=request.incident_id,
            occurred_at=request.occurred_at.astimezone(),  # le lot 2 exige un fuseau explicite
            address=request.address,
            first_name=request.first_name or "",
            name_reliable=request.first_name is not None,
            collision=request.cause is FallCause.COLLISION,
        )
        try:
            result = self.service.submit(alert)
        except (ValueError, sqlite3.Error, OSError) as error:
            # Échec local avant tout appel réseau : l'incident reste ouvert, l'alerte locale continue.
            return SmsSubmission(SmsStatus.FAILED, None, f"soumission impossible ({type(error).__name__})",
                                 self.service.now())
        if result.provider_sid:
            self._incident_by_ref[result.provider_sid] = request.incident_id
        return SmsSubmission(_DELIVERY_TO_STATUS[result.state], result.provider_sid, None,
                             _local_naive(result.attempted_at))

    def get_status(self, provider_ref: str) -> SmsStatus:
        incident_id = self._incident_by_ref.get(provider_ref)
        refreshed = self.service.refresh(incident_id) if incident_id else None
        return _DELIVERY_TO_STATUS[refreshed.state] if refreshed else SmsStatus.UNKNOWN

    def fetch_replies(self, since: datetime) -> list[IncomingSms]:
        messages: list[Incoming] = self.service.poll_replies(since.astimezone())
        return [
            IncomingSms(m.sid, m.sender, m.recipient, m.body, _local_naive(m.received_at)) for m in messages
        ]

    def acknowledge_reply(self, message_id: str) -> None:
        """À appeler après traitement : un message acquitté n'est plus représenté après une reconnexion."""
        self.service.acknowledge_reply(message_id)

    def inject_reply(self, body: str, sender: str, recipient: str, received_at: datetime | None = None,
                     message_id: str | None = None) -> None:
        """Canal simulé uniquement. `sender` et `recipient` viennent de la configuration de ce service."""
        moment = received_at.astimezone() if received_at else self.service.now()
        self.service.inject_reply(body, message_id or f"sim-{uuid.uuid4()}", moment)


class ReplyInterpreterAdapter:
    """`ReplyInterpreter` du moteur : règles hors ligne ou OpenAI, selon le mode SMS autorisé."""

    def __init__(self, openai: OpenAI | None, redact: tuple[str, ...] = ()) -> None:
        self._openai = openai
        self._redact = redact

    def interpret(self, text: str) -> ReplyInterpretation:
        result: Interpretation = (
            self._openai.interpret(text, redact=self._redact) if self._openai
            else interpret_simulated(text)
        )
        if result.service_unavailable:
            raise InterpretationUnavailable("OpenAI indisponible")
        return ReplyInterpretation(_CARE_TO_STATE[result.state], result.eta_minutes)
