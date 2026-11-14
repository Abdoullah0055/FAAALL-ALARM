"""Diagnostic local de la vision, désactivé par défaut.

Écrit un enregistrement JSON par personne et par image analysée : points fiables, indices calculés
(angle et longueur du torse, rapport de hauteur, hanches au-dessus des chevilles), état du
détecteur, changements de piste et événements. Aucune image ni coordonnée brute n'est écrite.
Le fichier reste local (dossier `artifacts/`, ignoré par Git) et sert à relire une erreur image par image.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path


class DiagnosticWriter:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("w", encoding="utf-8")
        self._lock = threading.Lock()

    def __call__(self, record: dict) -> None:
        with self._lock:
            self._file.write(json.dumps(record, ensure_ascii=False) + "\n")

    def close(self) -> None:
        with self._lock:
            self._file.close()
