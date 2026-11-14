"""Implémentation de `SettingsPort` (page Réglages du lot 2) au-dessus de la configuration du lot 1.

La page manipule un `Settings` (zones en pourcentages entiers, une seule zone de repos). L'application
garde un `AppConfig` (zones normalisées de 0 à 1, plusieurs zones de repos possibles) enregistré dans
`config.local.json`. Ce backend traduit dans les deux sens, valide avec les deux jeux de règles et
applique la configuration au moteur en cours.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import cv2

from .config import TELEGRAM_MODE, AppConfig, apply_config, read_config, validate_config
from .core import MonitoringCore
from .model import SourceKind
from .settings import FULL_FRAME, LocalSettings, Settings
from .voice import find_espeak

PREVIEW_SIZE = (450, 253)  # largeur, hauteur : le cadre sur lequel s'appliquent les zones en %
_PAGE_FIELD = {  # champ AppConfig -> champ de la page, pour afficher l'erreur au bon endroit
    "camera_index": "camera_index", "monitored_zone": "zone", "lying_zones": "rest_zone",
    "espeak_path": "voice_path", "voice_volume": "volume", "countdown_seconds": "countdown_seconds",
    "sms_mode": "sms_mode", "sms_live_confirmed": "sms_mode", "monitored_first_name": "first_name",
    "alert_address": "address", "contact_name": "contact_name", "contact_number": "recipient",
    "twilio_number": "sender", "voice_message": "voice_path",
}


def _to_percent(zone: list[float]) -> tuple[int, int, int, int]:
    return tuple(round(value * 100) for value in zone)  # type: ignore[return-value]


def _to_fraction(zone: tuple[int, int, int, int]) -> list[float]:
    return [value / 100 for value in zone]


def settings_from_config(config: AppConfig) -> Settings:
    espeak = find_espeak(config.espeak_path)
    return Settings(
        camera_index=config.camera_index,
        zone=_to_percent(config.monitored_zone) if config.monitored_zone else FULL_FRAME,
        rest_zone=_to_percent(config.lying_zones[0]) if config.lying_zones else None,
        voice_path=str(espeak) if espeak else (config.espeak_path or "espeak-ng"),
        volume=config.voice_volume,
        countdown_seconds=round(config.countdown_seconds),
        sms_mode=config.sms_mode,
        first_name=config.monitored_first_name, address=config.alert_address,
        contact_name=config.contact_name, recipient=config.contact_number, sender=config.twilio_number,
    )


def config_values_from_settings(settings: Settings) -> dict[str, object]:
    """Valeurs à fusionner dans `AppConfig`. Choisir « Réel » dans la page vaut activation explicite."""
    return {
        "camera_index": settings.camera_index,
        "monitored_zone": [] if settings.zone == FULL_FRAME else _to_fraction(settings.zone),
        "lying_zones": [_to_fraction(settings.rest_zone)] if settings.rest_zone else [],
        "espeak_path": "" if settings.voice_path == "espeak-ng" else settings.voice_path,
        "voice_volume": settings.volume,
        "countdown_seconds": float(settings.countdown_seconds),
        "sms_mode": str(settings.sms_mode),
        "sms_live_confirmed": str(settings.sms_mode) != "simulated",
        "monitored_first_name": settings.first_name, "alert_address": settings.address,
        "contact_name": settings.contact_name, "contact_number": settings.recipient,
        "twilio_number": settings.sender,
    }


class AppSettingsBackend:
    def __init__(
        self, core: MonitoringCore, services: LocalSettings, source_kind: SourceKind,
        latest_image: Callable[[], object | None], config_path: Path | None = None,
    ) -> None:
        self._core = core
        self._services = services  # règles de validation et test sonore du lot 2
        self._source_kind = source_kind
        self._latest_image = latest_image
        self._config_path = config_path

    @property
    def allows_telegram_test(self) -> bool:
        return self._source_kind is SourceKind.WEBCAM

    @property
    def telegram_pairing_allowed(self) -> bool:
        return self._core.engine.view() is None

    def load(self) -> Settings:
        return settings_from_config(self._core.config)

    def validate(self, settings: Settings) -> dict[str, str]:
        errors = self._services.validate(settings)
        if errors:
            return errors
        base = read_config(self._config_path).to_dict()
        for issue in validate_config({**base, **config_values_from_settings(settings)}):
            errors.setdefault(_PAGE_FIELD.get(issue.field, issue.field), issue.message)
        return errors

    def apply(self, settings: Settings) -> None:
        if settings.sms_mode == TELEGRAM_MODE and not self.telegram_pairing_allowed:
            raise ValueError("Attendez la fin de l’incident avant d’activer Telegram.")
        if self.validate(settings):
            raise ValueError("Vérifiez les réglages indiqués.")
        result = apply_config(config_values_from_settings(settings), self._config_path, self._core.apply_config)
        if not result.ok:
            raise ValueError(result.issues[0].message)

    def preview(self, camera_index: int) -> bytes | None:
        """PNG 450 × 253 du cadre de la caméra, ou None (aperçu simulé) hors webcam."""
        if self._source_kind is not SourceKind.WEBCAM:
            return None
        frame = None
        image = self._latest_image()
        if image is not None and camera_index == self._core.config.camera_index:
            frame = image  # la capture en cours utilise déjà cette caméra : ne pas la rouvrir
        else:
            capture = cv2.VideoCapture(camera_index, cv2.CAP_DSHOW)
            try:
                ok, frame = capture.read() if capture.isOpened() else (False, None)
            finally:
                capture.release()
            if not ok or frame is None:
                raise OSError("Caméra indisponible")
        encoded, png = cv2.imencode(".png", cv2.resize(frame, PREVIEW_SIZE))
        if not encoded:
            raise OSError("Aperçu indisponible")
        return png.tobytes()

    def test_sound(self, settings: Settings) -> bool:
        """Test sonore seul : ne déclenche aucun incident."""
        return self._services.test_sound(settings)
