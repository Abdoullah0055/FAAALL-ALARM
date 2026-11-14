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

from .config import TELEGRAM_MODE
from .demo import Demo
from .http import ServiceError
from .replies import DEMO_REPLY, OpenAI, ReplyTarget, associate, newer
from .settings import FULL_FRAME, LocalSettings, Settings, SettingsPort
from .sms import (Alert, Delivery, Mode, POLL_SECONDS, REPLY_AMBIGUITY_WINDOW,
                  REPLY_WINDOW, SmsService, Source, Submission, twilio_diagnostic)
from .sms_log import SMS_LOG_PATH, write_sms_log
from .telegram import TelegramError, telegram_channel

_CHANNELS = {"Simulé": Mode.SIMULATED, "SMS Twilio": Mode.LIVE, "Telegram": TELEGRAM_MODE}


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
        self.telegram = telegram_channel(state_dir, services.config.get("TELEGRAM_BOT_TOKEN") or "")
        self.telegram_busy = False
        self.telegram_test = None
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
        camera, voice, sms, telegram, demo = (ttk.Frame(tabs, padding=14) for _ in range(5))
        for tab, label in zip((camera, voice, sms, telegram, demo),
                              ("Caméra et zones", "Voix et délai", "SMS", "Telegram", "Simulation")):
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

        self.field(sms, 0, "sms_mode", "Canal d’alerte",
                   next((label for label, mode in _CHANNELS.items() if mode == settings.sms_mode), "Simulé"),
                   tuple(_CHANNELS))
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
        ttk.Label(sms, text="Journal local · sms-logs.txt à la racine du projet.").grid(
            row=8, columnspan=2, sticky="w", pady=8)
        self.telegram_status = tk.StringVar(window, "Créez un bot dédié avec @BotFather et /newbot.")
        ttk.Label(telegram, text="Configurez TELEGRAM_BOT_TOKEN dans le fichier .env local, puis "
                  "relancez l’application. Pour les alertes, choisissez Telegram dans l’onglet SMS "
                  "et enregistrez les réglages.", wraplength=490, justify="left").grid(
                      row=0, columnspan=2, sticky="w", pady=(0, 12))
        ttk.Button(telegram, text="Associer un proche", command=self.start_telegram_pairing).grid(
            row=1, sticky="w")
        ttk.Button(telegram, text="Envoyer un message de test", command=self.send_telegram_test).grid(
            row=1, column=1, sticky="w")
        self.telegram_link = ttk.Entry(telegram, width=58, state="readonly")
        self.telegram_link.grid(row=2, columnspan=2, sticky="ew", pady=8)
        self.telegram_link_value = tk.StringVar(window)
        self.telegram_link.configure(textvariable=self.telegram_link_value)
        ttk.Button(telegram, text="Copier le lien", command=self.copy_telegram_link).grid(row=4, sticky="w", pady=8)
        ttk.Label(telegram, textvariable=self.telegram_status, wraplength=490, justify="left").grid(
            row=3, columnspan=2, sticky="w")

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
            _CHANNELS[self.value("sms_mode")],
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

    def run(self, operation: Callable, completed: Callable, failed: Callable, *, on_error=None):
        future = self.executor.submit(operation)

        def collect():
            if self.closed:
                return
            if not future.done():
                self.window.after(75, collect)
                return
            try:
                result = future.result()
            except Exception as error:
                if on_error is None:
                    failed()
                else:
                    on_error(error)
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
        return replace(Settings(), sms_mode=Mode.LIVE if self.value("sms_mode") == "SMS Twilio"
                       else Mode.SIMULATED,
                       sender=self.value("sender"), recipient=self.value("recipient"))

    def log_sms(self, event: str):
        write_sms_log(SMS_LOG_PATH, Source.MANUAL_SMS, event)

    def telegram_error(self, error):
        self.telegram.log(f"Réglages · opération interrompue ({type(error).__name__}).")
        self.telegram_busy = False
        self.telegram_test = None
        self.telegram_link_value.set("")
        if isinstance(error, TelegramError):
            label = str(error)
        elif isinstance(error, ServiceError):
            label = {
                401: "Jeton Telegram refusé. Vérifiez TELEGRAM_BOT_TOKEN.",
                403: "Accès refusé. Vérifiez que le proche a démarré le bot.",
                409: "Le bot est déjà utilisé par un autre lecteur ou par un webhook.",
            }.get(error.status, "Telegram indisponible. Vérifiez la connexion et réessayez.")
        else:
            label = "Stockage ou traitement local Telegram indisponible."
        self.telegram_status.set(label)

    def run_telegram(self, operation, completed):
        self.run(operation, completed, lambda: None, on_error=self.telegram_error)

    def copy_telegram_link(self):
        if link := self.telegram_link_value.get():
            self.window.clipboard_clear()
            self.window.clipboard_append(link)
            self.telegram_status.set("Lien copié. Ouvrez-le avec le compte Telegram du proche.")

    def start_telegram_pairing(self):
        if self.telegram_busy:
            return
        if not getattr(self.backend, "telegram_pairing_allowed", True):
            self.telegram_status.set("Attendez la fin de l’incident avant d’associer un proche.")
            return
        self.telegram_busy = True
        self.telegram_status.set("Vérification du bot…")

        def ready(link):
            self.telegram_link_value.set(link)
            self.telegram_status.set("Ouvrez ce lien sur l’iPhone du proche et touchez Démarrer.")
            self.window.after(1000, self.poll_telegram_pairing)

        self.run_telegram(self.telegram.start_pairing, ready)

    def poll_telegram_pairing(self):
        def checked(ready):
            if not ready:
                self.window.after(2000, self.poll_telegram_pairing)
                return
            self.telegram_link_value.set("")
            if not getattr(self.backend, "telegram_pairing_allowed", True):
                self.telegram_busy = False
                self.telegram_status.set("Attendez la fin de l’incident puis recommencez l’association.")
            elif messagebox.askyesno("Associer le proche",
                                   "Confirmez-vous que le proche a ouvert le lien sur son iPhone ?",
                                   parent=self.window, default="no"):
                self.run_telegram(self.telegram.confirm_pairing, self.telegram_paired)
            else:
                self.telegram_busy = False
                self.telegram.log("Association · confirmation refusée sur le PC.")
                self.telegram_status.set("Association annulée.")

        self.run_telegram(self.telegram.pairing_ready, checked)

    def telegram_paired(self, _):
        self.telegram_busy = False
        self.telegram_status.set("Proche associé. Choisissez Telegram et enregistrez les réglages.")

    def send_telegram_test(self):
        if self.telegram_busy:
            return
        if not getattr(self.backend, "allows_telegram_test", True):
            self.telegram_status.set("Le test Telegram réel est indisponible en lecture MP4 ou en démo.")
            return
        if not messagebox.askyesno("Test Telegram réel", "Envoyer un message au proche associé ?",
                                   parent=self.window, default="no"):
            return
        self.telegram_busy = True
        self.telegram_test = None
        self.telegram_status.set("Envoi du message de test…")
        incident = f"test-{uuid4()}"

        def send():
            chat = self.telegram.chat()
            result = self.telegram.send(incident, "FAAALL-ALARM · Test. Répondez à ce message pour indiquer "
                                        "si vous pouvez intervenir et dans combien de temps.", "test", chat)
            return chat, result

        def sent(result):
            chat, submission = result
            self.telegram_busy = False
            self.telegram_status.set(submission.error or submission.status.value)
            if submission.provider_ref:
                self.telegram_test = (incident, chat, submission.submitted_at)
                test = self.telegram_test
                self.window.after(2000, lambda: self.poll_telegram_test(test))

        self.run_telegram(send, sent)

    def poll_telegram_test(self, test):
        if self.telegram_test is not test:
            return
        incident, chat, since = test
        if (datetime.now(timezone.utc) - since) >= REPLY_WINDOW:
            self.telegram_test = None
            self.telegram_status.set("Délai de réponse au test écoulé.")
            return
        model = self.services.model()
        redact = tuple(self.value(key) for key in ("first_name", "address", "contact_name",
                                                   "recipient", "sender"))

        def check():
            replies = self.telegram.replies("test", chat, since, incident)
            if not replies:
                return None
            message = max(replies, key=lambda reply: reply.received_at)
            interpretation = OpenAI(*model).interpret(message.body, redact=redact)
            for reply in replies:
                self.telegram.acknowledge_reply(reply.message_id)
            return interpretation

        def checked(interpretation):
            if self.telegram_test is not test:
                return
            if interpretation is None:
                self.window.after(3000, lambda: self.poll_telegram_test(test))
            else:
                self.telegram_test = None
                self.telegram_status.set(f"Réponse au test · {interpretation.label()}")

        def failed(error):
            if self.telegram_test is test:
                self.telegram_error(error)

        self.run(check, checked, lambda: None, on_error=failed)

    def check_sms(self):
        settings = self.sms_settings()
        self.real_result.set("Vérification du canal…")
        self.run(lambda: SmsService(self.state_dir / "sms.sqlite3", self.services.sms(settings),
                                    Source.MANUAL_SMS, log_path=SMS_LOG_PATH).check_diagnostic(),
                 self.real_result.set, self.sms_check_failed)

    def sms_check_failed(self):
        self.log_sms("Vérification interrompue · erreur locale.")
        self.real_result.set("Vérification impossible. Vérifiez l’accès au stockage local de l’application.")

    def test_sms(self):
        if self.real_test_running:
            return
        settings = self.sms_settings()
        config = self.services.sms(settings)
        if config.mode is not Mode.LIVE:
            self.log_sms("Soumission bloquée · mode simulé.")
            self.real_result.set("Choisissez SMS Twilio pour envoyer un SMS de test.")
            return
        if issues := config.issues():
            self.log_sms("Soumission bloquée · " + " ".join(issues))
            self.real_result.set("SMS de test bloqué · configuration\n" + "\n".join(issues))
            return
        if not messagebox.askyesno("Envoi réel",
                                   f"Envoyer le SMS de test convenu au numéro se terminant par "
                                   f"{config.recipient[-4:]} ?", parent=self.window, default="no"):
            self.log_sms("Soumission annulée avant l’envoi.")
            return
        self.real_test_running = True
        self.real_result.set("Soumission du SMS de test…")
        alert = Alert(f"test-{uuid4()}", datetime.now().astimezone(), "")

        def submit():
            service = SmsService(self.state_dir / "sms.sqlite3", config, Source.MANUAL_SMS,
                                 log_path=SMS_LOG_PATH)
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
            self.log_sms("Soumission interrompue · erreur locale, résultat non confirmé.")
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
            self.log_sms("Suivi du SMS interrompu · erreur locale.")
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
