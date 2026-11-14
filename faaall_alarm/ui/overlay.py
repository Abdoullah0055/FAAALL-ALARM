"""Dessin optionnel du squelette sur l'image de la caméra."""

from __future__ import annotations

import cv2
import numpy as np

from ..model import SKELETON_SEGMENTS, PersonView

_MIN_VISIBILITY = 0.5  # même seuil que `DetectionThresholds.min_landmark_visibility`
_COLORS = {"Personne 2": (255, 160, 0)}  # BGR ; « Personne » et « Personne 1 » partagent la première
_DEFAULT_COLOR = (0, 200, 255)


def draw_skeleton(image_bgr: np.ndarray, persons: list[PersonView]) -> np.ndarray:
    """Retourne une copie de l'image avec le squelette et le nom de chaque personne suivie.

    La couleur suit le libellé, stable pendant toute la trace, et non l'ordre de la liste : deux
    personnes ne changent pas de couleur d'une image à l'autre. Seuls les points fiables sont tracés
    et le nom est placé au-dessus du point fiable le plus haut (pas sur un nez masqué).
    """
    annotated = image_bgr.copy()
    height, width = annotated.shape[:2]
    for person in persons:
        if person.landmarks is None:
            continue  # pose masquée sur cette image : aucun squelette périmé n'est redessiné
        color = _COLORS.get(person.label, _DEFAULT_COLOR)
        points = (person.landmarks[:, :2] * np.array([width, height])).astype(int)
        visible = person.landmarks[:, 2] >= _MIN_VISIBILITY
        if not visible.any():
            continue
        for start, end in SKELETON_SEGMENTS:
            if visible[start] and visible[end]:
                cv2.line(annotated, tuple(points[start]), tuple(points[end]), color, 2, cv2.LINE_AA)
        for point in points[visible]:
            cv2.circle(annotated, tuple(point), 3, (255, 255, 255), -1, cv2.LINE_AA)
        top = points[visible][np.argmin(points[visible][:, 1])]
        anchor = (int(np.clip(top[0] + 8, 0, width - 1)), int(np.clip(top[1] - 10, 12, height - 1)))
        cv2.putText(annotated, person.label, anchor, cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
    return annotated
