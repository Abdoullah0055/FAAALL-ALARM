"""Synthèse vocale française locale avec eSpeak NG, hors ligne et non neuronale."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

_CANDIDATE_PATHS = (
    r"C:\Program Files\eSpeak NG\espeak-ng.exe",
    r"C:\Program Files (x86)\eSpeak NG\espeak-ng.exe",
)
_SPEAK_TIMEOUT_S = 30.0


@dataclass(frozen=True)
class VoiceResult:
    ok: bool
    error: str = ""


class Voice(Protocol):
    def is_available(self) -> bool:
        ...

    def speak(self, text: str) -> VoiceResult:
        """Bloquant : l'appelant l'exécute hors du fil de l'interface et du décompte."""


def find_espeak(configured_path: str = "") -> Path | None:
    """Cherche espeak-ng.exe : chemin configuré, PATH puis dossiers d'installation habituels."""
    candidates = [configured_path] if configured_path else []
    on_path = shutil.which("espeak-ng")
    candidates += [on_path] if on_path else []
    candidates += list(_CANDIDATE_PATHS)
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    return None


class EspeakVoice:
    def __init__(self, executable: str = "", speed_wpm: int = 150, voice: str = "fr", volume_percent: int = 100) -> None:
        self._executable = find_espeak(executable)
        self._speed_wpm = speed_wpm
        self._voice = voice
        self._volume_percent = volume_percent

    def is_available(self) -> bool:
        return self._executable is not None

    def _command(self, text: str, extra: tuple[str, ...] = ()) -> list[str]:
        assert self._executable is not None
        return [str(self._executable), "-v", self._voice, "-s", str(self._speed_wpm), "-a", str(self._volume_percent),
                *extra, "--", text]

    def speak(self, text: str) -> VoiceResult:
        if self._executable is None:
            return VoiceResult(False, "eSpeak NG introuvable")
        try:
            completed = subprocess.run(
                self._command(text), capture_output=True, timeout=_SPEAK_TIMEOUT_S, check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            return VoiceResult(False, type(error).__name__)
        if completed.returncode != 0:
            return VoiceResult(False, f"eSpeak NG a retourné le code {completed.returncode}")
        return VoiceResult(True)

    def synthesize_wav(self, text: str) -> bytes:
        """Synthèse vers la sortie standard (WAV), sans son : vérifie le moteur hors ligne."""
        if self._executable is None:
            return b""
        completed = subprocess.run(
            self._command(text, ("--stdout",)), capture_output=True, timeout=_SPEAK_TIMEOUT_S, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
        )
        return completed.stdout if completed.returncode == 0 else b""


class UnavailableVoice:
    """Voix absente : l'interface affiche « Voix indisponible » et le décompte continue."""

    def is_available(self) -> bool:
        return False

    def speak(self, text: str) -> VoiceResult:
        return VoiceResult(False, "voix désactivée")
