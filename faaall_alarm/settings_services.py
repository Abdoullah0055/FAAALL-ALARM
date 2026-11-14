"""Fonctions offertes à la page Réglages : lire, valider, appliquer et tester (contrat : docs/RACCORDEMENT.md)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .config import (
    DEFAULT_CONFIG_PATH, ApplyResult, AppConfig, ConfigIssue, apply_config, read_config, validate_config,
)
from .core import MonitoringCore
from .sms_contract import AlertRequest, SmsAvailability, SmsSubmission
from .voice import EspeakVoice, VoiceResult
from .model import FallCause


class SettingsServices:
    """Façade passée à `open_settings_page(parent, services)`. Aucune méthode ne déclenche d'incident."""

    def __init__(self, core: MonitoringCore | None = None, config_path: Path | None = None) -> None:
        self._core = core
        self._config_path = config_path or DEFAULT_CONFIG_PATH

    def read(self) -> AppConfig:
        """Configuration courante : config.local.json, puis variables d'environnement, puis défauts."""
        return read_config(self._config_path)

    def validate(self, values: Mapping[str, Any]) -> list[ConfigIssue]:
        """Liste vide si les valeurs sont valides ; chaque erreur nomme le champ concerné."""
        return validate_config(values)

    def apply(self, values: Mapping[str, Any]) -> ApplyResult:
        """Valide, enregistre puis applique à l'application en cours. Rien n'est écrit en cas d'erreur."""
        return apply_config(values, self._config_path, self._core.apply_config if self._core else None)

    def test_voice(self) -> VoiceResult:
        """Test sonore : agit uniquement sur l'audio, sans incident ni SMS."""
        config = self.read()
        voice = self._core.voice if self._core else EspeakVoice(config.espeak_path, config.voice_speed_wpm)
        if not voice.is_available():
            return VoiceResult(False, "Voix indisponible")
        return voice.speak(config.voice_message)

    def test_sms_simulated(self) -> SmsSubmission:
        """Message de test sur la passerelle simulée : aucun envoi réel, aucun incident."""
        from .clock import SystemClock
        from .sms_simulated import SimulatedSmsGateway

        config = self.read()
        clock = self._core.clock if self._core else SystemClock()
        request = AlertRequest(
            "TEST", clock.wall(), FallCause.UNDETERMINED, config.monitored_first_name or None,
            config.alert_address, config.contact_number, config.twilio_number,
        )
        return SimulatedSmsGateway(clock).submit_alert(request)

    def sms_availability(self) -> SmsAvailability:
        return self._core.verify_sms_availability() if self._core else SmsAvailability.UNVERIFIED

    def clear_history(self) -> None:
        if self._core:
            self._core.history.clear()
