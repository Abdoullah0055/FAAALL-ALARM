"""Classement de la posture actuelle d'une personne suivie.

`classify_posture` donne le libellé instantané d'une image ; `PostureSmoother` décide du libellé
affiché, qui ne change qu'après une stabilité minimale (FALLING s'affiche sans délai). Les deux
restent indépendants de l'état d'incident.
"""

from __future__ import annotations

from .config import DetectionThresholds
from .features import BodyFeatures
from .model import Posture
from .tracking import FallState, PersonTrack


def is_low_arrival(features: BodyFeatures, track: PersonTrack, thresholds: DetectionThresholds) -> bool:
    """Posture basse d'arrivée.

    Se pencher pour ramasser un objet bascule le torse mais garde les jambes sous le corps : un torse
    basculé ne suffit que si la silhouette est aussi nettement raccourcie. Une hauteur fortement
    réduite suffit seule (chute vers la caméra, torse raccourci mais presque vertical), de même
    qu'un torse nettement plus court que sa référence redressée. Si la
    hauteur n'est pas mesurable (pieds hors cadre), seul un torse très incliné compte. Pieds
    visibles, des hanches encore loin au-dessus des chevilles (accroupi, à genoux, en train de
    ramasser un objet) excluent une arrivée au sol.
    """
    hip_above = track.hip_above_ankles_torso(features)
    if hip_above is not None and hip_above > thresholds.low_max_hip_above_ankles_torso:
        return False
    if (track.reference_torso_px > 0
            and features.torso_length_px <= thresholds.low_torso_foreshortening_ratio * track.reference_torso_px):
        return True  # torse très raccourci : allongé dans l'axe de la caméra, l'angle ne dit plus rien
    ratio = track.height_ratio(features)
    angle = features.torso_angle_deg
    if ratio is None:
        return angle >= thresholds.low_torso_angle_no_height_deg
    if ratio <= thresholds.low_height_ratio:
        return True
    return angle >= thresholds.low_torso_angle_deg and ratio <= thresholds.low_bent_height_ratio


def stays_low(features: BodyFeatures, track: PersonTrack, thresholds: DetectionThresholds) -> bool:
    """Maintien au sol, une fois l'arrivée basse constatée : critère plus large que l'entrée.

    Au sol, MediaPipe estime mal l'angle du torse et perd souvent les chevilles ; exiger à chaque
    image les indices d'entrée remettrait le maintien à zéro sur une image bruitée. La personne
    reste « basse » tant que le torse reste incliné ou la silhouette raccourcie, sauf si les
    hanches sont nettement au-dessus des chevilles (accroupie ou relevée).
    """
    if is_low_arrival(features, track, thresholds):
        return True
    hip_above = track.hip_above_ankles_torso(features)
    if hip_above is not None and hip_above > thresholds.low_max_hip_above_ankles_torso:
        return False
    ratio = track.height_ratio(features)
    return (features.torso_angle_deg >= thresholds.stay_low_torso_angle_deg
            or (ratio is not None and ratio <= thresholds.low_bent_height_ratio))


def is_upright(features: BodyFeatures, track: PersonTrack, thresholds: DetectionThresholds) -> bool:
    """Torse redressé, de longueur normale (pas basculé vers la caméra) et, quand ils sont
    mesurables, hauteur apparente retrouvée et hanches au-dessus des chevilles."""
    if features.torso_angle_deg >= thresholds.recovery_torso_angle_deg or track.is_foreshortened(features):
        return False
    hip_above = track.hip_above_ankles_torso(features)
    if hip_above is not None and hip_above < thresholds.upright_min_hip_above_ankles_torso:
        return False
    ratio = track.height_ratio(features)
    return ratio is None or ratio >= thresholds.recovery_height_ratio


def _is_lying(features: BodyFeatures, track: PersonTrack, thresholds: DetectionThresholds) -> bool:
    """Couché : torse horizontal ET corps aplati. Se pencher en avant garde les jambes verticales."""
    hip_above = track.hip_above_ankles_torso(features)
    if hip_above is not None and hip_above > thresholds.low_max_hip_above_ankles_torso:
        return False  # jambes sous le corps : penché ou accroupi, pas couché
    ratio = track.height_ratio(features)
    flattened = ratio is None or ratio <= thresholds.lying_height_ratio
    if features.torso_angle_deg >= thresholds.lying_torso_angle_deg and flattened:
        return True
    return (
        features.bbox_aspect >= thresholds.lying_bbox_aspect
        and features.torso_angle_deg >= thresholds.low_torso_angle_deg - 15.0
        and (ratio is None or ratio <= thresholds.low_bent_height_ratio)
    )


def classify_posture(
    features: BodyFeatures | None, track: PersonTrack, t: float, thresholds: DetectionThresholds
) -> Posture:
    """Libellé instantané. L'observation courante doit déjà être dans l'historique (`track.record`)."""
    if features is None:
        return Posture.INDETERMINATE
    if track.fall_state is FallState.SUSPECTED:
        return Posture.FALLING

    # FALLING instantané : descente rapide récente des hanches ET torse basculé ET (pieds visibles)
    # hanches déjà près du sol. S'asseoir ou s'accroupir d'un coup fait descendre les hanches aussi
    # vite mais garde le torse droit ; ramasser un objet bascule le torse mais garde les hanches
    # au-dessus des genoux.
    descent_speed = track.peak_descent_speed_torso(t - thresholds.motion_window_s, t, tipping_only=True)
    hip_above = track.hip_above_ankles_torso(features)
    if (track.fall_state is FallState.ARMED  # sans posture de départ (zone de repos), pas de chute à afficher
            and descent_speed >= thresholds.falling_descent_speed_torso_per_s
            and features.torso_angle_deg >= thresholds.falling_label_min_torso_angle_deg
            and (hip_above is None or hip_above <= thresholds.falling_label_max_hip_above_ankles_torso)):
        track.last_fast_fall_t = t
    if t - track.last_fast_fall_t <= thresholds.falling_label_hold_s:
        return Posture.FALLING

    if _is_lying(features, track, thresholds):
        return Posture.LYING
    if track.fall_state is FallState.CONFIRMED and stays_low(features, track, thresholds):
        # Personne tombée encore au sol, même si l'angle du torse ne le montre pas (chute vers la
        # caméra) : la posture affichée ne doit pas redevenir assise ou debout.
        return Posture.LYING
    if features.torso_angle_deg >= thresholds.sitting_max_torso_angle_deg:
        # Penché en avant, jambes sous le corps : debout si les jambes le confirment.
        if features.legs_visible and features.thigh_vertical_ratio >= thresholds.sitting_thigh_vertical_ratio:
            return Posture.STANDING
        return Posture.INDETERMINATE
    if (features.legs_visible
            and thresholds.sitting_min_thigh_vertical_ratio <= features.thigh_vertical_ratio
            < thresholds.sitting_thigh_vertical_ratio):
        return Posture.SITTING  # des genoux nettement au-dessus des hanches ne sont pas une assise
    if features.legs_visible and features.thigh_vertical_ratio < thresholds.sitting_min_thigh_vertical_ratio:
        return Posture.INDETERMINATE

    if _is_walking(features, track, t, thresholds):
        return Posture.WALKING
    if not features.legs_visible:
        return Posture.INDETERMINATE  # jambes masquées : debout et assis ne se distinguent pas
    return Posture.STANDING


def _is_walking(features: BodyFeatures, track: PersonTrack, t: float, thresholds: DetectionThresholds) -> bool:
    """Marche : alternance des pieds, ou déplacement net au sol (hanches, pieds, approche) torse droit.

    Les hanches, et non le centre du torse, mesurent le déplacement : le torse avance quand on se
    penche. Un déplacement avec le torse incliné est un changement de position sur place.
    """
    window = thresholds.walking_window_s
    swing = track.leg_swing_torso(window, t)
    if swing >= thresholds.walking_leg_swing_torso:
        return True
    speed, scale_rate = track.ground_speed_torso(window, t)
    if speed < thresholds.walking_speed_torso_per_s and scale_rate < thresholds.walking_scale_rate_per_s:
        return False
    # Se pencher, s'asseoir ou se relever incline le torse ; en marchant, il reste droit.
    return features.torso_angle_deg < thresholds.walking_max_torso_angle_deg


class PostureSmoother:
    """Libellé affiché : une nouvelle posture doit rester stable avant de remplacer l'actuelle.

    FALLING s'affiche immédiatement (il vient de la descente ou du détecteur de chute) ; « Posture
    indéterminée » demande une stabilité plus longue pour ne pas clignoter sur une image douteuse.
    """

    def __init__(self, thresholds: DetectionThresholds) -> None:
        self._th = thresholds

    def update(self, track: PersonTrack, instant: Posture, t: float) -> Posture:
        if track.displayed_posture is None or instant is Posture.FALLING:
            track.displayed_posture, track.display_candidate = instant, None
            return instant
        if instant is track.displayed_posture:
            track.display_candidate = None
            return instant
        if instant is not track.display_candidate:
            track.display_candidate, track.display_candidate_since_t = instant, t
        hold = (self._th.posture_indeterminate_enter_s if instant is Posture.INDETERMINATE
                else self._th.posture_enter_s)
        if t - track.display_candidate_since_t >= hold:
            track.displayed_posture, track.display_candidate = instant, None
        return track.displayed_posture
