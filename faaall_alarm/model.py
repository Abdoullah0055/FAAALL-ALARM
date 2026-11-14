"""Types partagés entre la capture, la détection, les incidents et l'interface."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

import numpy as np

# Indices MediaPipe Pose (33 points). Seuls ceux utilisés par la détection sont nommés.
NOSE = 0
LEFT_SHOULDER, RIGHT_SHOULDER = 11, 12
LEFT_ELBOW, RIGHT_ELBOW = 13, 14
LEFT_WRIST, RIGHT_WRIST = 15, 16
LEFT_HIP, RIGHT_HIP = 23, 24
LEFT_KNEE, RIGHT_KNEE = 25, 26
LEFT_ANKLE, RIGHT_ANKLE = 27, 28
LANDMARK_COUNT = 33

# Segments dessinés pour l'affichage optionnel du squelette.
SKELETON_SEGMENTS = (
    (LEFT_SHOULDER, RIGHT_SHOULDER), (LEFT_SHOULDER, LEFT_ELBOW), (LEFT_ELBOW, LEFT_WRIST),
    (RIGHT_SHOULDER, RIGHT_ELBOW), (RIGHT_ELBOW, RIGHT_WRIST), (LEFT_SHOULDER, LEFT_HIP),
    (RIGHT_SHOULDER, RIGHT_HIP), (LEFT_HIP, RIGHT_HIP), (LEFT_HIP, LEFT_KNEE),
    (LEFT_KNEE, LEFT_ANKLE), (RIGHT_HIP, RIGHT_KNEE), (RIGHT_KNEE, RIGHT_ANKLE),
    (NOSE, LEFT_SHOULDER), (NOSE, RIGHT_SHOULDER),
)


class Posture(str, Enum):
    """Libellés de posture repris des vidéos de référence."""

    STANDING = "STANDING"
    WALKING = "WALKING"
    SITTING = "SITTING"
    LYING = "LYING"
    FALLING = "FALLING"
    INDETERMINATE = "Posture indéterminée"
    NO_PERSON = "Aucune personne détectée"


# Ordre d'importance pour choisir la posture affichée en grand quand deux personnes sont suivies.
POSTURE_DISPLAY_PRIORITY = (
    Posture.FALLING, Posture.LYING, Posture.SITTING, Posture.WALKING, Posture.STANDING,
    Posture.INDETERMINATE, Posture.NO_PERSON,
)


class FallCause(str, Enum):
    INDIVIDUAL = "Chute individuelle"
    COLLISION = "Collision probable · chute possible"
    UNDETERMINED = "Cause indéterminée"


class SourceKind(str, Enum):
    WEBCAM = "webcam"
    VIDEO_FILE = "video"
    SIMULATION = "simulation"


class SourceStatus(str, Enum):
    ACTIVE = "active"
    ENDED = "ended"  # fin d'un fichier : distincte d'une perte de caméra
    LOST = "lost"  # la source ne fournit plus d'images


@dataclass(frozen=True)
class PersonPose:
    """Pose d'une personne : tableau (33, 3) de x, y normalisés (0 à 1) et visibilité (0 à 1)."""

    landmarks: np.ndarray

    def __post_init__(self) -> None:
        if self.landmarks.shape != (LANDMARK_COUNT, 3):
            raise ValueError(f"Attendu (33, 3), reçu {self.landmarks.shape}.")


@dataclass(frozen=True)
class FrameObservation:
    """Une image analysée. `timestamp_s` vient du fichier en MP4 et de l'horloge monotone en direct."""

    timestamp_s: float
    frame_size_px: tuple[int, int]  # (largeur, hauteur)
    poses: tuple[PersonPose, ...] = ()


class EventKind(str, Enum):
    POSTURE_CHANGED = "posture"
    TRACK_LOST = "track_lost"
    TRACK_RESUMED = "track_resumed"
    FALL_SUSPECTED = "fall_suspected"
    SUSPICION_CLEARED = "suspicion_cleared"
    FALL_CONFIRMED = "fall_confirmed"
    PERSON_RECOVERED = "person_recovered"
    COLLISION_DETECTED = "collision"
    COLLISION_WITHOUT_FALL = "collision_without_fall"
    CROSSING_IGNORED = "crossing_ignored"
    RESTING_ZONE = "resting_zone"


@dataclass(frozen=True)
class DetectionEvent:
    """Décision du détecteur, destinée au moteur d'incident et au journal."""

    kind: EventKind
    timestamp_s: float  # temps de l'image (fichier ou monotone)
    track_ids: tuple[int, ...] = ()
    cause: FallCause | None = None
    posture: Posture | None = None
    detail: str = ""
    contact_timestamp_s: float | None = None  # instant du contact avec le sol ou entre personnes


@dataclass(frozen=True)
class PersonView:
    """Ce que l'interface affiche pour une personne suivie."""

    track_id: int
    label: str  # « Personne », « Personne 1 » ou « Personne 2 »
    posture: Posture
    landmarks: np.ndarray | None = None


@dataclass
class FrameAnalysis:
    """Résultat complet de l'analyse d'une image."""

    timestamp_s: float
    persons: list[PersonView] = field(default_factory=list)
    events: list[DetectionEvent] = field(default_factory=list)
