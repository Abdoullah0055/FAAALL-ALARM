"""Point d'entrée : `python -m faaall_alarm` (voir README.md)."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import DEFAULT_MODEL_PATH
from .simulation.scenarios import SCENARIOS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="faaall_alarm", description="FAAALL-ALARM : surveillance de chute locale.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--video", type=Path, help="Rejoue un fichier MP4 (mode test, SMS toujours simulé).")
    source.add_argument("--demo", nargs="?", const="chute_debout", metavar="SCENARIO",
                        help=f"Démo reproductible sans clé API. Scénarios : {', '.join(SCENARIOS)}.")
    parser.add_argument("--camera", type=int, help="Numéro de la webcam (0 par défaut).")
    parser.add_argument("--countdown", type=float, help="Décompte en secondes (10 par défaut).")
    parser.add_argument("--reply-delay", type=float, default=6.0,
                        help="Démo : délai avant la réponse simulée du proche, en secondes.")
    parser.add_argument("--no-reply", action="store_true", help="Démo : aucune réponse simulée.")
    parser.add_argument("--cancel-after", type=float, help="Démo : annule l'alerte après N secondes.")
    parser.add_argument("--headless", action="store_true",
                        help="Démo : rejoue le scénario sans fenêtre et affiche la chronologie.")
    parser.add_argument("--sms-recipe", action="store_true",
                        help="Rejoue la recette SMS du lot 2 dans le terminal (service simulé, hors ligne).")
    parser.add_argument("--check", action="store_true", help="Vérifie l'installation (Python, OpenCV, MediaPipe, eSpeak NG).")
    parser.add_argument("--voice-test", action="store_true", help="Prononce le message d'alerte, sans incident.")
    parser.add_argument("--list-scenarios", action="store_true", help="Liste les scénarios de démonstration.")
    return parser


def _enable_windows_dpi_awareness() -> None:
    """Évite une fenêtre floue ou mal dimensionnée sur un écran à mise à l'échelle."""
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        pass


def run_sms_recipe() -> int:
    """Recette SMS du lot 2 : alerte fictive, SMS simulé, réponse du proche, sans moteur de détection."""
    import tempfile

    from .demo import Demo
    from .replies import DEMO_REPLY

    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    with tempfile.TemporaryDirectory() as directory:
        demo = Demo.create(Path(directory) / "demo.sqlite3")
        print("Incident fictif · échéance simulée")
        print(demo.alert.message())
        print(f"SMS · {demo.send().state}")
        print(DEMO_REPLY)
        _, response = demo.reply()
        print(response.label())
    return 0


def run_check() -> int:
    from .check import run_installation_check

    return run_installation_check()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_scenarios:
        from .simulation.scenarios import get_scenario

        for name in SCENARIOS:
            scenario = get_scenario(name)
            print(f"{name:26} {scenario.description}")
        return 0
    if args.check:
        return run_check()
    if args.sms_recipe:
        return run_sms_recipe()
    if args.voice_test:
        from .config import read_config
        from .voice import EspeakVoice

        config = read_config()
        voice = EspeakVoice(config.espeak_path, config.voice_speed_wpm)
        result = voice.speak(config.voice_message) if voice.is_available() else None
        print("Voix prononcée." if result and result.ok else "Voix indisponible (eSpeak NG introuvable ou en échec).")
        return 0 if result and result.ok else 1
    if args.headless:
        from .simulation.cli import run_headless_demo

        return run_headless_demo(args.demo or "chute_debout", args.countdown, None if args.no_reply else args.reply_delay,
                                 args.cancel_after)
    if args.video and not DEFAULT_MODEL_PATH.is_file() or (not args.demo and not DEFAULT_MODEL_PATH.is_file()):
        print(f"Modèle de pose introuvable : {DEFAULT_MODEL_PATH}\nLance d'abord : .\\scripts\\install.ps1", file=sys.stderr)
        return 2

    import tkinter as tk

    from .app import AppOptions, MonitoringApp
    from .ui.main_window import MainWindow

    options = AppOptions(
        video_path=args.video, camera_index=args.camera, demo_scenario=args.demo,
        demo_reply_delay_s=None if args.no_reply else args.reply_delay, demo_cancel_after_s=args.cancel_after,
        countdown_seconds=args.countdown,
    )
    try:
        app = MonitoringApp(options)
    except (FileNotFoundError, OSError, KeyError) as error:
        print(f"Impossible de démarrer : {error}", file=sys.stderr)
        return 2
    _enable_windows_dpi_awareness()
    root = tk.Tk()
    MainWindow(root, app)
    app.start()
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
