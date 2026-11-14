"""Rendu d'un scénario en vidéo MP4 de mannequins (pour la démo, sans donnée privée)."""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..model import (
    LEFT_ELBOW, LEFT_HIP, LEFT_KNEE, LEFT_ANKLE, LEFT_SHOULDER, LEFT_WRIST, NOSE, RIGHT_ANKLE,
    RIGHT_ELBOW, RIGHT_HIP, RIGHT_KNEE, RIGHT_SHOULDER, RIGHT_WRIST, PersonPose,
)
from .scenarios import GROUND_Y_PX, TORSO_PX, Scenario

_BODY_COLORS = ((70, 130, 220), (80, 170, 100))  # BGR, une couleur par personne
_LIMBS = (
    (LEFT_SHOULDER, LEFT_ELBOW), (LEFT_ELBOW, LEFT_WRIST), (RIGHT_SHOULDER, RIGHT_ELBOW),
    (RIGHT_ELBOW, RIGHT_WRIST), (LEFT_HIP, LEFT_KNEE), (LEFT_KNEE, LEFT_ANKLE),
    (RIGHT_HIP, RIGHT_KNEE), (RIGHT_KNEE, RIGHT_ANKLE),
)


def render_frame(scenario: Scenario, t: float) -> np.ndarray:
    width, height = scenario.frame_size_px
    frame = np.full((height, width, 3), (58, 54, 50), dtype=np.uint8)
    cv2.rectangle(frame, (0, int(GROUND_Y_PX)), (width, height), (96, 88, 80), -1)
    for x0, y0, x1, y1 in scenario.lying_zones_px:
        cv2.rectangle(frame, (int(x0), int(y0)), (int(x1), int(y1)), (120, 90, 150), -1)
        cv2.putText(frame, "lit", (int(x0) + 8, int(y0) + 22), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (230, 230, 230), 1)
    if scenario.chair_x_px is not None:
        x = int(scenario.chair_x_px)
        cv2.rectangle(frame, (x - 50, int(GROUND_Y_PX - 80)), (x + 50, int(GROUND_Y_PX - 66)), (60, 90, 130), -1)
        cv2.line(frame, (x - 44, int(GROUND_Y_PX - 66)), (x - 44, int(GROUND_Y_PX)), (60, 90, 130), 6)
        cv2.line(frame, (x + 44, int(GROUND_Y_PX - 66)), (x + 44, int(GROUND_Y_PX)), (60, 90, 130), 6)
    for index, pose in enumerate(scenario.poses_at(t)):
        _draw_mannequin(frame, pose, _BODY_COLORS[index % 2], scenario.frame_size_px)
    return frame


def _draw_mannequin(frame: np.ndarray, pose: PersonPose, color: tuple[int, int, int], size: tuple[int, int]) -> None:
    points = (pose.landmarks[:, :2] * np.array(size, dtype=float)).astype(int)
    limb_thickness = max(int(TORSO_PX * 0.22), 4)
    torso = np.array([points[LEFT_SHOULDER], points[RIGHT_SHOULDER], points[RIGHT_HIP], points[LEFT_HIP]])
    cv2.fillPoly(frame, [torso], color)
    for start, end in _LIMBS:
        cv2.line(frame, tuple(points[start]), tuple(points[end]), color, limb_thickness, cv2.LINE_AA)
    cv2.circle(frame, tuple(points[NOSE]), int(TORSO_PX * 0.28), (170, 200, 235), -1, cv2.LINE_AA)


def write_scenario_video(scenario: Scenario, path: Path) -> Path:
    """Écrit le scénario en MP4. Retourne le chemin écrit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(path), cv2.VideoWriter_fourcc(*"mp4v"), scenario.fps, scenario.frame_size_px
    )
    if not writer.isOpened():
        raise OSError(f"Impossible d'écrire la vidéo : {path}")
    try:
        for t in scenario.frame_times():
            writer.write(render_frame(scenario, float(t)))
    finally:
        writer.release()
    return path
