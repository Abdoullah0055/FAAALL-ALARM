"""Classement de la posture actuelle d'une personne suivie."""

from __future__ import annotations

from .config import DetectionThresholds
from .features import BodyFeatures
from .model import Posture
from .tracking import FallState, PersonTrack


def is_low_arrival(features: BodyFeatures, track: PersonTrack, thresholds: DetectionThresholds) -> bool:
    """Posture basse d'arrivée : torse très incliné ou hauteur apparente fortement réduite."""
    if features.torso_angle_deg >= thresholds.low_torso_angle_deg:
        return True
    reference_height = track.reference_height_px
    return reference_height > 0 and features.bbox_height_px / reference_height <= thresholds.low_height_ratio


def is_upright(features: BodyFeatures, track: PersonTrack, thresholds: DetectionThresholds) -> bool:
    """Posture de reprise : torse redressé et hauteur apparente retrouvée."""
    if features.torso_angle_deg >= thresholds.recovery_torso_angle_deg:
        return False
    reference_height = track.reference_height_px
    if reference_height <= 0:
        return True
    return features.bbox_height_px / reference_height >= thresholds.recovery_height_ratio


def _is_lying(features: BodyFeatures, track: PersonTrack, thresholds: DetectionThresholds) -> bool:
    """Couché : torse horizontal ET corps aplati. Se pencher en avant garde les jambes verticales."""
    reference_height = track.reference_height_px
    flattened = reference_height <= 0 or (
        features.bbox_height_px / reference_height <= thresholds.lying_height_ratio
    )
    if features.torso_angle_deg >= thresholds.lying_torso_angle_deg and flattened:
        return True
    return (
        features.bbox_aspect >= thresholds.lying_bbox_aspect
        and features.torso_angle_deg >= thresholds.low_torso_angle_deg - 15.0
    )


def classify_posture(
    features: BodyFeatures | None, track: PersonTrack, t: float, thresholds: DetectionThresholds
) -> Posture:
    if features is None:
        return Posture.INDETERMINATE
    if track.fall_state is FallState.SUSPECTED:
        return Posture.FALLING

    _, vertical_shift, duration = track.hip_displacement_torso(0.5, t)
    descent_speed = vertical_shift / duration if duration > 0 else 0.0
    if descent_speed >= thresholds.falling_descent_speed_torso_per_s:
        track.last_fast_descent_t = t
    if t - track.last_fast_descent_t <= thresholds.falling_label_hold_s:
        return Posture.FALLING

    if _is_lying(features, track, thresholds):
        return Posture.LYING
    thigh_ratio = features.thigh_vertical_ratio
    if thigh_ratio is not None and thigh_ratio < thresholds.sitting_thigh_vertical_ratio:
        return Posture.SITTING
    horizontal_shift, _, duration = track.hip_displacement_torso(0.5, t)
    horizontal_speed = abs(horizontal_shift) / duration if duration > 0 else 0.0
    if horizontal_speed >= thresholds.walking_speed_torso_per_s:
        return Posture.WALKING
    return Posture.STANDING
