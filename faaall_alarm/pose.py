"""Estimation de pose : MediaPipe Pose (tâche PoseLandmarker) et estimateur scripté pour la démo."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .model import LANDMARK_COUNT, PersonPose


class PoseEstimator(Protocol):
    def estimate(self, image_bgr: np.ndarray, timestamp_s: float) -> tuple[PersonPose, ...]:
        ...

    def close(self) -> None:
        ...


class MediaPipePoseEstimator:
    """Enveloppe MediaPipe Pose Landmarker (modèle « lite », float16) en mode vidéo."""

    def __init__(self, model_path: Path, max_people: int = 2, min_confidence: float = 0.5) -> None:
        if not Path(model_path).is_file():
            raise FileNotFoundError(
                f"Modèle de pose introuvable : {model_path}. Lance scripts/install.ps1 pour le télécharger."
            )
        os.environ.setdefault("GLOG_minloglevel", "2")  # réduit les journaux natifs de MediaPipe
        import mediapipe as mp
        from mediapipe.tasks.python import BaseOptions, vision

        self._mp = mp
        options = vision.PoseLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model_path)),
            running_mode=vision.RunningMode.VIDEO,
            num_poses=max_people,
            min_pose_detection_confidence=min_confidence,
            min_pose_presence_confidence=min_confidence,
            min_tracking_confidence=min_confidence,
        )
        self._landmarker = vision.PoseLandmarker.create_from_options(options)
        self._last_timestamp_ms = -1

    def estimate(self, image_bgr: np.ndarray, timestamp_s: float) -> tuple[PersonPose, ...]:
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        image = self._mp.Image(image_format=self._mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb))
        timestamp_ms = max(int(timestamp_s * 1000), self._last_timestamp_ms + 1)  # strictement croissant
        self._last_timestamp_ms = timestamp_ms
        result = self._landmarker.detect_for_video(image, timestamp_ms)
        poses = []
        for landmarks in result.pose_landmarks:
            if len(landmarks) != LANDMARK_COUNT:
                continue
            poses.append(PersonPose(np.array([[p.x, p.y, p.visibility or 0.0] for p in landmarks])))
        return tuple(poses)

    def close(self) -> None:
        self._landmarker.close()


class ScenarioPoseEstimator:
    """Pose scriptée par un scénario simulé, lue à partir du temps de la vidéo (démo sans MediaPipe)."""

    def __init__(self, scenario) -> None:  # Scenario, importé à l'usage pour éviter une dépendance circulaire
        self._scenario = scenario

    def estimate(self, image_bgr: np.ndarray, timestamp_s: float) -> tuple[PersonPose, ...]:
        return tuple(self._scenario.poses_at(timestamp_s))

    def close(self) -> None:
        return None
