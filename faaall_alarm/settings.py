"""Réglages du lot 2 et contrat de la page Windows."""

import json
import os
import subprocess
from tempfile import NamedTemporaryFile
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values

from .replies import DEFAULT_MODEL
from .sms import E164, Mode, SmsConfig


STATE_DIR = Path(os.environ.get("LOCALAPPDATA") or Path.home()) / "FAAALL-ALARM"
VOICE_TEST = "Une chute possible a été détectée. Si tout va bien, appuyez sur Annuler l’alerte."
FULL_FRAME = (0, 0, 100, 100)
Rect = tuple[int, int, int, int]


@dataclass(frozen=True)
class Settings:
    camera_index: int = 0
    zone: Rect = FULL_FRAME
    rest_zone: Rect | None = None
    voice_path: str = "espeak-ng"
    volume: int = 100
    countdown_seconds: int = 10
    sms_mode: str = Mode.SIMULATED
    first_name: str = field(default="", repr=False)
    address: str = field(default="", repr=False)
    contact_name: str = field(default="", repr=False)
    recipient: str = field(default="", repr=False)
    sender: str = field(default="", repr=False)


class SettingsPort(Protocol):
    def load(self) -> Settings: ...
    def validate(self, settings: Settings) -> dict[str, str]: ...
    def apply(self, settings: Settings) -> None: ...
    def preview(self, camera_index: int) -> bytes | None: ...
    def test_sound(self, settings: Settings) -> bool: ...


def configuration() -> dict:
    # Aucun export vers le processus ni affichage des valeurs locales.
    return {**dotenv_values(".env", interpolate=False), **os.environ}


class LocalSettings:
    """Persistance autonome et aperçu factice en attendant la capture d’Adam."""

    def __init__(self, path: Path, config: dict):
        self.path = path
        self.config = config

    def load(self) -> Settings:
        defaults = Settings(
            sms_mode=self.config.get("FAAALL_SMS_MODE") or Mode.SIMULATED,
            first_name=self.config.get("FAAALL_MONITORED_FIRST_NAME") or "",
            address=self.config.get("FAAALL_ALERT_ADDRESS") or "",
            recipient=self.config.get("FAAALL_SMS_TO_NUMBER") or "",
            sender=self.config.get("TWILIO_FROM_NUMBER") or "",
            contact_name=self.config.get("FAAALL_CONTACT_NAME") or "",
        )
        if not self.path.exists():
            return defaults
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            settings = replace(defaults, **data)
            settings = replace(settings, zone=tuple(settings.zone),
                               rest_zone=tuple(settings.rest_zone) if settings.rest_zone else None)
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("Le fichier de réglages locaux est illisible.") from error
        return settings

    def sms(self, settings: Settings) -> SmsConfig:
        return SmsConfig(Mode(settings.sms_mode), settings.sender, settings.recipient,
                         self.config.get("TWILIO_ACCOUNT_SID") or "",
                         self.config.get("TWILIO_AUTH_TOKEN") or "")

    def validate(self, settings: Settings) -> dict[str, str]:
        errors = {}
        if type(settings.camera_index) is not int or settings.camera_index < 0:
            errors["camera_index"] = "Choisissez un index de caméra positif ou nul."
        if not _valid_rect(settings.zone):
            errors["zone"] = "La zone doit avoir une surface dans le cadre de 0 à 100 %."
        if settings.rest_zone is not None:
            if not _valid_rect(settings.rest_zone) or (
                "zone" not in errors and not _inside(settings.rest_zone, settings.zone)
            ):
                errors["rest_zone"] = "Placez la zone de repos à l’intérieur de la zone surveillée."
        if not settings.voice_path.strip():
            errors["voice_path"] = "Indiquez le chemin de l’exécutable eSpeak NG."
        if type(settings.volume) is not int or not 0 <= settings.volume <= 100:
            errors["volume"] = "Choisissez un volume de 0 à 100 %."
        if type(settings.countdown_seconds) is not int or not 1 <= settings.countdown_seconds <= 300:
            errors["countdown_seconds"] = "Choisissez un délai de 1 à 300 secondes."
        for name in ("sender", "recipient"):
            number = getattr(settings, name)
            if number and not E164.fullmatch(number):
                errors[name] = "Le numéro doit avoir le format international, par exemple +15145550101."
        if settings.sms_mode not in {Mode.SIMULATED, Mode.LIVE}:
            errors["sms_mode"] = "Choisissez Simulé ou Réel."
        elif settings.sms_mode == Mode.LIVE:
            if not self.sms(settings).valid():
                errors["sms_mode"] = "Vérifiez les deux numéros distincts et les identifiants Twilio locaux."
            if not settings.address.strip():
                errors["address"] = "Indiquez l’adresse d’intervention."
            if not settings.first_name.strip():
                errors["first_name"] = "Indiquez le prénom de la personne surveillée."
            if not settings.contact_name.strip():
                errors["contact_name"] = "Indiquez le nom du proche."
        return errors

    def apply(self, settings: Settings) -> None:
        if self.validate(settings):
            raise ValueError("Vérifiez les réglages indiqués.")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=self.path.parent, suffix=".tmp", mode="w",
                                encoding="utf-8", delete=False) as file:
            pending = Path(file.name)
            try:
                json.dump(asdict(settings), file, ensure_ascii=False, indent=2)
                file.close()
                pending.replace(self.path)
            finally:
                file.close()
                pending.unlink(missing_ok=True)

    def preview(self, camera_index: int) -> bytes | None:
        return None

    def test_sound(self, settings: Settings) -> bool:
        try:
            result = subprocess.run(
                [settings.voice_path, "-v", "fr", "-a", str(settings.volume), VOICE_TEST],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=12,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            return False
        return result.returncode == 0

    def model(self) -> tuple[str, str]:
        return (self.config.get("OPENROUTER_API_KEY") or "",
                self.config.get("OPENROUTER_MODEL") or DEFAULT_MODEL)


def _valid_rect(rect: Rect) -> bool:
    return (isinstance(rect, tuple) and len(rect) == 4 and all(type(n) is int for n in rect)
            and 0 <= rect[0] < rect[2] <= 100 and 0 <= rect[1] < rect[3] <= 100)


def _inside(inner: Rect, outer: Rect) -> bool:
    return outer[0] <= inner[0] < inner[2] <= outer[2] and outer[1] <= inner[1] < inner[3] <= outer[3]
