"""Indices de posture calculés à partir d'une pose MediaPipe, en pixels et à l'échelle du corps.

Chaque point dont la visibilité est sous `min_landmark_visibility` est écarté : les centres, la
boîte englobante et les indices des jambes ne reposent que sur des points fiables. Un point hors
cadre ou masqué ne peut donc ni déplacer un centre ni raccourcir artificiellement la silhouette.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import DetectionThresholds
from .model import (
    LEFT_ANKLE, LEFT_HIP, LEFT_KNEE, LEFT_SHOULDER, RIGHT_ANKLE, RIGHT_HIP, RIGHT_KNEE, RIGHT_SHOULDER,
    PersonPose,
)

_CORE = (LEFT_SHOULDER, RIGHT_SHOULDER, LEFT_HIP, RIGHT_HIP)


@dataclass(frozen=True)
class BodyFeatures:
    """Indices exploitables d'une personne sur une image. Coordonnées en pixels, y vers le bas."""

    core_visibility: float
    hip_center_px: tuple[float, float]
    shoulder_center_px: tuple[float, float]
    torso_length_px: float
    torso_angle_deg: float  # 0 : torse vertical, 90 : torse horizontal
    bbox_px: tuple[float, float, float, float]  # x0, y0, x1, y1 des points visibles
    thigh_vertical_ratio: float | None  # (genou - hanche) vertical / torse, None si genoux masqués
    ankle_center_px: tuple[float, float] | None = None  # None si les chevilles sont masquées
    ankle_offset_px: tuple[float, float] | None = None  # cheville gauche - droite, si les deux sont visibles
    height_reliable: bool = True  # pieds visibles et tête non coupée par le haut ou les côtés de l'image

    @property
    def bbox_height_px(self) -> float:
        return self.bbox_px[3] - self.bbox_px[1]

    @property
    def bbox_aspect(self) -> float:
        height = max(self.bbox_height_px, 1e-6)
        return (self.bbox_px[2] - self.bbox_px[0]) / height

    @property
    def legs_visible(self) -> bool:
        return self.thigh_vertical_ratio is not None

    @property
    def torso_center_px(self) -> tuple[float, float]:
        """Centre du torse : ne bouge pas quand les jambes sortent du cadre ou sont masquées."""
        return (
            (self.hip_center_px[0] + self.shoulder_center_px[0]) / 2.0,
            (self.hip_center_px[1] + self.shoulder_center_px[1]) / 2.0,
        )

    @property
    def body_center_px(self) -> tuple[float, float]:
        return (
            (self.bbox_px[0] + self.bbox_px[2]) / 2.0,
            (self.bbox_px[1] + self.bbox_px[3]) / 2.0,
        )


def _visible_mean(points: np.ndarray, visible: np.ndarray, indices: tuple[int, int]) -> np.ndarray | None:
    chosen = [index for index in indices if visible[index]]
    return points[chosen].mean(axis=0) if chosen else None


def compute_features(
    pose: PersonPose, frame_size_px: tuple[int, int], thresholds: DetectionThresholds
) -> BodyFeatures | None:
    """Retourne None si aucune épaule ou aucune hanche n'est fiable (pose inexploitable)."""
    landmarks = pose.landmarks
    width, height = frame_size_px
    visible = landmarks[:, 2] >= thresholds.min_landmark_visibility
    core_visibility = float(np.mean(landmarks[list(_CORE), 2]))
    if core_visibility < thresholds.min_core_visibility:
        return None

    points = landmarks[:, :2] * np.array([width, height], dtype=float)
    shoulder_center = _visible_mean(points, visible, (LEFT_SHOULDER, RIGHT_SHOULDER))
    hip_center = _visible_mean(points, visible, (LEFT_HIP, RIGHT_HIP))
    if shoulder_center is None or hip_center is None:
        return None
    torso_vector = hip_center - shoulder_center
    torso_length = float(np.linalg.norm(torso_vector))
    if torso_length < 4.0:  # torse quasi invisible : le calcul d'angle n'aurait pas de sens
        return None
    torso_angle = math.degrees(math.atan2(abs(torso_vector[0]), abs(torso_vector[1])))

    visible_points = points[visible]
    bbox = (
        float(visible_points[:, 0].min()), float(visible_points[:, 1].min()),
        float(visible_points[:, 0].max()), float(visible_points[:, 1].max()),
    )

    thigh_ratio = None
    knee_center = _visible_mean(points, visible, (LEFT_KNEE, RIGHT_KNEE))
    if knee_center is not None:
        thigh_ratio = float((knee_center[1] - hip_center[1]) / torso_length)
    ankle_center = _visible_mean(points, visible, (LEFT_ANKLE, RIGHT_ANKLE))
    ankle_offset = None
    if visible[LEFT_ANKLE] and visible[RIGHT_ANKLE]:
        ankle_offset = points[LEFT_ANKLE] - points[RIGHT_ANKLE]

    # Des chevilles visibles garantissent le bas de la silhouette, même au ras du bord inférieur ;
    # un corps coupé en haut ou sur un côté a en revanche une hauteur apparente tronquée.
    margin_x, margin_y = thresholds.frame_edge_margin_ratio * width, thresholds.frame_edge_margin_ratio * height
    touches_edge = bbox[0] <= margin_x or bbox[1] <= margin_y or bbox[2] >= width - margin_x
    return BodyFeatures(
        core_visibility=core_visibility,
        hip_center_px=(float(hip_center[0]), float(hip_center[1])),
        shoulder_center_px=(float(shoulder_center[0]), float(shoulder_center[1])),
        torso_length_px=torso_length,
        torso_angle_deg=torso_angle,
        bbox_px=bbox,
        thigh_vertical_ratio=thigh_ratio,
        ankle_center_px=(float(ankle_center[0]), float(ankle_center[1])) if ankle_center is not None else None,
        ankle_offset_px=(float(ankle_offset[0]), float(ankle_offset[1])) if ankle_offset is not None else None,
        height_reliable=ankle_center is not None and not touches_edge,
    )


def pose_centroid_px(pose: PersonPose, frame_size_px: tuple[int, int]) -> tuple[float, float]:
    """Centre pondéré par la visibilité, utilisé pour suivre une pose dont les indices sont inexploitables."""
    weights = pose.landmarks[:, 2] + 1e-3
    xy = pose.landmarks[:, :2] * np.array(frame_size_px, dtype=float)
    center = (xy * weights[:, None]).sum(axis=0) / weights.sum()
    return float(center[0]), float(center[1])


def pose_bbox_px(pose: PersonPose, frame_size_px: tuple[int, int],
                 min_visibility: float) -> tuple[float, float, float, float]:
    """Boîte des points visibles (tous les points si aucun ne l'est)."""
    xy = pose.landmarks[:, :2] * np.array(frame_size_px, dtype=float)
    visible = pose.landmarks[:, 2] >= min_visibility
    chosen = xy[visible] if visible.any() else xy
    return (float(chosen[:, 0].min()), float(chosen[:, 1].min()),
            float(chosen[:, 0].max()), float(chosen[:, 1].max()))


def pose_extent_px(pose: PersonPose, frame_size_px: tuple[int, int], min_visibility: float = 0.0) -> float:
    """Taille approximative du corps (diagonale de la boîte des points visibles)."""
    x0, y0, x1, y1 = pose_bbox_px(pose, frame_size_px, min_visibility)
    return math.hypot(x1 - x0, y1 - y0)
