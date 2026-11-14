"""Masquage des données sensibles avant tout affichage, copie ou enregistrement."""

from __future__ import annotations

import re
import threading

MASK = "[masqué]"

# Numéros internationaux ou nationaux (au moins 8 chiffres, séparateurs usuels tolérés).
_PHONE = re.compile(r"(?<![\w.])\+?\d[\d\s().-]{6,}\d(?![\w])")
# Identifiants Twilio (AC…, SK…, SM…, MM…) et clés de type sk-…
_TOKENS = re.compile(r"\b(?:AC|SK|SM|MM)[0-9a-fA-F]{32}\b|\bsk-[A-Za-z0-9_-]{16,}\b")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b(token|api[_-]?key|auth[_-]?token|password|mot de passe|secret)\b(\s*[:=]\s*)(\S+)"
)


class SensitiveMasker:
    """Masque les motifs génériques et les valeurs sensibles enregistrées (adresse, numéros, clés)."""

    def __init__(self) -> None:
        self._known_values: list[str] = []
        self._lock = threading.Lock()

    def register(self, value: str | None) -> None:
        cleaned = (value or "").strip()
        if len(cleaned) < 4:
            return
        with self._lock:
            if cleaned not in self._known_values:
                self._known_values.append(cleaned)
                self._known_values.sort(key=len, reverse=True)

    def clear_registered(self) -> None:
        with self._lock:
            self._known_values.clear()

    def mask(self, text: str) -> str:
        masked = text
        with self._lock:
            known_values = list(self._known_values)
        for value in known_values:
            masked = masked.replace(value, MASK)
        masked = _TOKENS.sub(MASK, masked)
        masked = _SECRET_ASSIGNMENT.sub(lambda m: f"{m.group(1)}{m.group(2)}{MASK}", masked)
        return _PHONE.sub(MASK, masked)


_DEFAULT_MASKER = SensitiveMasker()


def default_masker() -> SensitiveMasker:
    return _DEFAULT_MASKER


def mask_sensitive(text: str) -> str:
    return _DEFAULT_MASKER.mask(text)
