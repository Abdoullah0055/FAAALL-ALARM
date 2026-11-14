"""Soumission durable et réception SMS. Appeler depuis un fil de travail."""

import base64
import hashlib
import re
import sqlite3
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import StrEnum
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .http import ServiceError, request_json


E164 = re.compile(r"\+[1-9][0-9]{7,14}")
MESSAGE_SID = re.compile(r"(?:SM|MM)[a-fA-F0-9]{32}")
REPLY_WINDOW = timedelta(minutes=30)
REPLY_AMBIGUITY_WINDOW = timedelta(hours=24)
DELIVERY_WINDOW = timedelta(hours=24)
POLL_SECONDS = 5


class Mode(StrEnum):
    SIMULATED = "simulated"
    LIVE = "live"


class Source(StrEnum):
    CAMERA = "caméra"
    MP4 = "MP4"
    DEMO = "simulation"
    TEST = "test automatisé"
    MANUAL_SMS = "test SMS réel"


class Delivery(StrEnum):
    SIMULATED = "Simulé"
    PENDING = "En attente"
    DELIVERED = "Livré"
    FAILED = "Échec"
    UNKNOWN = "Livraison inconnue"


@dataclass(frozen=True)
class SmsConfig:
    mode: Mode = Mode.SIMULATED
    sender: str = field(default="", repr=False)
    recipient: str = field(default="", repr=False)
    account_sid: str = field(default="", repr=False)
    auth_token: str = field(default="", repr=False)

    def valid(self) -> bool:
        return bool(
            E164.fullmatch(self.sender) and E164.fullmatch(self.recipient)
            and self.sender != self.recipient
            and re.fullmatch(r"AC[a-fA-F0-9]{32}", self.account_sid)
            and self.auth_token.strip()
        )


@dataclass(frozen=True)
class Alert:
    incident_id: str
    occurred_at: datetime
    address: str = field(repr=False)
    first_name: str = field(default="", repr=False)
    name_reliable: bool = False
    collision: bool = False

    def message(self) -> str:
        if self.occurred_at.utcoffset() is None or not self.address.strip():
            raise ValueError("Indiquez l’heure avec son fuseau et l’adresse d’intervention.")
        subject = "CHUTE POSSIBLE"
        if self.collision:
            subject = "COLLISION ET CHUTE POSSIBLES"
        elif self.name_reliable and self.first_name.strip():
            subject = f"{self.first_name.strip().upper()} EST TOMBÉE"
        return f"ALERTE : {subject} À {self.occurred_at:%H:%M} à {self.address.strip()} !"


@dataclass(frozen=True)
class Submission:
    incident_id: str
    state: Delivery
    attempted_at: datetime
    provider_sid: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class Incoming:
    sid: str
    received_at: datetime
    sender: str = field(repr=False)
    recipient: str = field(repr=False)
    body: str = field(repr=False)


def _delivery(status: object) -> Delivery:
    if not isinstance(status, str):
        return Delivery.UNKNOWN
    if status == "delivered":
        return Delivery.DELIVERED
    if status in {"failed", "undelivered", "canceled"}:
        return Delivery.FAILED
    if status in {"accepted", "scheduled", "queued", "sending", "sent"}:
        return Delivery.PENDING
    return Delivery.UNKNOWN


class _Twilio:
    def __init__(self, config: SmsConfig, transport=request_json):
        if not config.valid():
            raise ValueError("Configuration Twilio incomplète ou invalide.")
        self.config = config
        self.transport = transport
        self.resource = f"/2010-04-01/Accounts/{config.account_sid}/Messages"
        credentials = f"{config.account_sid}:{config.auth_token}".encode()
        encoded = base64.b64encode(credentials).decode("ascii")
        self.headers = {"Authorization": f"Basic {encoded}"}

    def _get(self, path: str) -> dict:
        return self.transport(f"https://api.twilio.com{path}", self.headers)

    def submit(self, message: str) -> tuple[str, Delivery]:
        headers = self.headers | {"Content-Type": "application/x-www-form-urlencoded"}
        body = urlencode({
            "From": self.config.sender, "To": self.config.recipient, "Body": message,
        }).encode()
        result = self.transport(f"https://api.twilio.com{self.resource}.json", headers, body)
        sid = result.get("sid")
        if not isinstance(sid, str) or not MESSAGE_SID.fullmatch(sid):
            raise ServiceError()
        return sid, _delivery(result.get("status"))

    def status(self, sid: str) -> Delivery:
        if not MESSAGE_SID.fullmatch(sid):
            raise ValueError("Identifiant de message invalide.")
        return _delivery(self._get(f"{self.resource}/{sid}.json").get("status"))

    def _inbox_path(self, since: datetime, page_size: int = 100) -> str:
        query = urlencode({
            "From": self.config.recipient, "To": self.config.sender,
            # Le filtre fournisseur a une précision au jour. Le filtrage exact se fait ci-dessous.
            "DateSent>": (since.astimezone(timezone.utc).date() - timedelta(days=1)).isoformat(),
            "PageSize": page_size,
        })
        return f"{self.resource}.json?{query}"

    def check(self, now: datetime) -> None:
        if not isinstance(self._get(self._inbox_path(now, 1)).get("messages"), list):
            raise ServiceError()

    def incoming(self, since: datetime, until: datetime) -> list[Incoming]:
        path = self._inbox_path(since)
        found: dict[str, Incoming] = {}
        visited = set()
        while path:
            parsed = urlsplit(path)
            if (
                parsed.scheme or parsed.netloc or parsed.path != f"{self.resource}.json"
                or path in visited
            ):
                raise ServiceError()
            visited.add(path)
            page = self._get(path)
            if not isinstance(page.get("messages"), list):
                raise ServiceError()
            for item in page["messages"]:
                message = self._incoming_message(item, since, until)
                if message is not None:
                    found[message.sid] = message
            path = page.get("next_page_uri")
            if path is not None and not isinstance(path, str):
                raise ServiceError()
        return sorted(found.values(), key=lambda message: (message.received_at, message.sid))

    def _incoming_message(self, item, since: datetime, until: datetime) -> Incoming | None:
        if not isinstance(item, dict) or (
            item.get("direction") != "inbound" or item.get("from") != self.config.recipient
            or item.get("to") != self.config.sender
        ):
            return None
        sid, body = item.get("sid"), item.get("body")
        if not isinstance(sid, str) or not MESSAGE_SID.fullmatch(sid) or not isinstance(body, str):
            return None
        try:
            received = parsedate_to_datetime(item.get("date_sent") or "")
        except (ValueError, TypeError, OverflowError):
            return None
        if received.utcoffset() is None or not since <= received <= until:
            return None
        return Incoming(sid, received, self.config.recipient, self.config.sender, body)


class SmsService:
    def __init__(self, database: Path, config: SmsConfig, source: Source, *,
                 transport=request_json, now=lambda: datetime.now(timezone.utc)):
        self.database = database
        self.config = config
        self.source = source
        self.transport = transport
        self.now = now
        self.simulated = config.mode is not Mode.LIVE or source not in {
            Source.CAMERA, Source.MANUAL_SMS,
        }
        recipient = config.recipient if not self.simulated else "simulated"
        self.recipient_key = hashlib.sha256(recipient.encode()).hexdigest()
        self.fake_inbox: list[Incoming] = []
        database.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as db, db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS submissions (
                    incident_id TEXT NOT NULL, recipient TEXT NOT NULL, state TEXT NOT NULL,
                    attempted_at TEXT NOT NULL, sid TEXT,
                    PRIMARY KEY (incident_id, recipient)
                );
                CREATE TABLE IF NOT EXISTS seen_messages (sid TEXT PRIMARY KEY);
            """)

    def _connect(self):
        db = sqlite3.connect(self.database)
        db.row_factory = sqlite3.Row
        return db

    def _twilio(self):
        if self.simulated:
            raise RuntimeError("Canal réseau désactivé pour cette source.")
        return _Twilio(self.config, self.transport)

    def check(self) -> str:
        if self.simulated:
            return "SMS simulé"
        try:
            self._twilio().check(self.now())
        except (ServiceError, ValueError):
            return "SMS indisponible"
        return "SMS opérationnel"

    def submit(self, alert: Alert, *, confirmed_test: bool = False) -> Submission:
        if not alert.incident_id.strip():
            raise ValueError("Un identifiant d’incident stable est requis.")
        attempted_at = self.now().astimezone(timezone.utc)
        if self.simulated:
            initial = Delivery.SIMULATED
            message = ""
        else:
            if not self.config.valid():
                return Submission(alert.incident_id, Delivery.FAILED, attempted_at)
            if self.source is Source.MANUAL_SMS:
                if confirmed_test is not True:
                    raise ValueError("Confirmez l’envoi du SMS de test.")
                message = "FAAALL-ALARM : SMS de test convenu. Vous pouvez répondre à ce message."
            else:
                message = alert.message()
            # Un arrêt après le commit et avant la réponse laisse un résultat incertain.
            initial = Delivery.UNKNOWN
        with closing(self._connect()) as db, db:
            insertion = db.execute("""
                INSERT OR IGNORE INTO submissions VALUES (?, ?, ?, ?, NULL)
            """, (alert.incident_id, self.recipient_key, initial, attempted_at.isoformat()))
            claimed = insertion.rowcount == 1
        if not claimed:
            return self.lookup(alert.incident_id)
        if self.simulated:
            return Submission(alert.incident_id, initial, attempted_at)
        try:
            sid, state = self._twilio().submit(message)
        except ServiceError as error:
            # Une réponse 4xx explicite vaut refus, sauf un délai dépassé.
            rejected = error.status is not None and 400 <= error.status < 500 and error.status != 408
            sid, state = None, Delivery.FAILED if rejected else Delivery.UNKNOWN
        return self._save_result(Submission(alert.incident_id, state, attempted_at, sid))

    def lookup(self, incident_id: str) -> Submission | None:
        with closing(self._connect()) as db:
            row = db.execute("""
                SELECT * FROM submissions WHERE incident_id = ? AND recipient = ?
            """, (incident_id, self.recipient_key)).fetchone()
        if row is not None:
            return Submission(incident_id, Delivery(row["state"]),
                              datetime.fromisoformat(row["attempted_at"]), row["sid"])
        return None

    def recent_submissions(self, since: datetime) -> list[Submission]:
        with closing(self._connect()) as db:
            rows = db.execute("""
                SELECT incident_id, state, attempted_at, sid FROM submissions
                WHERE recipient = ? AND attempted_at >= ? AND (sid IS NOT NULL OR state = ?)
            """, (self.recipient_key, since.astimezone(timezone.utc).isoformat(),
                  Delivery.UNKNOWN)).fetchall()
        return [Submission(row["incident_id"], Delivery(row["state"]),
                           datetime.fromisoformat(row["attempted_at"]), row["sid"]) for row in rows]

    def _save_result(self, result: Submission) -> Submission:
        with closing(self._connect()) as db, db:
            db.execute("""
                UPDATE submissions SET state = ?, sid = ?
                WHERE incident_id = ? AND recipient = ? AND state NOT IN (?, ?)
            """, (result.state, result.provider_sid, result.incident_id, self.recipient_key,
                  Delivery.DELIVERED, Delivery.FAILED))
        return self.lookup(result.incident_id)

    def refresh(self, incident_id: str) -> Submission | None:
        previous = self.lookup(incident_id)
        if (self.simulated or previous is None or previous.provider_sid is None
                or previous.state in {Delivery.DELIVERED, Delivery.FAILED}):
            return previous
        try:
            state = self._twilio().status(previous.provider_sid)
        except ServiceError:
            state = Delivery.UNKNOWN
        if state is Delivery.PENDING and self.now() >= previous.attempted_at + DELIVERY_WINDOW:
            state = Delivery.UNKNOWN
        return self._save_result(Submission(incident_id, state, previous.attempted_at,
                                            previous.provider_sid))

    def inject_reply(self, body: str, sid: str, received_at: datetime) -> None:
        if not self.simulated:
            raise ValueError("L’injection est réservée au canal simulé.")
        self.fake_inbox.append(Incoming(sid, received_at, self.config.recipient,
                                        self.config.sender, body))

    def poll_replies(self, since: datetime) -> list[Incoming]:
        until = self.now()
        if self.simulated:
            candidates = {item.sid: item for item in self.fake_inbox
                          if since <= item.received_at <= until}
            messages = sorted(candidates.values(), key=lambda item: (item.received_at, item.sid))
        else:
            messages = self._twilio().incoming(since, until)
        with closing(self._connect()) as db:
            return [item for item in messages if db.execute(
                "SELECT 1 FROM seen_messages WHERE sid = ?", (item.sid,)
            ).fetchone() is None]

    def acknowledge_reply(self, sid: str) -> None:
        """Acquitter après traitement. Le consommateur doit être idempotent par SID."""
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR IGNORE INTO seen_messages VALUES (?)", (sid,))
        if self.simulated:
            self.fake_inbox = [message for message in self.fake_inbox if message.sid != sid]
