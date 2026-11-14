"""Point de raccordement avec les modules du lot d'Abdoullah.

Chaque composant externe est cherché par nom de module. S'il manque ou s'il n'est pas configuré,
une simulation prend le relais et l'écran l'indique. Voir docs/RACCORDEMENT.md pour les signatures.
"""

from __future__ import annotations

import importlib
from datetime import datetime
from typing import Callable

from .config import SMS_MODE_LIVE, AppConfig
from .model import SourceKind
from .sms_contract import (
    AlertRequest, InterpretationUnavailable, IncomingSms, ReplyInterpretation, ReplyInterpreter,
    SmsAvailability, SmsGateway, SmsStatus, SmsSubmission,
)
from .sms_simulated import SimulatedReplyInterpreter, SimulatedSmsGateway
from .clock import Clock

REAL_GATEWAY_FACTORY = ("faaall_alarm.sms", "create_gateway")  # create_gateway(config) -> SmsGateway
# create_simulated_gateway(config, clock) -> SmsGateway exposant aussi inject_reply(body, sender, recipient, ...)
SIMULATED_GATEWAY_FACTORY = ("faaall_alarm.sms", "create_simulated_gateway")
REAL_INTERPRETER_FACTORY = ("faaall_alarm.replies", "create_interpreter")  # create_interpreter(config) -> ReplyInterpreter
SETTINGS_PAGE_ENTRY = ("faaall_alarm.settings_page", "open_settings_page")  # open_settings_page(parent, services)


def _load(entry: tuple[str, str]):
    module_name, attribute = entry
    try:
        return getattr(importlib.import_module(module_name), attribute)
    except (ImportError, AttributeError):
        return None


def real_sms_allowed(config: AppConfig, source_kind: SourceKind) -> bool:
    """Frontière d'envoi : le réel exige le mode live, l'activation explicite et une vraie webcam.

    La lecture MP4, la démo et les tests restent simulés même avec des identifiants Twilio valides.
    """
    return (
        config.sms_mode == SMS_MODE_LIVE and config.sms_live_confirmed and source_kind is SourceKind.WEBCAM
    )


class SendGuard:
    """Enveloppe un adaptateur réel et refuse toute soumission quand le réel n'est pas autorisé."""

    def __init__(self, real: SmsGateway, is_allowed: Callable[[], bool], clock: Clock) -> None:
        self._real = real
        self._is_allowed = is_allowed
        self._clock = clock

    def check_availability(self) -> SmsAvailability:
        return self._real.check_availability() if self._is_allowed() else SmsAvailability.UNAVAILABLE

    def submit_alert(self, request: AlertRequest) -> SmsSubmission:
        if not self._is_allowed():
            return SmsSubmission(SmsStatus.FAILED, None, "envoi réel non autorisé dans ce mode", self._clock.wall())
        return self._real.submit_alert(request)

    def get_status(self, provider_ref: str) -> SmsStatus:
        return self._real.get_status(provider_ref)

    def fetch_replies(self, since: datetime) -> list[IncomingSms]:
        return self._real.fetch_replies(since)


class UnavailableGateway:
    """Canal réel demandé mais module absent ou refusé : l'écran affiche « SMS indisponible »."""

    def __init__(self, clock: Clock, reason: str) -> None:
        self._clock = clock
        self.reason = reason

    def check_availability(self) -> SmsAvailability:
        return SmsAvailability.UNAVAILABLE

    def submit_alert(self, request: AlertRequest) -> SmsSubmission:
        return SmsSubmission(SmsStatus.FAILED, None, self.reason, self._clock.wall())

    def get_status(self, provider_ref: str) -> SmsStatus:
        return SmsStatus.UNKNOWN

    def fetch_replies(self, since: datetime) -> list[IncomingSms]:
        return []


class UnavailableInterpreter:
    def interpret(self, text: str) -> ReplyInterpretation:
        raise InterpretationUnavailable("aucun interpréteur configuré")


def build_sms_gateway(config: AppConfig, source_kind: SourceKind, clock: Clock) -> SmsGateway:
    if not real_sms_allowed(config, source_kind):
        simulated_factory = _load(SIMULATED_GATEWAY_FACTORY)
        if simulated_factory is not None:
            try:
                return simulated_factory(config, clock)
            except Exception:  # noqa: BLE001 - repli sur le substitut local
                pass
        return SimulatedSmsGateway(clock)
    factory = _load(REAL_GATEWAY_FACTORY)
    if factory is None:
        return UnavailableGateway(clock, "module SMS réel non relié")
    try:
        real = factory(config)
    except Exception as error:  # noqa: BLE001 - le module externe peut échouer de multiples façons
        return UnavailableGateway(clock, f"module SMS réel refusé ({type(error).__name__})")
    return SendGuard(real, lambda: real_sms_allowed(config, source_kind), clock)


def build_interpreter(config: AppConfig, source_kind: SourceKind) -> ReplyInterpreter:
    """Interpréteur réel seulement en mode SMS réel autorisé ; sinon règles simulées, sans clé API."""
    if not real_sms_allowed(config, source_kind):
        return SimulatedReplyInterpreter()
    factory = _load(REAL_INTERPRETER_FACTORY)
    if factory is None:
        return UnavailableInterpreter()
    try:
        return factory(config)
    except Exception:  # noqa: BLE001
        return UnavailableInterpreter()


def load_settings_page() -> Callable | None:
    """Page Réglages d'Abdoullah, ou None tant qu'elle n'est pas disponible."""
    return _load(SETTINGS_PAGE_ENTRY)
