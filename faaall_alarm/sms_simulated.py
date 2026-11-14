"""Substituts simulés du module SMS et de l'interpréteur de réponses, sans réseau ni clé API.

Ils servent à la démo, aux tests et tant que les modules d'Abdoullah ne sont pas reliés.
`SimulatedReplyInterpreter` est une règle simple de remplacement : elle n'est pas l'interpréteur
OpenRouter et ne couvre pas toute la langue.
"""

from __future__ import annotations

import itertools
import re
import threading
from datetime import datetime

from .clock import Clock, SystemClock
from .sms_contract import (
    AlertRequest, IncomingSms, InterpretationUnavailable, ReplyInterpretation, ReplyState,
    SmsAvailability, SmsStatus, SmsSubmission,
)


class SimulatedSmsGateway:
    """Adaptateur SMS simulé : aucun envoi réel, réponses injectées à la main."""

    def __init__(self, clock: Clock | None = None, forced_result: SmsStatus | None = None) -> None:
        self._clock = clock or SystemClock()
        self._forced_result = forced_result  # FAILED ou UNKNOWN pour simuler un refus ou un réseau incertain
        self._counter = itertools.count(1)
        self._inbox: list[IncomingSms] = []
        self._delivered_message_ids: set[str] = set()
        self._lock = threading.Lock()
        self.submitted_requests: list[AlertRequest] = []

    def check_availability(self) -> SmsAvailability:
        return SmsAvailability.SIMULATED

    def submit_alert(self, request: AlertRequest) -> SmsSubmission:
        with self._lock:
            self.submitted_requests.append(request)
        now = self._clock.wall()
        if self._forced_result in (SmsStatus.FAILED, SmsStatus.UNKNOWN):
            error = "refus simulé" if self._forced_result is SmsStatus.FAILED else "délai réseau simulé"
            return SmsSubmission(self._forced_result, None, error, now)
        return SmsSubmission(SmsStatus.SIMULATED, f"SIM-{next(self._counter):04d}", None, now)

    def get_status(self, provider_ref: str) -> SmsStatus:
        return SmsStatus.SIMULATED

    def fetch_replies(self, since: datetime) -> list[IncomingSms]:
        with self._lock:
            ready = [
                sms for sms in self._inbox
                if sms.received_at >= since and sms.message_id not in self._delivered_message_ids
            ]
            self._delivered_message_ids.update(sms.message_id for sms in ready)
        return ready

    def inject_reply(
        self, body: str, sender: str, recipient: str, received_at: datetime | None = None,
        message_id: str | None = None,
    ) -> IncomingSms:
        sms = IncomingSms(
            message_id=message_id or f"SIM-IN-{next(self._counter):04d}", sender=sender,
            recipient=recipient, body=body, received_at=received_at or self._clock.wall(),
        )
        with self._lock:
            self._inbox.append(sms)
        return sms


_DELAY = re.compile(r"(?:environ|dans|d'ici|autour de)?\s*(\d{1,3})\s*(min(?:ute)?s?|h(?:eure)?s?)\b", re.IGNORECASE)
_UNAVAILABLE = re.compile(
    r"ne peux pas|peux pas|impossible|pas disponible|indisponible|ne pourrai pas|pas possible|je ne viens pas",
    re.IGNORECASE,
)
_EN_ROUTE = re.compile(r"en route|j['’ ]arrive|je viens|j['’ ]y vais|je pars", re.IGNORECASE)
_CONFIRMED = re.compile(r"je m['’ ]en occupe|pris en charge|je suis sur place|j['’ ]ai appel", re.IGNORECASE)


class SimulatedReplyInterpreter:
    """Interprétation par règles : un état parmi quatre et un délai seulement s'il est explicite."""

    def __init__(self, available: bool = True) -> None:
        self.available = available

    def interpret(self, text: str) -> ReplyInterpretation:
        if not self.available:
            raise InterpretationUnavailable("interpréteur simulé coupé")
        cleaned = text.strip()
        if _UNAVAILABLE.search(cleaned):
            return ReplyInterpretation(ReplyState.UNAVAILABLE)
        if "?" in cleaned:
            return ReplyInterpretation(ReplyState.TO_CHECK)
        if _EN_ROUTE.search(cleaned):
            return ReplyInterpretation(ReplyState.EN_ROUTE, self._announced_delay(cleaned))
        if _CONFIRMED.search(cleaned):
            return ReplyInterpretation(ReplyState.CONFIRMED, self._announced_delay(cleaned))
        return ReplyInterpretation(ReplyState.TO_CHECK)

    @staticmethod
    def _announced_delay(text: str) -> int | None:
        match = _DELAY.search(text)
        if match is None:
            return None
        value = int(match.group(1))
        return value * 60 if match.group(2).lower().startswith("h") else value
