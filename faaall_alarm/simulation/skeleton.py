"""Mannequin à 33 points MediaPipe, piloté par quelques angles, pour produire des poses synthétiques."""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from ..model import (
    LANDMARK_COUNT, LEFT_ANKLE, LEFT_ELBOW, LEFT_HIP, LEFT_KNEE, LEFT_SHOULDER, LEFT_WRIST, NOSE,
    RIGHT_ANKLE, RIGHT_ELBOW, RIGHT_HIP, RIGHT_KNEE, RIGHT_SHOULDER, RIGHT_WRIST, PersonPose,
)

# Proportions relatives à la longueur du torse T (épaules-hanches).
SHOULDER_HALF_WIDTH, HIP_HALF_WIDTH = 0.275, 0.2
UPPER_ARM, FOREARM, THIGH, SHIN, HEAD = 0.5, 0.45, 0.9, 0.95, 0.45
STANDING_HIP_HEIGHT = THIGH + SHIN  # hauteur des hanches debout, en T
HORIZON_Y_RATIO = 0.42  # ligne d'horizon de la caméra simulée, en fraction de la hauteur d'image


@dataclass(frozen=True)
class BodyState:
    """État du corps dans l'image. Les angles sont relatifs à la direction `facing` (+1 droite, -1 gauche)."""

    x_px: float
    hip_height_t: float = STANDING_HIP_HEIGHT  # hauteur des hanches au-dessus du sol, en T
    torso_deg: float = 0.0  # 0 : vertical, 90 : couché vers `facing`
    thigh_deg: float = 0.0  # 0 : cuisse vers le bas, 90 : cuisse horizontale vers `facing`
    knee_flex_deg: float = 0.0
    gait_deg: float = 0.0  # amplitude du balancement des jambes en marchant
    facing: int = 1
    visibility: float = 0.95
    lower_body_visible: bool = True
    depth_scale: float = 1.0  # taille apparente : > 1 quand la personne s'approche de la caméra
    torso_scale: float = 1.0  # raccourci du torse quand il pointe vers la caméra (chute vers la caméra)
    thigh_scale: float = 1.0  # raccourci des cuisses vues de face (assise face à la caméra)
    shin_scale: float = 1.0  # raccourci des tibias (jambes tendues vers le fond de la pièce)


STANDING = BodyState(x_px=0.0)
SITTING = replace(STANDING, hip_height_t=1.0, thigh_deg=90.0, knee_flex_deg=90.0)
LYING = replace(STANDING, hip_height_t=0.12, torso_deg=90.0, thigh_deg=-90.0)
CROUCHING = replace(STANDING, hip_height_t=0.85, torso_deg=35.0, thigh_deg=70.0, knee_flex_deg=120.0)
BENDING = replace(STANDING, torso_deg=75.0)


def blend(first: BodyState, second: BodyState, ratio: float) -> BodyState:
    """Interpolation douce entre deux états."""
    eased = ratio * ratio * (3 - 2 * ratio)

    def mix(a: float, b: float) -> float:
        return a + (b - a) * eased

    return BodyState(
        x_px=first.x_px + (second.x_px - first.x_px) * ratio,
        hip_height_t=mix(first.hip_height_t, second.hip_height_t),
        torso_deg=mix(first.torso_deg, second.torso_deg),
        thigh_deg=mix(first.thigh_deg, second.thigh_deg),
        knee_flex_deg=mix(first.knee_flex_deg, second.knee_flex_deg),
        gait_deg=mix(first.gait_deg, second.gait_deg),
        facing=second.facing,
        visibility=mix(first.visibility, second.visibility),
        lower_body_visible=first.lower_body_visible and second.lower_body_visible,
        depth_scale=mix(first.depth_scale, second.depth_scale),
        torso_scale=mix(first.torso_scale, second.torso_scale),
        thigh_scale=mix(first.thigh_scale, second.thigh_scale),
        shin_scale=mix(first.shin_scale, second.shin_scale),
    )


def _direction_down(angle_deg: float, facing: int) -> np.ndarray:
    radians = math.radians(angle_deg * facing)
    return np.array([math.sin(radians), math.cos(radians)])


def build_pose(
    state: BodyState, ground_y_px: float, torso_px: float, frame_size_px: tuple[int, int], phase: float = 0.0
) -> PersonPose:
    """Calcule les 33 points d'un mannequin et les normalise par la taille de l'image.

    Les points qui sortent de l'image reçoivent une visibilité faible, comme MediaPipe le fait pour
    un membre hors cadre. `depth_scale` rapproche la personne : elle grandit et ses pieds descendent
    dans l'image (caméra au-dessus du sol, horizon à `HORIZON_Y_RATIO` de la hauteur).
    """
    facing = state.facing
    ground_y_px = HORIZON_Y_RATIO * frame_size_px[1] + (ground_y_px - HORIZON_Y_RATIO * frame_size_px[1]) * state.depth_scale
    torso_px = torso_px * state.depth_scale
    radians = math.radians(state.torso_deg * facing)
    up = np.array([math.sin(radians), -math.cos(radians)])
    lateral = np.array([math.cos(radians), math.sin(radians)])
    hip_center = np.array([state.x_px, ground_y_px - state.hip_height_t * torso_px])
    shoulder_center = hip_center + torso_px * state.torso_scale * up
    points = np.zeros((LANDMARK_COUNT, 2))

    left_hip = hip_center - HIP_HALF_WIDTH * torso_px * lateral
    right_hip = hip_center + HIP_HALF_WIDTH * torso_px * lateral
    left_shoulder = shoulder_center - SHOULDER_HALF_WIDTH * torso_px * lateral
    right_shoulder = shoulder_center + SHOULDER_HALF_WIDTH * torso_px * lateral
    points[LEFT_HIP], points[RIGHT_HIP] = left_hip, right_hip
    points[LEFT_SHOULDER], points[RIGHT_SHOULDER] = left_shoulder, right_shoulder
    points[NOSE] = shoulder_center + HEAD * torso_px * up

    arm_direction = -up
    for shoulder, elbow, wrist in (
        (left_shoulder, LEFT_ELBOW, LEFT_WRIST), (right_shoulder, RIGHT_ELBOW, RIGHT_WRIST),
    ):
        elbow_point = shoulder + UPPER_ARM * torso_px * arm_direction
        points[elbow] = elbow_point
        points[wrist] = elbow_point + FOREARM * torso_px * arm_direction

    swing = state.gait_deg * math.sin(phase)
    for hip, knee, ankle, offset in (
        (left_hip, LEFT_KNEE, LEFT_ANKLE, swing), (right_hip, RIGHT_KNEE, RIGHT_ANKLE, -swing),
    ):
        thigh_angle = state.thigh_deg + offset
        knee_point = hip + THIGH * state.thigh_scale * torso_px * _direction_down(thigh_angle, facing)
        shin_angle = thigh_angle - state.knee_flex_deg
        points[knee] = knee_point
        points[ankle] = knee_point + SHIN * state.shin_scale * torso_px * _direction_down(shin_angle, facing)

    # Les points secondaires (yeux, doigts, talons…) suivent l'articulation voisine.
    for index in range(LANDMARK_COUNT):
        if not points[index].any():
            neighbour = NOSE if index <= 10 else (LEFT_WRIST if index in (17, 19, 21) else
                        RIGHT_WRIST if index in (18, 20, 22) else
                        LEFT_ANKLE if index in (27, 29, 31) else RIGHT_ANKLE)
            points[index] = points[neighbour]

    visibility = np.full(LANDMARK_COUNT, state.visibility)
    outside = (points[:, 0] < 0) | (points[:, 0] >= frame_size_px[0]) | (points[:, 1] < 0) | (points[:, 1] >= frame_size_px[1])
    visibility[outside] = 0.05
    if not state.lower_body_visible:
        visibility[[LEFT_KNEE, RIGHT_KNEE, LEFT_ANKLE, RIGHT_ANKLE, 29, 30, 31, 32]] = 0.05
    normalized = points / np.array(frame_size_px, dtype=float)
    return PersonPose(np.column_stack([normalized, visibility]))
