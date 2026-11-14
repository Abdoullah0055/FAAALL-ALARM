"""Journal de détection de la session et historique local des incidents, sans données sensibles."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .clock import Clock, SystemClock
from .privacy import SensitiveMasker, default_masker


@dataclass(frozen=True)
class LogEntry:
    wall: datetime
    message: str
    video_time_s: float | None = None  # temps dans le fichier en lecture MP4


def format_video_time(seconds: float) -> str:
    minutes, remainder = divmod(seconds, 60.0)
    return f"{int(minutes):02d}:{remainder:04.1f}"


def format_entry(entry: LogEntry) -> str:
    video = f" [vidéo {format_video_time(entry.video_time_s)}]" if entry.video_time_s is not None else ""
    return f"{entry.wall:%H:%M:%S}{video} {entry.message}"


class DetectionLog:
    """Entrées chronologiques de la session entière. « Tout copier » exporte tout, pas seulement le visible."""

    def __init__(self, clock: Clock | None = None, masker: SensitiveMasker | None = None) -> None:
        self._clock = clock or SystemClock()
        self._masker = masker or default_masker()
        self._entries: list[LogEntry] = []
        self._lock = threading.Lock()

    def add(self, message: str, video_time_s: float | None = None) -> None:
        entry = LogEntry(self._clock.wall(), self._masker.mask(message), video_time_s)
        with self._lock:
            self._entries.append(entry)

    def entries(self, start: int = 0) -> list[LogEntry]:
        with self._lock:
            return self._entries[start:]

    def __len__(self) -> int:
        with self._lock:
            return len(self._entries)

    def export_text(self) -> str:
        """Texte complet de la session, horodaté et masqué (y compris des valeurs sensibles enregistrées depuis)."""
        return "\n".join(self._masker.mask(format_entry(entry)) for entry in self.entries())


class HistoryStore:
    """Historique local en JSON Lines : événements, issues, statuts. Jamais le texte brut des réponses."""

    def __init__(self, path: Path, mode: str, clock: Clock | None = None,
                 masker: SensitiveMasker | None = None) -> None:
        self._path = path
        self.mode = mode  # « simulé », « réel » ou « test »
        self._clock = clock or SystemClock()
        self._masker = masker or default_masker()
        self._lock = threading.Lock()

    def record(self, event: str, durable: bool = False, **fields: object) -> None:
        payload = {"heure": self._clock.wall().isoformat(timespec="seconds"), "mode": self.mode, "evenement": event}
        for key, value in fields.items():
            payload[key] = self._masker.mask(value) if isinstance(value, str) else value
        line = json.dumps(payload, ensure_ascii=False)
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                if durable:
                    handle.flush()
                    os.fsync(handle.fileno())

    def read(self) -> list[dict[str, object]]:
        if not self._path.is_file():
            return []
        with self._lock:
            lines = self._path.read_text(encoding="utf-8").splitlines()
        records = []
        for line in lines:
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def clear(self) -> None:
        with self._lock:
            if self._path.is_file():
                self._path.unlink()
