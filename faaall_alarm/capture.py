"""Sources d'images : webcam ou fichier MP4, avec une perte de caméra distincte d'une fin de fichier."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .clock import Clock
from .model import SourceKind, SourceStatus

CAMERA_LOST_AFTER_S = 1.0  # échecs de lecture consécutifs avant de déclarer la caméra inactive
CAMERA_RETRY_EVERY_S = 2.0
MAX_SKIPPED_FRAMES = 30  # images sautées au plus par lecture quand l'analyse d'un MP4 prend du retard


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
    """Webcam lue par un fil dédié qui ne garde que l'image la plus récente.

    Lire la caméra depuis le fil d'analyse laisse les images s'accumuler dans le tampon du pilote
    pendant l'inférence : l'analyse traite alors des images de plus en plus anciennes. Ici, chaque
    image est horodatée à sa lecture et `read()` rend la dernière, les intermédiaires sont écartées.
    """

    kind = SourceKind.WEBCAM

    def __init__(self, camera_index: int, clock: Clock,
                 capture_factory: Callable[[int], cv2.VideoCapture] | None = None) -> None:
        self._index = camera_index
        self._clock = clock
        self._factory = capture_factory or (lambda index: cv2.VideoCapture(index, cv2.CAP_DSHOW))
        self._capture = self._open()
        self._lock = threading.Lock()
        self._new_frame = threading.Event()
        self._latest: tuple[np.ndarray, float] | None = None
        self._last_ok_t = self._clock.monotonic()
        self._last_retry = self._clock.monotonic()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._grab_loop, name="capture", daemon=True)
        self._thread.start()

    def _open(self) -> cv2.VideoCapture:
        capture = self._factory(self._index)
        capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # ignoré par certains pilotes : le fil reste nécessaire
        return capture

    def _grab_loop(self) -> None:
        while not self._stopped.is_set():
            ok, image = self._capture.read() if self._capture.isOpened() else (False, None)
            now = self._clock.monotonic()
            if ok and image is not None:
                with self._lock:
                    self._latest = (image, now)
                    self._last_ok_t = now
                self._new_frame.set()
                continue
            if now - self._last_ok_t >= CAMERA_LOST_AFTER_S and now - self._last_retry >= CAMERA_RETRY_EVERY_S:
                self._last_retry = now
                self._capture.release()
                self._capture = self._open()
            time.sleep(0.03)

    def read(self) -> SourceFrame:
        if self._new_frame.wait(timeout=0.1):
            with self._lock:
                self._new_frame.clear()
                image, timestamp = self._latest
            return SourceFrame(SourceStatus.ACTIVE, image, timestamp)
        with self._lock:
            silent_s = self._clock.monotonic() - self._last_ok_t
        if silent_s < CAMERA_LOST_AFTER_S:
            return SourceFrame(SourceStatus.ACTIVE)  # échec ponctuel toléré
        return SourceFrame(SourceStatus.LOST)

    def close(self) -> None:
        self._stopped.set()
        self._thread.join(timeout=1.0)
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
        if self._realtime:
            self._skip_late_frames()
        ok, image = self._capture.read()
        if not ok:
            return SourceFrame(SourceStatus.ENDED)
        timestamp = self._timestamp()
        self._frame_index += 1
        if self._realtime:
            delay = (self._started + timestamp) - self._clock.monotonic()
            if delay > 0:
                time.sleep(delay)
        return SourceFrame(SourceStatus.ACTIVE, image, timestamp)

    def _timestamp(self) -> float:
        position_ms = self._capture.get(cv2.CAP_PROP_POS_MSEC)
        return position_ms / 1000.0 if position_ms > 0 else self._frame_index / self._fps

    def _skip_late_frames(self) -> None:
        """En lecture réelle, saute les images déjà en retard, comme une webcam dont l'analyse est
        lente : la latence reste bornée au lieu de s'accumuler (sans effet si l'analyse suit)."""
        late_by_frames = (self._clock.monotonic() - self._started) * self._fps - self._frame_index
        for _ in range(min(int(late_by_frames) - 1, MAX_SKIPPED_FRAMES)):
            if not self._capture.grab():
                return
            self._frame_index += 1

    def close(self) -> None:
        self._capture.release()
