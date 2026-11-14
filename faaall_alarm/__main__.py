import argparse
import sys
import tempfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Réglages et simulation FAAALL-ALARM")
    parser.add_argument("--demo", action="store_true", help="Jouer la recette SMS hors ligne dans le terminal")
    args = parser.parse_args()
    if args.demo:
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
        return

    import tkinter as tk

    from .settings import STATE_DIR, LocalSettings, configuration
    from .settings_page import SettingsPage

    settings = LocalSettings(STATE_DIR / "reglages.json", configuration())
    root = tk.Tk()
    SettingsPage(root, settings, settings, STATE_DIR)
    root.mainloop()


if __name__ == "__main__":
    main()
