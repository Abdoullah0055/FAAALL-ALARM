"""Capture webcam : l'analyse reçoit la dernière image lue, une coupure devient « caméra inactive »."""

from __future__ import annotations

import threading
import time

import numpy as np

from faaall_alarm.capture import CAMERA_LOST_AFTER_S, WebcamSource
from faaall_alarm.clock import SystemClock
from faaall_alarm.model import SourceStatus


class FakeCapture:
    """Caméra factice à 100 images/s ; chaque image porte son numéro dans son premier pixel."""

    def __init__(self) -> None:
        self.count = 0
        self.working = threading.Event()
        self.working.set()

    def isOpened(self) -> bool:  # noqa: N802 - même interface qu'OpenCV
        return True

    def set(self, prop, value) -> bool:
        return True

    def read(self):
        time.sleep(0.01)
        if not self.working.is_set():
            return False, None
        self.count += 1
        return True, np.full((4, 4, 3), self.count % 256, dtype=np.uint8)

    def release(self) -> None:
        return None


def test_la_lecture_rend_l_image_la_plus_recente_sans_retard_accumule():
    fake = FakeCapture()
    source = WebcamSource(0, SystemClock(), capture_factory=lambda index: fake)
    try:
        source.read()
        time.sleep(0.2)  # analyse lente : une vingtaine d'images sont lues pendant ce temps
        frame = source.read()
        assert frame.status is SourceStatus.ACTIVE
        assert fake.count - int(frame.image_bgr[0, 0, 0]) <= 2  # pas les images en attente
    finally:
        source.close()


def test_coupure_prolongee_devient_camera_inactive():
    fake = FakeCapture()
    source = WebcamSource(0, SystemClock(), capture_factory=lambda index: fake)
    try:
        assert source.read().status is SourceStatus.ACTIVE
        fake.working.clear()
        deadline = time.monotonic() + CAMERA_LOST_AFTER_S + 1.0
        statuses = []
        while time.monotonic() < deadline:
            statuses.append(source.read().status)
        assert statuses[0] is SourceStatus.ACTIVE and statuses[-1] is SourceStatus.LOST
    finally:
        source.close()
