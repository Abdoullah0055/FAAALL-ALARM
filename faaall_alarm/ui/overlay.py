"""Dessin optionnel du squelette sur l'image de la caméra."""

from __future__ import annotations

import cv2
import numpy as np

from ..model import SKELETON_SEGMENTS, PersonView

_MIN_VISIBILITY = 0.5
_COLORS = ((0, 200, 255), (255, 160, 0))  # BGR


def draw_skeleton(image_bgr: np.ndarray, persons: list[PersonView]) -> np.ndarray:
    """Retourne une copie de l'image avec le squelette et le nom de chaque personne suivie."""
    annotated = image_bgr.copy()
    height, width = annotated.shape[:2]
    for index, person in enumerate(persons):
        if person.landmarks is None:
            continue
        color = _COLORS[index % len(_COLORS)]
        points = (person.landmarks[:, :2] * np.array([width, height])).astype(int)
        visible = person.landmarks[:, 2] >= _MIN_VISIBILITY
        for start, end in SKELETON_SEGMENTS:
            if visible[start] and visible[end]:
                cv2.line(annotated, tuple(points[start]), tuple(points[end]), color, 2, cv2.LINE_AA)
        for point in points[visible]:
            cv2.circle(annotated, tuple(point), 3, (255, 255, 255), -1, cv2.LINE_AA)
        anchor = tuple(points[0] + np.array([8, -10]))
        cv2.putText(annotated, person.label, anchor, cv2.FONT_HERSHEY_SIMPLEX, 0.55, color, 2, cv2.LINE_AA)
    return annotated
