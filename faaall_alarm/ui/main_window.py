"""Écran de surveillance : caméra à gauche (~70 %), posture, alerte et logs à droite. Aucun en-tête global."""

from __future__ import annotations

import json
import tkinter as tk
from tkinter import messagebox, ttk

import cv2
from PIL import Image, ImageTk

from ..app import MonitoringApp
from ..settings import STATE_DIR
from ..settings_page import open_settings
from ..incident import IncidentState, IncidentView
from ..logbook import format_entry
from ..model import Posture
from .overlay import draw_skeleton
from .viewmodel import (
    alert_cause_line, format_countdown, posture_caption, reply_text, sms_status_text, voice_line,
)

REFRESH_MS = 33
COPY_CONFIRMATION_MS = 3000
FONT = "Segoe UI"
ALERT_COLOR = "#b00020"


class MainWindow:
    def __init__(self, root: tk.Tk, app: MonitoringApp) -> None:
        self.root, self.app, self.core = root, app, app.core
        self._settings_window: tk.Toplevel | None = None
        self._skeleton_var = tk.BooleanVar(value=app.config.show_skeleton)
        self._photo: ImageTk.PhotoImage | None = None
        self._log_count = 0
        self._alert_visible = False
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self._refresh()

    # -- construction -------------------------------------------------------------------------------------
    def _build(self) -> None:
        self.root.title("FAAALL-ALARM")
        self.root.geometry("1280x760")
        self.root.minsize(960, 600)
        self.root.columnconfigure(0, weight=7, uniform="colonnes")
        self.root.columnconfigure(1, weight=3, uniform="colonnes")
        self.root.rowconfigure(0, weight=1)
        self._build_left_column()
        self._build_right_column()

    def _build_left_column(self) -> None:
        left = ttk.Frame(self.root, padding=(12, 8, 6, 8))
        left.grid(row=0, column=0, sticky="nsew")
        left.columnconfigure(0, weight=1)
        left.rowconfigure(1, weight=1)
        # Texte simple aligné à gauche avec la vidéo : pas de fond, de bordure ni de badge.
        self.status_label = ttk.Label(left, text="", font=(FONT, 10), anchor="w")
        self.status_label.grid(row=0, column=0, sticky="ew", pady=(0, 4))
        # Un Canvas garde une taille indépendante de l'image affichée (un Label grandirait avec elle).
        self.video_canvas = tk.Canvas(left, background="#101010", highlightthickness=0, width=320, height=240)
        self.video_canvas.grid(row=1, column=0, sticky="nsew")
        controls = ttk.Frame(left)
        controls.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        ttk.Checkbutton(controls, text="Squelette", variable=self._skeleton_var).pack(side="left", padx=(0, 12))
        ttk.Button(controls, text="Historique", command=self.show_history).pack(side="left", padx=(0, 12))
        ttk.Button(controls, text="Réglages", command=self.open_settings).pack(side="left")

    def _build_right_column(self) -> None:
        right = ttk.Frame(self.root, padding=(6, 8, 12, 8))
        right.grid(row=0, column=1, sticky="nsew")
        right.columnconfigure(0, weight=1)
        right.rowconfigure(2, weight=1)

        posture_frame = ttk.Frame(right)
        posture_frame.grid(row=0, column=0, sticky="ew")
        self.posture_label = ttk.Label(posture_frame, text=Posture.NO_PERSON.value, font=(FONT, 28, "bold"))
        self.posture_label.pack(anchor="w")
        self.posture_caption = ttk.Label(posture_frame, text="", font=(FONT, 10))
        self.posture_caption.pack(anchor="w")

        self.alert_frame = tk.Frame(right, padx=10, pady=10, highlightthickness=1, highlightbackground="#c8c8c8")
        self.alert_title = ttk.Label(self.alert_frame, text="Chute possible", font=(FONT, 20, "bold"), foreground=ALERT_COLOR)
        self.alert_cause = ttk.Label(self.alert_frame, font=(FONT, 11))
        self.countdown_label = ttk.Label(self.alert_frame, font=(FONT, 36, "bold"))
        self.cancel_button = ttk.Button(self.alert_frame, text="Annuler l'alerte", command=self.cancel_alert)
        self.sms_label = ttk.Label(self.alert_frame, font=(FONT, 11), wraplength=330, justify="left")
        self.reply_label = ttk.Label(self.alert_frame, font=(FONT, 11), wraplength=330, justify="left")
        self.voice_label = ttk.Label(self.alert_frame, font=(FONT, 10), foreground=ALERT_COLOR)
        self.close_button = ttk.Button(self.alert_frame, text="Clore l'incident", command=self.close_incident)
        for widget in (self.alert_title, self.alert_cause, self.countdown_label, self.cancel_button,
                       self.sms_label, self.reply_label, self.voice_label, self.close_button):
            widget.pack(anchor="w", fill="x", pady=2)

        self.log_frame = ttk.Frame(right)
        self.log_frame.grid(row=2, column=0, sticky="nsew", pady=(8, 0))
        self.log_frame.columnconfigure(0, weight=1)
        self.log_frame.rowconfigure(1, weight=1)
        header = ttk.Frame(self.log_frame)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        ttk.Label(header, text="Logs de détection", font=(FONT, 11, "bold")).pack(side="left")
        self.copy_label = ttk.Label(header, text="", font=(FONT, 9))
        self.copy_label.pack(side="right", padx=(0, 6))
        ttk.Button(header, text="Tout copier", command=self.copy_all_logs).pack(side="right", padx=6)
        self.log_text = tk.Text(self.log_frame, height=6, wrap="word", state="disabled", font=("Consolas", 9),
                                borderwidth=1, relief="solid")
        scrollbar = ttk.Scrollbar(self.log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        self.log_text.grid(row=1, column=0, sticky="nsew")
        scrollbar.grid(row=1, column=1, sticky="ns")

    # -- actions ----------------------------------------------------------------------------------------------
    def cancel_alert(self) -> None:
        self.core.engine.cancel()

    def close_incident(self) -> None:
        self.core.engine.close()

    def copy_all_logs(self) -> None:
        """Copie tout le journal de la session, y compris les lignes hors de la zone visible."""
        text = self.core.log.export_text()
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.root.update()
        self.copy_label.configure(text=f"Copié · {len(self.core.log)} lignes")
        self.root.after(COPY_CONFIRMATION_MS, lambda: self.copy_label.configure(text=""))

    def open_settings(self) -> None:
        """Page Réglages du lot 2, branchée sur la configuration et le moteur en cours."""
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.lift()
            return
        self._settings_window = open_settings(
            self.root, self.app.settings_backend, self.app.settings_services, STATE_DIR,
        )

    def show_history(self) -> None:
        window = tk.Toplevel(self.root)
        window.title("Historique local")
        window.geometry("720x420")
        text = tk.Text(window, wrap="none", font=("Consolas", 9))
        text.pack(fill="both", expand=True)

        def fill() -> None:
            text.configure(state="normal")
            text.delete("1.0", "end")
            records = self.core.history.read()
            for record in records:
                text.insert("end", json.dumps(record, ensure_ascii=False) + "\n")
            if not records:
                text.insert("end", "Aucun événement enregistré.")
            text.configure(state="disabled")

        def clear() -> None:
            if messagebox.askyesno("Effacer l'historique", "Effacer définitivement l'historique local ?", parent=window):
                self.core.history.clear()
                fill()

        ttk.Button(window, text="Effacer l'historique", command=clear).pack(pady=6)
        fill()

    def close(self) -> None:
        self.app.stop()
        self.root.destroy()

    # -- rafraîchissement ---------------------------------------------------------------------------------------
    def _refresh(self) -> None:
        self.core.tick()
        self.core.poll_background()
        if self.app.director is not None:
            self.app.director.tick()
        self._update_video()
        self.status_label.configure(text=self.core.status_line())
        state = self.app.latest()
        persons = state.persons if state else []
        self.posture_label.configure(text=self.core.last_posture.value)
        self.posture_caption.configure(text=posture_caption(persons))
        self._update_alert(self.core.engine.view())
        self._append_new_logs()
        self.root.after(REFRESH_MS, self._refresh)

    def _update_video(self) -> None:
        state = self.app.latest()
        canvas = self.video_canvas
        width, height = max(canvas.winfo_width(), 2), max(canvas.winfo_height(), 2)
        canvas.delete("all")
        if state is None or state.image_bgr is None:
            canvas.create_text(width // 2, height // 2, fill="#cccccc", font=(FONT, 14), text="En attente d'images…")
            return
        image = state.image_bgr
        if self._skeleton_var.get():
            image = draw_skeleton(image, state.persons)
        scale = min(width / image.shape[1], height / image.shape[0])
        resized = cv2.resize(image, (max(int(image.shape[1] * scale), 1), max(int(image.shape[0] * scale), 1)))
        self._photo = ImageTk.PhotoImage(Image.fromarray(cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)))
        canvas.create_image(0, 0, image=self._photo, anchor="nw")  # aligné en haut à gauche, comme le statut
        if self.core.status_line().startswith("Caméra inactive"):
            canvas.create_text(width // 2, height // 2, fill="#ffffff", font=(FONT, 18, "bold"), text="Caméra inactive")

    def _update_alert(self, view: IncidentView | None) -> None:
        if view is None:
            if self._alert_visible:  # le panneau se replie : les logs récupèrent l'espace libéré
                self.alert_frame.grid_remove()
                self.root.nametowidget(self.log_frame.winfo_parent()).rowconfigure(1, weight=0, uniform="")
                self.root.nametowidget(self.log_frame.winfo_parent()).rowconfigure(2, weight=1, uniform="")
                self._alert_visible = False
            return
        if not self._alert_visible:
            right = self.root.nametowidget(self.log_frame.winfo_parent())
            self.alert_frame.grid(row=1, column=0, sticky="nsew", pady=(8, 0))
            right.rowconfigure(1, weight=2, uniform="droite")  # alerte ~2/3, logs ~1/3 pendant l'alerte
            right.rowconfigure(2, weight=1, uniform="droite")
            self._alert_visible = True
        self.alert_cause.configure(text=alert_cause_line(view))
        if view.state is IncidentState.COUNTDOWN and view.remaining_s is not None:
            self.countdown_label.configure(text=format_countdown(view.remaining_s), font=(FONT, 36, "bold"))
        else:
            self.countdown_label.configure(text="Alerte traitée", font=(FONT, 18, "bold"))
        self._toggle(self.cancel_button, view.can_cancel)
        self._toggle(self.close_button, view.can_close)
        self.sms_label.configure(text=sms_status_text(view))
        self.reply_label.configure(text=reply_text(view) if view.state is IncidentState.HANDLED else "")
        self.voice_label.configure(text=voice_line(view) + (" · Caméra inactive" if not view.camera_available else ""))

    @staticmethod
    def _toggle(widget: ttk.Button, visible: bool) -> None:
        if visible and not widget.winfo_ismapped():
            widget.pack(anchor="w", fill="x", pady=2)
        elif not visible and widget.winfo_ismapped():
            widget.pack_forget()

    def _append_new_logs(self) -> None:
        entries = self.core.log.entries(self._log_count)
        if not entries:
            return
        at_bottom = self.log_text.yview()[1] >= 0.999
        self.log_text.configure(state="normal")
        for entry in entries:
            self.log_text.insert("end", format_entry(entry) + "\n")
        self.log_text.configure(state="disabled")
        self._log_count += len(entries)
        if at_bottom:
            self.log_text.see("end")
