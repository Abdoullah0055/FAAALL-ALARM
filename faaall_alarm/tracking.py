"""Suivi de deux personnes au plus : identifiants temporaires, sans reconnaissance faciale."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

from .config import DetectionThresholds
from .features import BodyFeatures, pose_centroid_px, pose_extent_px
from .model import PersonPose, Posture

_HISTORY_S = 8.0  # historique conservé par personne, au-delà de la fenêtre de descente
_UPRIGHT_POSTURES = (Posture.STANDING, Posture.WALKING, Posture.SITTING)


class FallState(str, Enum):
    DISARMED = "disarmed"  # pas encore de posture de départ exploitable
    ARMED = "armed"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"  # chute confirmée, en attente d'une reprise de posture


@dataclass(frozen=True)
class TrackSample:
    t: float
    features: BodyFeatures | None
    posture: Posture
    center_px: tuple[float, float]


@dataclass
class PersonTrack:
    track_id: int
    created_t: float
    thresholds: DetectionThresholds
    samples: deque[TrackSample] = field(default_factory=deque)
    reference_heights: deque[tuple[float, float]] = field(default_factory=deque)
    reference_torso_px: float = 0.0
    last_seen_t: float = 0.0
    last_center_px: tuple[float, float] = (0.0, 0.0)
    velocity_px_s: tuple[float, float] = (0.0, 0.0)
    present: bool = True
    fall_state: FallState = FallState.DISARMED
    last_fast_descent_t: float = -1e9
    # Mémoire du détecteur de chute.
    upright_since_t: float | None = None
    low_since_t: float | None = None
    recovery_since_t: float | None = None
    suspicion_started_t: float | None = None
    descent_start_t: float | None = None
    contact_t: float | None = None
    pose_lost_since_t: float | None = None
    last_posture: Posture | None = None
    lost_reported: bool = False
    reported_posture: Posture | None = None
    pending_posture: Posture | None = None
    pending_posture_since_t: float = 0.0

    @property
    def last_features(self) -> BodyFeatures | None:
        for sample in reversed(self.samples):
            if sample.features is not None:
                return sample.features
        return None

    @property
    def reference_height_px(self) -> float:
        return max((h for _, h in self.reference_heights), default=0.0)

    def scale_px(self) -> float:
        """Échelle du corps : longueur de torse de référence prise debout, jamais celle d'un torse couché."""
        return self.reference_torso_px if self.reference_torso_px > 0 else 1.0

    def record(self, t: float, features: BodyFeatures | None, posture: Posture,
               center_px: tuple[float, float]) -> None:
        dt = t - self.last_seen_t
        if self.samples and dt > 1e-6:
            self.velocity_px_s = (
                (center_px[0] - self.last_center_px[0]) / dt,
                (center_px[1] - self.last_center_px[1]) / dt,
            )
        self.samples.append(TrackSample(t, features, posture, center_px))
        while self.samples and t - self.samples[0].t > _HISTORY_S:
            self.samples.popleft()
        self.last_seen_t = t
        self.last_center_px = center_px
        self.present = True
        if features is not None and posture in _UPRIGHT_POSTURES:
            self._update_references(t, features)

    def _update_references(self, t: float, features: BodyFeatures) -> None:
        self.reference_heights.append((t, features.bbox_height_px))
        while self.reference_heights and t - self.reference_heights[0][0] > self.thresholds.reference_window_s:
            self.reference_heights.popleft()
        if features.torso_angle_deg < 30.0:
            blend = 0.1 if self.reference_torso_px > 0 else 1.0
            self.reference_torso_px += blend * (features.torso_length_px - self.reference_torso_px)

    def seed_reference(self, features: BodyFeatures | None) -> None:
        """Échelle provisoire pour une personne vue pour la première fois dans une posture non debout."""
        if features is not None and self.reference_torso_px <= 0:
            self.reference_torso_px = features.torso_length_px

    def hip_displacement_torso(self, window_s: float, now_t: float) -> tuple[float, float, float]:
        """(dx, dy, durée) du centre des hanches sur la fenêtre, en longueurs de torse."""
        current = self.samples[-1].features if self.samples else None
        if current is None:
            return 0.0, 0.0, 0.0
        for sample in self.samples:
            if sample.features is not None and now_t - sample.t <= window_s and sample.t < now_t:
                dx = current.hip_center_px[0] - sample.features.hip_center_px[0]
                dy = current.hip_center_px[1] - sample.features.hip_center_px[1]
                elapsed = max(now_t - sample.t, 1e-3)
                scale = self.scale_px()
                return dx / scale, dy / scale, elapsed
        return 0.0, 0.0, 0.0

    def predicted_center_px(self, t: float) -> tuple[float, float]:
        dt = min(t - self.last_seen_t, 0.5)
        return (
            self.last_center_px[0] + self.velocity_px_s[0] * dt,
            self.last_center_px[1] + self.velocity_px_s[1] * dt,
        )


@dataclass(frozen=True)
class Detection:
    pose: PersonPose
    features: BodyFeatures | None
    center_px: tuple[float, float]
    extent_px: float


def make_detection(pose: PersonPose, features: BodyFeatures | None,
                   frame_size_px: tuple[int, int]) -> Detection:
    center = features.body_center_px if features is not None else pose_centroid_px(pose, frame_size_px)
    return Detection(pose, features, center, pose_extent_px(pose, frame_size_px))


class PersonTracker:
    """Apparie les poses de l'image courante aux traces existantes par distance, avec prédiction de mouvement."""

    def __init__(self, thresholds: DetectionThresholds) -> None:
        self._thresholds = thresholds
        self._tracks: dict[int, PersonTrack] = {}
        self._next_id = 1

    @property
    def tracks(self) -> list[PersonTrack]:
        return sorted(self._tracks.values(), key=lambda track: track.track_id)

    def associate(self, t: float, detections: list[Detection]) -> list[tuple[PersonTrack, Detection]]:
        limit = self._thresholds.max_people
        detections = sorted(detections, key=lambda d: d.extent_px, reverse=True)[:limit]
        pairs = self._match(t, detections)
        matched_detection_ids = {id(detection) for _, detection in pairs}
        for detection in detections:
            if id(detection) in matched_detection_ids:
                continue
            track = self._new_track(t)
            if track is not None:
                pairs.append((track, detection))
        return pairs

    def _match(self, t: float, detections: list[Detection]) -> list[tuple[PersonTrack, Detection]]:
        candidates: list[tuple[float, PersonTrack, Detection]] = []
        for track in self._tracks.values():
            predicted = track.predicted_center_px(t)
            gate = self._thresholds.track_gate_torso * max(track.scale_px(), 20.0)
            for detection in detections:
                distance = math.dist(predicted, detection.center_px)
                if distance <= gate:
                    candidates.append((distance, track, detection))
        candidates.sort(key=lambda item: item[0])
        used_tracks: set[int] = set()
        used_detections: set[int] = set()
        pairs: list[tuple[PersonTrack, Detection]] = []
        for _, track, detection in candidates:
            if track.track_id in used_tracks or id(detection) in used_detections:
                continue
            used_tracks.add(track.track_id)
            used_detections.add(id(detection))
            pairs.append((track, detection))
        return pairs

    def _new_track(self, t: float) -> PersonTrack | None:
        if len(self._tracks) >= self._thresholds.max_people:
            # Remplace la trace perdue la plus ancienne, sauf une personne tombée encore en mémoire.
            replaceable = [
                track for track in self._tracks.values()
                if not track.present and track.fall_state is not FallState.CONFIRMED
            ]
            if not replaceable:
                return None
            del self._tracks[min(replaceable, key=lambda track: track.last_seen_t).track_id]
        track = PersonTrack(self._next_id, t, self._thresholds)
        self._next_id += 1
        self._tracks[track.track_id] = track
        return track

    def expire(self, t: float) -> list[PersonTrack]:
        """Oublie les traces perdues depuis trop longtemps. Retourne les traces oubliées."""
        forgotten = []
        for track in list(self._tracks.values()):
            memory = (
                self._thresholds.confirmed_track_memory_s
                if track.fall_state is FallState.CONFIRMED
                else self._thresholds.track_memory_s
            )
            if t - track.last_seen_t > memory:
                forgotten.append(self._tracks.pop(track.track_id))
        return forgotten
