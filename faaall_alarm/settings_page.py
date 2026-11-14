"""Page Réglages intégrable comme fenêtre secondaire Tk."""

import base64
import sqlite3
import tkinter as tk
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from math import ceil
from pathlib import Path
from tkinter import messagebox, ttk
from time import monotonic
from uuid import uuid4

from .demo import Demo
from .http import ServiceError
from .replies import DEMO_REPLY, OpenAI, ReplyTarget, associate, newer
from .settings import FULL_FRAME, LocalSettings, Settings, SettingsPort
from .sms import (Alert, Delivery, Mode, POLL_SECONDS, REPLY_AMBIGUITY_WINDOW,
                  REPLY_WINDOW, SmsService, Source, Submission, twilio_diagnostic)


def test_result_label(submission: Submission) -> str:
    label = f"SMS de test · {submission.state}"
    if submission.diagnostic:
        label += f"\n{submission.diagnostic}"
    return label


def open_settings(parent: tk.Misc, backend: SettingsPort, services: LocalSettings,
                  state_dir: Path) -> tk.Toplevel:
    window = tk.Toplevel(parent)
    SettingsPage(window, backend, services, state_dir)
    return window


class SettingsPage:
    def __init__(self, window: tk.Tk | tk.Toplevel, backend: SettingsPort,
                 services: LocalSettings, state_dir: Path):
        self.window = window
        self.backend = backend
        self.services = services
        self.state_dir = state_dir
        self.executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="reglages")
        self.closed = False
        self.demo: Demo | None = None
        self.deadline = 0
        self.demo_running = False
        self.real_test_running = False
        self.preview_image = None
        window.title("Réglages · FAAALL-ALARM")
        window.minsize(590, 590)
        window.geometry("690x650")
        window.protocol("WM_DELETE_WINDOW", self.close)
        window.bind("<Destroy>", self.destroyed, add="+")
        self.status = tk.StringVar(window)
        self.fields: dict[str, tk.StringVar] = {}

        try:
            settings = backend.load()
        except (ValueError, OSError):
            settings = Settings()
            self.status.set("Les réglages locaux sont illisibles. Vérifiez-les avant d’enregistrer.")

        content = ttk.Frame(window, padding=18)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="Réglages", font=("Segoe UI", 22, "bold")).pack(anchor="w", pady=(0, 10))
        tabs = ttk.Notebook(content)
        tabs.pack(fill="both", expand=True)
        camera, voice, sms, demo = (ttk.Frame(tabs, padding=14) for _ in range(4))
        for tab, label in zip((camera, voice, sms, demo), ("Caméra et zones", "Voix et délai", "SMS", "Simulation")):
            tabs.add(tab, text=label)
            tab.columnconfigure(1, weight=1)

        self.field(camera, 0, "camera_index", "Index de la webcam", settings.camera_index)
        self.field(camera, 1, "zone", "Zone surveillée (%)", self.rect_text(settings.zone))
        self.field(camera, 2, "rest_zone", "Zone de repos (%)", self.rect_text(settings.rest_zone))
        ttk.Label(camera, text="Saisissez gauche, haut, droite, bas. La zone de repos est facultative.",
                  wraplength=475).grid(row=3, columnspan=2, sticky="w", pady=(0, 8))
        ttk.Button(camera, text="Actualiser l’aperçu", command=self.preview).grid(row=4, sticky="w")
        self.canvas = tk.Canvas(camera, width=450, height=253, bg="#20343a", highlightthickness=0)
        self.canvas.grid(row=5, columnspan=2, sticky="w", pady=(10, 0))
        self.draw_preview(None, settings)

        self.field(voice, 0, "voice_path", "Exécutable eSpeak NG", settings.voice_path)
        self.field(voice, 1, "volume", "Volume (%)", settings.volume)
        self.field(voice, 2, "countdown_seconds", "Décompte (secondes)", settings.countdown_seconds)
        ttk.Button(voice, text="Tester le son", command=self.sound).grid(row=3, sticky="w", pady=8)

        self.field(sms, 0, "sms_mode", "Mode SMS",
                   "Réel" if settings.sms_mode == Mode.LIVE else "Simulé", ("Simulé", "Réel"))
        self.field(sms, 1, "first_name", "Prénom surveillé", settings.first_name)
        self.field(sms, 2, "address", "Adresse d’intervention", settings.address)
        self.field(sms, 3, "contact_name", "Nom du proche", settings.contact_name)
        self.field(sms, 4, "recipient", "Numéro du proche", settings.recipient)
        self.field(sms, 5, "sender", "Numéro Twilio", settings.sender)
        ttk.Button(sms, text="Vérifier le canal", command=self.check_sms).grid(row=6, sticky="w", pady=8)
        ttk.Button(sms, text="Envoyer un SMS de test réel", command=self.test_sms).grid(
            row=6, column=1, sticky="w", pady=8)
        self.real_result = tk.StringVar(window, "SMS non vérifié")
        ttk.Label(sms, textvariable=self.real_result, wraplength=500, justify="left").grid(
            row=7, columnspan=2, sticky="w")

        ttk.Label(demo, text="Incident fictif", font=("Segoe UI", 15, "bold")).grid(sticky="w")
        ttk.Label(demo, text="Marie · 123, rue Exemple, Montréal").grid(row=1, sticky="w", pady=(4, 14))
        self.demo_state = tk.StringVar(window, "Prêt à simuler une chute")
        ttk.Label(demo, textvariable=self.demo_state, wraplength=510, justify="left").grid(
            row=2, columnspan=2, sticky="w")
        self.start_button = ttk.Button(demo, text="Simuler une chute", command=self.start_demo)
        self.start_button.grid(row=3, sticky="w", pady=10)
        self.cancel_button = ttk.Button(demo, text="Annuler l’alerte", command=self.cancel_demo, state="disabled")
        self.cancel_button.grid(row=3, column=1, sticky="w", padx=12)
        self.field(demo, 4, "reply", "Réponse fictive", DEMO_REPLY)
        self.reply_button = ttk.Button(demo, text="Simuler la réponse", command=self.reply_demo, state="disabled")
        self.reply_button.grid(row=5, columnspan=2, sticky="w", pady=8)
        self.demo_reply = tk.StringVar(window)
        ttk.Label(demo, textvariable=self.demo_reply, wraplength=510).grid(row=6, columnspan=2, sticky="w")

        ttk.Label(content, textvariable=self.status, wraplength=545, justify="left").pack(
            anchor="w", pady=(7, 0))
        ttk.Button(content, text="Enregistrer les réglages", command=self.save).pack(anchor="e", pady=(6, 0))

    def field(self, frame, row, key, label, value, options=None):
        variable = self.fields[key] = tk.StringVar(self.window, str(value))
        widget = (ttk.Combobox(frame, textvariable=variable, values=options, state="readonly")
                  if options else ttk.Entry(frame, textvariable=variable))
        ttk.Label(frame, text=label).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=6)
        widget.grid(row=row, column=1, sticky="ew", pady=6)

    @staticmethod
    def rect_text(rect):
        return ", ".join(str(value) for value in rect) if rect else ""

    def value(self, name):
        return self.fields[name].get().strip()

    def number(self, key, label):
        try:
            return int(self.value(key))
        except ValueError:
            raise ValueError(f"{label} doit être un nombre entier.") from None

    def rect(self, key):
        try:
            return tuple(int(value.strip()) for value in self.value(key).split(","))
        except ValueError:
            raise ValueError("Utilisez quatre nombres pour chaque zone.") from None

    def read(self):
        return Settings(
            self.number("camera_index", "L’index de la caméra"), self.rect("zone"),
            self.rect("rest_zone") if self.value("rest_zone") else None,
            self.value("voice_path"), self.number("volume", "Le volume"),
            self.number("countdown_seconds", "Le décompte"),
            Mode.LIVE if self.value("sms_mode") == "Réel" else Mode.SIMULATED,
            *(self.value(key) for key in ("first_name", "address", "contact_name", "recipient", "sender")),
        )

    def save(self):
        try:
            settings = self.read()
            errors = self.backend.validate(settings)
            if errors:
                self.status.set("\n".join(errors.values()))
                return
            self.backend.apply(settings)
        except (ValueError, OSError) as error:
            self.status.set(str(error) if isinstance(error, ValueError) else "Écriture des réglages impossible.")
            return
        self.status.set("Réglages enregistrés.")

    def run(self, operation: Callable, completed: Callable, failed: Callable):
        future = self.executor.submit(operation)

        def collect():
            if self.closed:
                return
            if not future.done():
                self.window.after(75, collect)
                return
            try:
                result = future.result()
            except Exception:
                failed()
            else:
                completed(result)

        self.window.after(75, collect)

    def draw_preview(self, image, settings):
        self.canvas.delete("all")
        if image is None:
            self.canvas.create_text(225, 125, text="Aperçu simulé · aucune image de caméra",
                                    fill="white", font=("Segoe UI", 11))
        else:
            try:
                self.preview_image = tk.PhotoImage(data=base64.b64encode(image))
            except tk.TclError:
                self.status.set("Aperçu de caméra indisponible.")
                return
            self.canvas.create_image(0, 0, image=self.preview_image, anchor="nw")
        for rect, color in ((settings.zone, "#45e5c0"), (settings.rest_zone, "#ffce76")):
            if rect:
                left, top, right, bottom = rect
                self.canvas.create_rectangle(2 + left * 4.46, 2 + top * 2.49, 2 + right * 4.46,
                                             2 + bottom * 2.49, outline=color, width=2)

    def preview(self):
        try:
            settings = replace(Settings(), camera_index=self.number("camera_index", "L’index de la caméra"),
                               zone=self.rect("zone"), rest_zone=self.rect("rest_zone")
                               if self.value("rest_zone") else None)
            errors = self.services.validate(settings)
            if errors:
                raise ValueError("\n".join(errors.values()))
        except ValueError as error:
            self.status.set(str(error))
            return
        self.run(lambda: self.backend.preview(settings.camera_index),
                 lambda image: self.draw_preview(image, settings),
                 lambda: self.status.set("Caméra indisponible."))

    def sound(self):
        try:
            settings = replace(Settings(), voice_path=self.value("voice_path"),
                               volume=self.number("volume", "Le volume"))
            errors = self.services.validate(settings)
            if errors:
                raise ValueError("\n".join(errors.values()))
        except ValueError as error:
            self.status.set(str(error))
            return
        self.status.set("Test sonore en cours…")
        self.run(lambda: self.backend.test_sound(settings),
                 lambda ok: self.status.set("Test sonore terminé." if ok else "Voix indisponible."),
                 lambda: self.status.set("Voix indisponible."))

    def sms_settings(self):
        return replace(Settings(), sms_mode=Mode.LIVE if self.value("sms_mode") == "Réel"
                       else Mode.SIMULATED,
                       sender=self.value("sender"), recipient=self.value("recipient"))

    def check_sms(self):
        settings = self.sms_settings()
        self.real_result.set("Vérification du canal…")
        self.run(lambda: SmsService(self.state_dir / "sms.sqlite3", self.services.sms(settings),
                                    Source.MANUAL_SMS).check_diagnostic(), self.real_result.set,
                 lambda: self.real_result.set(
                     "Vérification impossible. Vérifiez l’accès au stockage local de l’application."))

    def test_sms(self):
        if self.real_test_running:
            return
        settings = self.sms_settings()
        config = self.services.sms(settings)
        if config.mode is not Mode.LIVE:
            self.real_result.set("Choisissez Réel pour envoyer un SMS de test.")
            return
        if issues := config.issues():
            self.real_result.set("SMS de test bloqué · configuration\n" + "\n".join(issues))
            return
        if not messagebox.askyesno("Envoi réel",
                                   f"Envoyer le SMS de test convenu au numéro se terminant par "
                                   f"{config.recipient[-4:]} ?", parent=self.window, default="no"):
            return
        self.real_test_running = True
        self.real_result.set("Soumission du SMS de test…")
        alert = Alert(f"test-{uuid4()}", datetime.now().astimezone(), "")

        def submit():
            service = SmsService(self.state_dir / "sms.sqlite3", config, Source.MANUAL_SMS)
            return service, service.submit(alert, confirmed_test=True)

        def started(result):
            service, submission = result
            self.real_result.set(test_result_label(submission))
            if submission.provider_sid:
                self.poll_test(service, submission, None)
            else:
                self.real_test_running = False

        def failed():
            self.real_test_running = False
            self.real_result.set(
                "SMS de test · résultat indisponible. Vérifiez le stockage local et les journaux "
                "Twilio avant de relancer le test.")

        self.run(submit, started, failed)

    def poll_test(self, service, submission, latest):
        since = submission.attempted_at
        redact = tuple(self.value(key) for key in ("first_name", "address", "contact_name"))

        def poll():
            current = service.refresh(submission.incident_id)
            if current.state is Delivery.FAILED and not current.diagnostic:
                current = replace(current, diagnostic=submission.diagnostic)
            targets = [
                ReplyTarget(item.incident_id, item.attempted_at, service.config.recipient,
                            service.config.sender, item.incident_id == submission.incident_id)
                for item in service.recent_submissions(since - REPLY_AMBIGUITY_WINDOW)
            ]
            response = latest
            ambiguous = False
            read_error = None
            try:
                messages = service.poll_replies(since)
            except ServiceError as error:
                messages = []
                read_error = twilio_diagnostic(error)
            for message in messages:
                # Un SMS réel de test ne doit pas accaparer les réponses du moteur d’Adam.
                if associate(message, targets) is None:
                    ambiguous = True
                elif newer(message, response[0] if response else None):
                    interpretation = OpenAI(*self.services.model()).interpret(message.body, redact=redact)
                    response = message, interpretation
            return current, response, ambiguous, read_error

        def completed(result):
            current, response, ambiguous, read_error = result
            label = test_result_label(current)
            if response:
                message, interpretation = response
                label += f"\n{message.received_at.astimezone():%H:%M:%S} · {interpretation.label()}"
                if interpretation.service_unavailable:
                    label += " · OpenAI indisponible"
            if ambiguous:
                label += "\nRéponse reçue, à vérifier (rattachement incertain)"
            if read_error:
                label += f"\nLecture des réponses · {read_error}"
            self.real_result.set(label)
            schedule(current, response)

        def schedule(current, response):
            if datetime.now(timezone.utc) < since + REPLY_WINDOW:
                self.window.after(POLL_SECONDS * 1000, lambda: self.poll_test(service, current, response))
            else:
                self.real_test_running = False

        def failed():
            self.real_result.set(test_result_label(submission) + "\nSuivi du SMS interrompu.")
            schedule(submission, latest)

        self.run(poll, completed, failed)

    def start_demo(self):
        try:
            seconds = self.number("countdown_seconds", "Le décompte")
            if not 1 <= seconds <= 300:
                raise ValueError("Choisissez un délai de 1 à 300 secondes.")
            self.demo = Demo.create(self.state_dir / "simulation.sqlite3")
        except ValueError as error:
            self.demo_state.set(str(error))
            return
        except (OSError, sqlite3.Error):
            self.demo_state.set("Stockage local indisponible.")
            return
        self.demo_running = True
        self.deadline = monotonic() + seconds
        self.demo_reply.set("")
        self.start_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.reply_button.configure(state="disabled")
        self.tick()

    def tick(self):
        if not self.demo_running:
            return
        remaining = ceil(self.deadline - monotonic())
        if remaining > 0:
            self.demo_state.set(f"Chute possible · SMS simulé dans {remaining} s")
            self.demo_timer = self.window.after(100, self.tick)
            return
        self.demo_running = False
        self.cancel_button.configure(state="disabled")

        def sent(result):
            self.demo_state.set(f"SMS · {result.state}\n{self.demo.alert.message()}")
            self.reply_button.configure(state="normal")
            self.start_button.configure(state="normal")

        def failed():
            self.demo_state.set("Simulation indisponible.")
            self.start_button.configure(state="normal")

        self.run(self.demo.send, sent, failed)

    def cancel_demo(self):
        if not self.demo_running:
            return
        if monotonic() >= self.deadline:
            self.window.after_cancel(self.demo_timer)
            self.tick()
            return
        self.demo_running = False
        self.window.after_cancel(self.demo_timer)
        self.demo_state.set("Alerte annulée · aucun SMS soumis")
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")

    def reply_demo(self):
        if not self.value("reply"):
            self.demo_reply.set("Saisissez une réponse fictive.")
            return
        self.reply_button.configure(state="disabled")
        self.start_button.configure(state="disabled")
        body = self.value("reply")

        def completed(result):
            message, interpretation = result
            self.demo_reply.set(f"{message.received_at.astimezone():%H:%M:%S} · {interpretation.label()}")
            self.reply_button.configure(state="normal")
            self.start_button.configure(state="normal")

        def failed():
            self.demo_reply.set("Lecture de la réponse simulée indisponible.")
            self.reply_button.configure(state="normal")
            self.start_button.configure(state="normal")

        self.run(lambda: self.demo.reply(body), completed, failed)

    def destroyed(self, event):
        if event.widget == self.window and not self.closed:
            self.closed = True
            self.executor.shutdown(wait=False, cancel_futures=True)

    def close(self):
        self.window.destroy()
