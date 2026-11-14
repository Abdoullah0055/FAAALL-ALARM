"""Indices de posture calculés à partir d'une pose MediaPipe, en pixels et à l'échelle du corps."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .config import DetectionThresholds
from .model import (
    LEFT_HIP, LEFT_KNEE, LEFT_SHOULDER, RIGHT_HIP, RIGHT_KNEE, RIGHT_SHOULDER, PersonPose,
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

    @property
    def bbox_height_px(self) -> float:
        return self.bbox_px[3] - self.bbox_px[1]

    @property
    def bbox_aspect(self) -> float:
        height = max(self.bbox_height_px, 1e-6)
        return (self.bbox_px[2] - self.bbox_px[0]) / height

    @property
    def body_center_px(self) -> tuple[float, float]:
        return (
            (self.bbox_px[0] + self.bbox_px[2]) / 2.0,
            (self.bbox_px[1] + self.bbox_px[3]) / 2.0,
        )


def compute_features(
    pose: PersonPose, frame_size_px: tuple[int, int], thresholds: DetectionThresholds
) -> BodyFeatures | None:
    """Retourne None si les épaules et les hanches ne sont pas assez visibles (pose inexploitable)."""
    landmarks = pose.landmarks
    width, height = frame_size_px
    core_visibility = float(np.mean(landmarks[list(_CORE), 2]))
    if core_visibility < thresholds.min_core_visibility:
        return None

    points = landmarks[:, :2] * np.array([width, height], dtype=float)
    shoulder_center = points[[LEFT_SHOULDER, RIGHT_SHOULDER]].mean(axis=0)
    hip_center = points[[LEFT_HIP, RIGHT_HIP]].mean(axis=0)
    torso_vector = hip_center - shoulder_center
    torso_length = float(np.linalg.norm(torso_vector))
    if torso_length < 4.0:  # torse quasi invisible : le calcul d'angle n'aurait pas de sens
        return None
    torso_angle = math.degrees(math.atan2(abs(torso_vector[0]), abs(torso_vector[1])))

    visible = landmarks[:, 2] >= thresholds.min_landmark_visibility
    visible_points = points[visible] if visible.any() else points[list(_CORE)]
    bbox = (
        float(visible_points[:, 0].min()), float(visible_points[:, 1].min()),
        float(visible_points[:, 0].max()), float(visible_points[:, 1].max()),
    )

    thigh_ratio = None
    leg_visibility = float(np.mean(landmarks[[LEFT_KNEE, RIGHT_KNEE], 2]))
    if leg_visibility >= thresholds.min_landmark_visibility:
        knee_center = points[[LEFT_KNEE, RIGHT_KNEE]].mean(axis=0)
        thigh_ratio = float((knee_center[1] - hip_center[1]) / torso_length)

    return BodyFeatures(
        core_visibility=core_visibility,
        hip_center_px=(float(hip_center[0]), float(hip_center[1])),
        shoulder_center_px=(float(shoulder_center[0]), float(shoulder_center[1])),
        torso_length_px=torso_length,
        torso_angle_deg=torso_angle,
        bbox_px=bbox,
        thigh_vertical_ratio=thigh_ratio,
    )


def pose_centroid_px(pose: PersonPose, frame_size_px: tuple[int, int]) -> tuple[float, float]:
    """Centre pondéré par la visibilité, utilisé pour suivre une pose dont les indices sont inexploitables."""
    weights = pose.landmarks[:, 2] + 1e-3
    xy = pose.landmarks[:, :2] * np.array(frame_size_px, dtype=float)
    center = (xy * weights[:, None]).sum(axis=0) / weights.sum()
    return float(center[0]), float(center[1])


def pose_extent_px(pose: PersonPose, frame_size_px: tuple[int, int]) -> float:
    """Taille approximative du corps (diagonale de la boîte englobante), pour fixer une échelle de repli."""
    xy = pose.landmarks[:, :2] * np.array(frame_size_px, dtype=float)
    return float(np.linalg.norm(xy.max(axis=0) - xy.min(axis=0)))
