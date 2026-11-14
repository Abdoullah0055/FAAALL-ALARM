"""Canal Telegram privé, avec intention durable et un lecteur par bot sur le PC."""

import json
import re
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from secrets import token_urlsafe
from threading import RLock
from time import time
from urllib.parse import urlencode

from .http import ServiceError, request_json
from .model import FallCause
from .sms import Alert
from .sms_log import SMS_LOG_PATH, write_sms_log
from .sms_contract import IncomingSms, SmsAvailability, SmsStatus, SmsSubmission

TOKEN = re.compile(r"[0-9]+:[A-Za-z0-9_-]+")
REPLY_SECONDS = 1800
TELEGRAM_LOG_PATH = SMS_LOG_PATH.with_name("telegram-logs.txt")


class TelegramError(Exception):
    """Erreur dont le libellé est affichable et journalisable."""


class TelegramChannel:
    def __init__(self, database, token, transport=request_json, now=time, log_path=None):
        self.database = database
        self.token = token
        self.transport = transport
        self.now = now
        self.lock = RLock()
        self.challenge = None
        self.candidate = None
        self.pending = {}
        self.next_offset = None
        self.bot_id = token.partition(":")[0]
        self.log_path = log_path

    def log(self, event):
        if self.log_path is not None:
            write_sms_log(self.log_path, "Telegram", event)

    def _connect(self):
        self.database.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.database)
        try:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS pairing (bot TEXT PRIMARY KEY, chat INTEGER);
                CREATE TABLE IF NOT EXISTS cursor (bot TEXT PRIMARY KEY, offset INTEGER, updated REAL);
                CREATE TABLE IF NOT EXISTS seen (
                    bot TEXT, update_id INTEGER, processed REAL, PRIMARY KEY (bot, update_id)
                );
                CREATE TABLE IF NOT EXISTS alerts (
                    bot TEXT, chat INTEGER, incident TEXT, kind TEXT, submitted REAL,
                    message INTEGER, state TEXT, PRIMARY KEY (bot, incident)
                );
            """)
        except sqlite3.Error:
            db.close()
            raise
        return db

    def call(self, method, **fields):
        if not TOKEN.fullmatch(self.token):
            self.log(f"{method} · jeton manquant ou format invalide.")
            raise TelegramError("Renseignez TELEGRAM_BOT_TOKEN avec le jeton BotFather.")
        try:
            response = self.transport(
                f"https://api.telegram.org/bot{self.token}/{method}",
                {"Content-Type": "application/x-www-form-urlencoded"}, urlencode(fields).encode(),
            )
        except ServiceError as error:
            detail = f"HTTP {error.status}" if error.status is not None else error.reason
            self.log(f"{method} · échec · {detail}.")
            raise
        except (OSError, ValueError):
            self.log(f"{method} · erreur de transport ou de décodage.")
            raise ServiceError() from None
        if response.get("ok") is not True:
            status = response.get("error_code")
            self.log(f"{method} · refus Telegram · code {status if type(status) is int else 'absent'}.")
            raise ServiceError(status if type(status) is int else None)
        if method != "getUpdates":
            self.log(f"{method} · réponse acceptée par l’API.")
        return response.get("result")

    def chat(self):
        if not TOKEN.fullmatch(self.token):
            return None
        with closing(self._connect()) as db:
            row = db.execute("SELECT chat FROM pairing WHERE bot = ?", (self.bot_id,)).fetchone()
        return row[0] if row else None

    def start_pairing(self):
        with self.lock:
            bot = self.call("getMe")
            username = bot.get("username") if isinstance(bot, dict) else None
            if (not isinstance(username, str) or not re.fullmatch(r"[A-Za-z0-9_]+", username)
                    or str(bot.get("id")) != self.bot_id):
                raise TelegramError("Identité du bot Telegram indisponible.")
            code = token_urlsafe(24)
            self.challenge = (code, self.now() + 600)
            self.candidate = None
            self.log("Association · lien généré, attente de Démarrer (10 minutes).")
            return f"https://t.me/{username}?start={code}"

    def pairing_ready(self):
        with self.lock:
            if not self.challenge or self.challenge[1] <= self.now():
                self.log("Association · lien expiré.")
                raise TelegramError("Lien expiré. Relancez l’association.")
            self._poll()
            return self.candidate is not None

    def confirm_pairing(self):
        with self.lock:
            if not self.challenge or self.challenge[1] <= self.now() or self.candidate is None:
                raise TelegramError("Association expirée ou réponse du proche manquante.")
            with closing(self._connect()) as db, db:
                db.execute("INSERT OR REPLACE INTO pairing VALUES (?, ?)", (self.bot_id, self.candidate))
            self.challenge = self.candidate = None
            self.log("Association · proche confirmé sur le PC.")

    def send(self, incident, text, kind, chat):
        if chat is None or not TOKEN.fullmatch(self.token):
            self.log("Envoi bloqué · bot ou proche non configuré.")
            return SmsSubmission(SmsStatus.FAILED, error="Associez un proche dans Réglages → Telegram.")
        attempted = self.now()
        submitted = datetime.fromtimestamp(attempted, timezone.utc)
        with closing(self._connect()) as db, db:
            claim = db.execute("INSERT OR IGNORE INTO alerts VALUES (?, ?, ?, ?, ?, NULL, ?)",
                               (self.bot_id, chat, incident, kind, attempted, SmsStatus.TELEGRAM_UNKNOWN))
        if claim.rowcount != 1:
            self.log("Envoi ignoré · intention déjà enregistrée.")
            with closing(self._connect()) as db:
                message, state, timestamp = db.execute(
                    "SELECT message, state, submitted FROM alerts WHERE bot = ? AND incident = ?",
                    (self.bot_id, incident)).fetchone()
            return SmsSubmission(SmsStatus(state), f"telegram:{message}" if message else None,
                                 submitted_at=datetime.fromtimestamp(timestamp, timezone.utc))
        message_id = None
        error = None
        self.log(f"Envoi {kind} · intention enregistrée.")
        try:
            result = self.call("sendMessage", chat_id=chat, text=text,
                               reply_markup=json.dumps({"force_reply": True}))
            message_id = result.get("message_id") if isinstance(result, dict) else None
            if type(message_id) is not int or message_id <= 0:
                message_id = None
                raise ServiceError()
            state = SmsStatus.TELEGRAM_ACCEPTED
        except ServiceError as failure:
            refused = failure.status is not None and 400 <= failure.status < 500 and failure.status != 408
            state = SmsStatus.FAILED if refused else SmsStatus.TELEGRAM_UNKNOWN
            error = (f"Telegram a refusé l’envoi (HTTP {failure.status})." if refused
                     else "Résultat Telegram incertain. Aucun renvoi automatique.")
        with closing(self._connect()) as db, db:
            db.execute("UPDATE alerts SET message = ?, state = ? WHERE bot = ? AND incident = ?",
                       (message_id, state, self.bot_id, incident))
        self.log(f"Envoi {kind} · {state.value}.")
        return SmsSubmission(state, f"telegram:{message_id}" if message_id else None, error, submitted)

    def _poll(self):
        # Les réponses utiles restent en mémoire jusqu’à leur traitement. Le curseur n’est
        # enregistré qu’après acquittement, pour permettre une relecture après interruption.
        self._save_cursor()
        with closing(self._connect()) as db:
            row = db.execute("SELECT offset, updated FROM cursor WHERE bot = ?", (self.bot_id,)).fetchone()
            seen = {row[0] for row in db.execute("SELECT update_id FROM seen WHERE bot = ?", (self.bot_id,))}
        offset = row[0] if row and self.now() - row[1] < 86400 else 0
        updates = self.call("getUpdates", offset=offset, timeout=0, allowed_updates='["message"]')
        if not isinstance(updates, list):
            raise TelegramError("Réponse Telegram inattendue.")
        for update in updates:
            update_id = update.get("update_id") if isinstance(update, dict) else None
            if type(update_id) is not int:
                continue
            self.next_offset = max(self.next_offset or 0, update_id + 1)
            if update_id not in seen and str(update_id) not in self.pending:
                self._handle(update_id, update.get("message"))
        self._save_cursor()

    def _handle(self, update_id, message):
        if not isinstance(message, dict):
            return
        chat = message.get("chat")
        sender = message.get("from")
        body, date = message.get("text"), message.get("date")
        if not isinstance(chat, dict) or not isinstance(sender, dict) or chat.get("type") != "private":
            return
        chat_id = chat.get("id")
        if type(chat_id) is not int or sender.get("id") != chat_id or sender.get("is_bot"):
            return
        if not isinstance(body, str) or type(date) is not int:
            return
        if self.challenge and self.now() < self.challenge[1] and body == f"/start {self.challenge[0]}":
            if self.candidate is None:
                self.candidate = chat_id
                self.log("Association · code reçu et validé, attente de la confirmation sur le PC.")
            return
        if self.challenge and body.startswith("/start"):
            self.log("Association · commande Démarrer reçue sans code valide.")
        quoted = message.get("reply_to_message")
        reference = quoted.get("message_id") if isinstance(quoted, dict) else None
        if body.startswith("/"):
            return
        with closing(self._connect()) as db:
            if quoted is None:
                candidates = db.execute("""
                    SELECT kind, incident, submitted, message FROM alerts
                    WHERE bot = ? AND chat = ? AND submitted >= ? AND submitted < ?
                    LIMIT 2
                """, (self.bot_id, chat_id, date - REPLY_SECONDS, date + 1)).fetchall()
                row = candidates[0] if len(candidates) == 1 and candidates[0][3] else None
            else:
                row = db.execute("""
                    SELECT kind, incident, submitted FROM alerts WHERE bot = ? AND chat = ? AND message = ?
                """, (self.bot_id, chat_id, reference)).fetchone()
        if row and int(row[2]) <= date <= row[2] + REPLY_SECONDS and date <= self.now():
            self.log(f"Réception {row[0]} · réponse rattachée.")
            self.pending[str(update_id)] = (row[0], IncomingSms(
                f"telegram:{self.bot_id}:{update_id}", str(chat_id), self.bot_id, body,
                datetime.fromtimestamp(date, timezone.utc), row[1],
            ), row[2] + REPLY_SECONDS)
        else:
            self.log("Réception · message sans alerte identifiable ou réponse hors délai.")

    def _save_cursor(self):
        if self.pending or self.next_offset is None:
            return
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO cursor VALUES (?, ?, ?)",
                       (self.bot_id, self.next_offset, self.now()))
            db.execute("DELETE FROM seen WHERE bot = ? AND processed < ?",
                       (self.bot_id, self.now() - 86400))
        self.next_offset = None

    def replies(self, kind, chat, since, incident=None):
        if chat is None:
            return []
        with self.lock:
            for key in [key for key, item in self.pending.items() if self.now() > item[2]]:
                del self.pending[key]
            self._poll()
            return [reply for target, reply, _ in self.pending.values()
                    if target == kind and reply.sender == str(chat)
                    and reply.received_at.timestamp() >= int(since.timestamp())
                    and (incident is None or reply.incident_ref == incident)]

    def acknowledge_reply(self, message_id):
        with self.lock:
            key = message_id.rsplit(":", 1)[-1]
            if key not in self.pending:
                return
            with closing(self._connect()) as db, db:
                db.execute("INSERT OR IGNORE INTO seen VALUES (?, ?, ?)", (self.bot_id, int(key), self.now()))
            del self.pending[key]
            self.log("Réception · réponse acquittée.")
            self._save_cursor()


@lru_cache(maxsize=8)
def telegram_channel(state_dir, token):
    return TelegramChannel(state_dir / "telegram.sqlite3", token, log_path=TELEGRAM_LOG_PATH)


class TelegramGateway:
    def __init__(self, channel):
        self.channel = channel
        try:
            self.chat = channel.chat()
        except (OSError, sqlite3.Error):
            self.chat = None

    def check_availability(self):
        if self.chat is None:
            return SmsAvailability.TELEGRAM_UNAVAILABLE
        try:
            self.channel.call("getChat", chat_id=self.chat)
        except (ServiceError, TelegramError):
            return SmsAvailability.TELEGRAM_UNAVAILABLE
        return SmsAvailability.TELEGRAM_OPERATIONAL

    def submit_alert(self, request):
        try:
            text = Alert(request.incident_id, request.occurred_at.astimezone(), request.address,
                         request.first_name or "", bool(request.first_name),
                         request.cause is FallCause.COLLISION).message()
            return self.channel.send(request.incident_id, text + "\nRépondez à ce message pour "
                                     "indiquer si vous pouvez intervenir et dans combien de temps.",
                                     "incident", self.chat)
        except (OSError, sqlite3.Error):
            return SmsSubmission(SmsStatus.TELEGRAM_UNKNOWN, error="Stockage local indisponible.")
        except ValueError:
            return SmsSubmission(SmsStatus.FAILED, error="Adresse d’intervention manquante.")

    def get_status(self, provider_ref):
        if not provider_ref.startswith("telegram:"):
            raise TelegramError("Référence d’un autre canal.")
        return SmsStatus.TELEGRAM_ACCEPTED

    def fetch_replies(self, since):
        return self.channel.replies("incident", self.chat, since)

    def acknowledge_reply(self, message_id):
        self.channel.acknowledge_reply(message_id)
