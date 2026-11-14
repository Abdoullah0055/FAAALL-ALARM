"""Horloges injectables : l'horloge monotone pilote les délais, l'heure civile sert à l'affichage."""

from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    def monotonic(self) -> float:
        """Secondes depuis un point arbitraire, jamais en recul. Sert aux délais."""

    def wall(self) -> datetime:
        """Heure civile locale. Réservée à l'affichage et à l'historique."""


class SystemClock:
    def monotonic(self) -> float:
        return time.monotonic()

    def wall(self) -> datetime:
        return datetime.now()


class ManualClock:
    """Horloge contrôlable pour les tests et la démo accélérée."""

    def __init__(self, start_monotonic_s: float = 0.0, start_wall: datetime | None = None) -> None:
        self._monotonic_s = start_monotonic_s
        self._start_monotonic_s = start_monotonic_s
        self._start_wall = start_wall or datetime(2026, 1, 15, 14, 32, 0)

    def monotonic(self) -> float:
        return self._monotonic_s

    def wall(self) -> datetime:
        return self._start_wall + timedelta(seconds=self._monotonic_s - self._start_monotonic_s)

    def advance(self, seconds: float) -> None:
        if seconds < 0:
            raise ValueError("Une horloge monotone ne recule pas.")
        self._monotonic_s += seconds

    def advance_to(self, monotonic_s: float) -> None:
        if monotonic_s > self._monotonic_s:
            self._monotonic_s = monotonic_s
