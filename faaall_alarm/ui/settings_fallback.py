"""Page Réglages de secours, utilisée tant que la page d'Abdoullah n'est pas reliée (voir docs/RACCORDEMENT.md)."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

from ..settings_services import SettingsServices

_FIELDS = (
    ("countdown_seconds", "Décompte (secondes)"),
    ("monitored_first_name", "Prénom de la personne surveillée"),
    ("alert_address", "Adresse d'intervention"),
    ("contact_name", "Nom du proche"),
    ("contact_number", "Numéro du proche (+15145550123)"),
    ("twilio_number", "Numéro Twilio"),
)


def open_fallback_settings(parent: tk.Misc, services: SettingsServices) -> None:
    window = tk.Toplevel(parent)
    window.title("Réglages")
    window.transient(parent)
    config = services.read().to_dict()
    variables: dict[str, tk.StringVar] = {}
    errors: dict[str, ttk.Label] = {}
    frame = ttk.Frame(window, padding=12)
    frame.grid(sticky="nsew")
    ttk.Label(frame, text="Page Réglages provisoire (la page complète n'est pas encore reliée).").grid(
        row=0, column=0, columnspan=2, sticky="w", pady=(0, 8)
    )
    for row, (name, label) in enumerate(_FIELDS, start=1):
        variables[name] = tk.StringVar(value=str(config[name]))
        ttk.Label(frame, text=label).grid(row=row * 2 - 1, column=0, sticky="w")
        ttk.Entry(frame, textvariable=variables[name], width=36).grid(row=row * 2 - 1, column=1, padx=(8, 0))
        errors[name] = ttk.Label(frame, foreground="#b00020")
        errors[name].grid(row=row * 2, column=1, sticky="w", padx=(8, 0))
    status = ttk.Label(frame)
    status.grid(row=len(_FIELDS) * 2 + 2, column=0, columnspan=2, sticky="w", pady=(6, 0))

    def collect() -> dict[str, object]:
        values: dict[str, object] = {name: variable.get().strip() for name, variable in variables.items()}
        try:
            values["countdown_seconds"] = float(str(values["countdown_seconds"]).replace(",", "."))
        except ValueError:
            values["countdown_seconds"] = -1.0  # rejeté par la validation avec un message lisible
        return values

    def show_issues(issues) -> None:
        for label in errors.values():
            label.configure(text="")
        for issue in issues:
            if issue.field in errors:
                errors[issue.field].configure(text=issue.message)
        status.configure(text="Corriger les champs signalés." if issues else "")

    def apply() -> None:
        result = services.apply(collect())
        show_issues(result.issues)
        if result.ok:
            status.configure(text="Réglages appliqués.")

    def test_sound() -> None:
        result = services.test_voice()
        status.configure(text="Voix prononcée." if result.ok else "Voix indisponible.")

    buttons = ttk.Frame(frame)
    buttons.grid(row=len(_FIELDS) * 2 + 3, column=0, columnspan=2, sticky="e", pady=(10, 0))
    ttk.Button(buttons, text="Tester le son", command=test_sound).pack(side="left", padx=4)
    ttk.Button(buttons, text="Appliquer", command=apply).pack(side="left", padx=4)
    ttk.Button(buttons, text="Fermer", command=window.destroy).pack(side="left", padx=4)
