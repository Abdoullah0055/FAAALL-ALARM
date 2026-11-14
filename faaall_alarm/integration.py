"""Frontière d'envoi réel et construction des services SMS et d'interprétation du lot 2.

Le SMS réel exige le mode `live`, l'activation explicite et une vraie webcam. La lecture d'un MP4, la
démo et les tests restent simulés, même avec des identifiants Twilio valides : la règle est appliquée ici
et une seconde fois par `SmsService` (qui reçoit la source réelle).
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from .adapters import ReplyInterpreterAdapter, SmsServiceGateway
from .clock import Clock
from .config import SMS_MODE_LIVE, AppConfig
from .model import SourceKind
from .replies import OpenAI
from .settings import STATE_DIR, LocalSettings, configuration
from .sms import Mode, SmsConfig, SmsService, Source
from .sms_contract import (
    AlertRequest, IncomingSms, ReplyInterpreter, SmsAvailability, SmsGateway, SmsStatus, SmsSubmission,
)

SMS_DATABASE = "sms.sqlite3"  # même base que la page Réglages : une soumission par incident et destinataire
_SOURCES = {
    SourceKind.WEBCAM: Source.CAMERA,
    SourceKind.VIDEO_FILE: Source.MP4,
    SourceKind.SIMULATION: Source.DEMO,
}


def real_sms_allowed(config: AppConfig, source_kind: SourceKind) -> bool:
    return (
        config.sms_mode == SMS_MODE_LIVE and config.sms_live_confirmed and source_kind is SourceKind.WEBCAM
    )


def sms_config(config: AppConfig, environment: dict | None = None) -> SmsConfig:
    """Configuration du service : numéros des réglages, secrets Twilio lus dans l'environnement local."""
    secrets = environment if environment is not None else configuration()
    return SmsConfig(
        Mode.LIVE if config.sms_mode == SMS_MODE_LIVE else Mode.SIMULATED,
        config.twilio_number, config.contact_number,
        secrets.get("TWILIO_ACCOUNT_SID") or "", secrets.get("TWILIO_AUTH_TOKEN") or "",
    )


class SendGuard:
    """Refuse toute soumission quand le mode réel n'est plus autorisé (second verrou autour du service)."""

    def __init__(self, gateway: SmsServiceGateway, is_allowed, clock: Clock) -> None:
        self._gateway = gateway
        self._is_allowed = is_allowed
        self._clock = clock

    def check_availability(self) -> SmsAvailability:
        return self._gateway.check_availability() if self._is_allowed() else SmsAvailability.UNAVAILABLE

    def submit_alert(self, request: AlertRequest) -> SmsSubmission:
        if not self._is_allowed():
            return SmsSubmission(SmsStatus.FAILED, None, "envoi réel non autorisé dans ce mode", self._clock.wall())
        return self._gateway.submit_alert(request)

    def get_status(self, provider_ref: str) -> SmsStatus:
        return self._gateway.get_status(provider_ref)

    def fetch_replies(self, since: datetime) -> list[IncomingSms]:
        return self._gateway.fetch_replies(since)

    def acknowledge_reply(self, message_id: str) -> None:
        self._gateway.acknowledge_reply(message_id)


def build_sms_gateway(
    config: AppConfig, source_kind: SourceKind, clock: Clock, state_dir: Path | None = None,
    environment: dict | None = None, transport=None, source_override: Source | None = None, now=None,
) -> SmsGateway:
    """Passerelle du moteur. Hors webcam ou hors mode réel confirmé, le service est simulé."""
    allowed = real_sms_allowed(config, source_kind)
    effective = config if allowed else AppConfig(**{**config.to_dict(), "sms_mode": "simulated"})
    options = {key: value for key, value in (("transport", transport), ("now", now)) if value is not None}
    service = SmsService(
        (state_dir or STATE_DIR) / SMS_DATABASE, sms_config(effective, environment),
        source_override or _SOURCES[source_kind], **options,
    )
    gateway = SmsServiceGateway(service)
    return SendGuard(gateway, lambda: real_sms_allowed(config, source_kind), clock) if allowed else gateway


def build_interpreter(config: AppConfig, source_kind: SourceKind, environment: dict | None = None,
                      transport=None) -> ReplyInterpreter:
    """OpenAI seulement avec le SMS réel autorisé ; sinon règles hors ligne, sans clé API."""
    if not real_sms_allowed(config, source_kind):
        return ReplyInterpreterAdapter(None)
    secrets = environment if environment is not None else configuration()
    settings = LocalSettings(STATE_DIR / "reglages.json", secrets)
    key, model = settings.model()
    redact = (config.monitored_first_name, config.contact_name, config.alert_address,
              config.contact_number, config.twilio_number)
    options = {"transport": transport} if transport is not None else {}
    return ReplyInterpreterAdapter(OpenAI(key, model, **options), redact)
