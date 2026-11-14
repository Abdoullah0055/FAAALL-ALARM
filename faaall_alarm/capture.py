"""Sources d'images : webcam ou fichier MP4, avec une perte de caméra distincte d'une fin de fichier."""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .clock import Clock
from .model import SourceKind, SourceStatus

CAMERA_LOST_AFTER_S = 1.0  # échecs de lecture consécutifs avant de déclarer la caméra inactive
CAMERA_RETRY_EVERY_S = 2.0


@dataclass(frozen=True)
class SourceFrame:
    status: SourceStatus
    image_bgr: np.ndarray | None = None
    timestamp_s: float | None = None  # temps du fichier en MP4, horloge monotone en direct


class FrameSource(Protocol):
    kind: SourceKind

    def read(self) -> SourceFrame:
        ...

    def close(self) -> None:
        ...


class WebcamSource:
    kind = SourceKind.WEBCAM

    def __init__(self, camera_index: int, clock: Clock) -> None:
        self._index = camera_index
        self._clock = clock
        self._capture = self._open()
        self._failing_since: float | None = None
        self._last_retry = self._clock.monotonic()

    def _open(self) -> cv2.VideoCapture:
        return cv2.VideoCapture(self._index, cv2.CAP_DSHOW)

    def read(self) -> SourceFrame:
        now = self._clock.monotonic()
        ok, image = self._capture.read() if self._capture.isOpened() else (False, None)
        if ok and image is not None:
            self._failing_since = None
            return SourceFrame(SourceStatus.ACTIVE, image, now)
        if self._failing_since is None:
            self._failing_since = now
        if now - self._failing_since < CAMERA_LOST_AFTER_S:
            time.sleep(0.03)
            return SourceFrame(SourceStatus.ACTIVE)  # échec ponctuel toléré
        if now - self._last_retry >= CAMERA_RETRY_EVERY_S:
            self._last_retry = now
            self._capture.release()
            self._capture = self._open()
        time.sleep(0.1)
        return SourceFrame(SourceStatus.LOST)

    def close(self) -> None:
        self._capture.release()


class VideoFileSource:
    """Lecture d'un MP4 avec les temps du fichier. `realtime` cadence la lecture sur l'horloge murale."""

    kind = SourceKind.VIDEO_FILE

    def __init__(self, path: Path, clock: Clock, realtime: bool = True) -> None:
        if not Path(path).is_file():
            raise FileNotFoundError(f"Fichier vidéo introuvable : {path}")
        self._capture = cv2.VideoCapture(str(path))
        if not self._capture.isOpened():
            raise OSError(f"Impossible d'ouvrir la vidéo : {path}")
        self._clock = clock
        self._realtime = realtime
        self._fps = self._capture.get(cv2.CAP_PROP_FPS) or 30.0
        self._started = clock.monotonic()
        self._frame_index = 0

    def read(self) -> SourceFrame:
        ok, image = self._capture.read()
        if not ok:
            return SourceFrame(SourceStatus.ENDED)
        position_ms = self._capture.get(cv2.CAP_PROP_POS_MSEC)
        timestamp = position_ms / 1000.0 if position_ms > 0 else self._frame_index / self._fps
        self._frame_index += 1
        if self._realtime:
            delay = (self._started + timestamp) - self._clock.monotonic()
            if delay > 0:
                time.sleep(delay)
        return SourceFrame(SourceStatus.ACTIVE, image, timestamp)

    def close(self) -> None:
        self._capture.release()
