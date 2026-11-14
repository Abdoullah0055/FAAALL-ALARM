"""Assemblage de l'application Windows : sources, détection, voix, SMS et fil d'analyse."""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .capture import FrameSource, VideoFileSource, WebcamSource
from .clock import SystemClock
from .config import (
    DEFAULT_HISTORY_PATH, DEFAULT_MODEL_PATH, SMS_MODE_LIVE, AppConfig, read_config,
)
from .core import MonitoringCore
from .integration import build_interpreter, build_sms_gateway, real_sms_allowed
from .logbook import HistoryStore
from .model import FrameObservation, PersonView, SourceKind, SourceStatus
from .pose import MediaPipePoseEstimator, PoseEstimator, ScenarioPoseEstimator
from .settings import STATE_DIR, LocalSettings, configuration
from .settings_backend import AppSettingsBackend
from .simulation.demo import DemoDirector, ensure_demo_video
from .simulation.runner import demo_config
from .simulation.scenarios import get_scenario
from .voice import EspeakVoice, UnavailableVoice


@dataclass(frozen=True)
class AppOptions:
    video_path: Path | None = None
    camera_index: int | None = None
    demo_scenario: str | None = None  # nom d'un scénario simulé : vidéo générée, pose scriptée, SMS simulé
    demo_reply_delay_s: float | None = 6.0
    demo_cancel_after_s: float | None = None
    countdown_seconds: float | None = None
    config_path: Path | None = None


@dataclass(frozen=True)
class RenderState:
    image_bgr: np.ndarray | None
    persons: list[PersonView]
    timestamp_s: float


class MonitoringApp:
    """Porte la source, l'estimateur et le fil d'analyse. L'interface lit `latest()` et pilote `core`."""

    def __init__(self, options: AppOptions) -> None:
        self.options = options
        self.clock = SystemClock()
        self.config, self.source_kind = self._resolve_config_and_kind(options)
        self._estimator, self.source = self._build_source(options)
        self.executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="faaall")  # voix, envoi, analyse
        self.poll_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="faaall-sondage")
        history_mode = "réel" if real_sms_allowed(self.config, self.source_kind) else "simulé"
        gateway, interpreter, voice = self._build_services(self.config)
        self.core = MonitoringCore(
            self.config, self.clock, voice, gateway, interpreter,
            HistoryStore(DEFAULT_HISTORY_PATH, history_mode, self.clock), self.executor, self.source_kind,
            services_factory=self._build_services, poll_executor=self.poll_executor,
        )
        self.settings_services = LocalSettings(STATE_DIR / "reglages.json", configuration())
        self.settings_backend = AppSettingsBackend(
            self.core, self.settings_services, self.source_kind,
            lambda: (self.latest().image_bgr if self.latest() else None), options.config_path,
        )
        self.director = (
            DemoDirector(self.core, options.demo_reply_delay_s, cancel_after_s=options.demo_cancel_after_s)
            if options.demo_scenario else None
        )
        self._latest: RenderState | None = None
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="analyse", daemon=True)

    # -- construction ---------------------------------------------------------------------------------
    def _build_services(self, config: AppConfig):
        """Passerelle SMS, interpréteur et voix selon les réglages (reconstruits au repos après Réglages)."""
        voice = (
            EspeakVoice(config.espeak_path, config.voice_speed_wpm, volume_percent=config.voice_volume)
            if config.voice_enabled else UnavailableVoice()
        )
        return (build_sms_gateway(config, self.source_kind, self.clock),
                build_interpreter(config, self.source_kind), voice)

    def _resolve_config_and_kind(self, options: AppOptions) -> tuple[AppConfig, SourceKind]:
        if options.demo_scenario:
            base = demo_config()  # identité factice : la démo ne lit ni .env ni config locale
            kind = SourceKind.SIMULATION
        else:
            base = read_config(options.config_path)
            kind = SourceKind.VIDEO_FILE if options.video_path else SourceKind.WEBCAM
        overrides = {}
        if options.camera_index is not None:
            overrides["camera_index"] = options.camera_index
        if options.countdown_seconds is not None:
            overrides["countdown_seconds"] = options.countdown_seconds
        if kind is not SourceKind.WEBCAM and base.sms_mode == SMS_MODE_LIVE:
            overrides["sms_mode"] = "simulated"  # sans webcam, jamais de SMS réel
        return AppConfig(**{**base.to_dict(), **overrides}), kind

    def _build_source(self, options: AppOptions) -> tuple[PoseEstimator, FrameSource]:
        if options.demo_scenario:
            scenario = get_scenario(options.demo_scenario)
            self.config = AppConfig(**{**self.config.to_dict(), "lying_zones": [list(z) for z in scenario.lying_zones]})
            video = ensure_demo_video(scenario)
            return ScenarioPoseEstimator(scenario), VideoFileSource(video, self.clock)
        estimator = MediaPipePoseEstimator(DEFAULT_MODEL_PATH)
        if options.video_path:
            return estimator, VideoFileSource(options.video_path, self.clock)
        return estimator, WebcamSource(self.config.camera_index, self.clock)

    # -- cycle de vie ---------------------------------------------------------------------------------------
    def start(self) -> None:
        self.executor.submit(self.core.verify_sms_availability)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=3.0)
        self.source.close()
        self._estimator.close()
        self.executor.shutdown(wait=False, cancel_futures=True)
        self.poll_executor.shutdown(wait=False, cancel_futures=True)

    def latest(self) -> RenderState | None:
        with self._lock:
            return self._latest

    # -- fil d'analyse ----------------------------------------------------------------------------------------
    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                self._step()
            except Exception as error:  # noqa: BLE001 - une image défaillante ne doit pas arrêter la surveillance
                self.core.log.add(f"Erreur d'analyse : {type(error).__name__}")
                time.sleep(0.2)

    def _step(self) -> None:
        frame = self.source.read()
        self.core.set_source_status(frame.status)
        if frame.status is SourceStatus.ENDED:
            time.sleep(0.05)
            return
        if frame.status is SourceStatus.LOST or frame.image_bgr is None or frame.timestamp_s is None:
            return
        height, width = frame.image_bgr.shape[:2]
        poses = self._estimator.estimate(frame.image_bgr, frame.timestamp_s)
        video_time = frame.timestamp_s if self.source.kind is not SourceKind.WEBCAM else None
        analysis = self.core.process(FrameObservation(frame.timestamp_s, (width, height), poses), video_time)
        with self._lock:
            self._latest = RenderState(frame.image_bgr, analysis.persons, frame.timestamp_s)
        # Le dernier état de personnes reste affiché même si l'image suivante n'en contient plus.
