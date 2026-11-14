"""Vérification de l'installation : `python -m faaall_alarm --check`."""

from __future__ import annotations

import platform
import sys

from .config import DEFAULT_MODEL_PATH
from .voice import EspeakVoice


def run_installation_check() -> int:
    problems = 0

    def report(name: str, ok: bool, detail: str) -> None:
        nonlocal problems
        problems += 0 if ok else 1
        print(f"[{'OK' if ok else 'KO'}] {name} : {detail}")

    report("Python", True, platform.python_version())
    try:
        import cv2

        report("OpenCV", True, cv2.__version__)
    except ImportError as error:
        report("OpenCV", False, str(error))
    try:
        import mediapipe

        report("MediaPipe", True, mediapipe.__version__)
    except ImportError as error:
        report("MediaPipe", False, str(error))
    report("Modèle de pose", DEFAULT_MODEL_PATH.is_file(), str(DEFAULT_MODEL_PATH))
    try:
        import tkinter

        report("Tkinter", True, f"Tk {tkinter.TkVersion}")
    except ImportError as error:
        report("Tkinter", False, str(error))
    voice = EspeakVoice()
    wav = voice.synthesize_wav("Test.") if voice.is_available() else b""
    report("eSpeak NG (synthèse hors ligne)", bool(wav), "synthèse WAV réussie" if wav else "introuvable")
    sys.stdout.flush()
    return 1 if problems else 0
