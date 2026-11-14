"""Démo reproductible : MP4 de mannequins, pose scriptée, SMS simulé et réponse du proche, sans clé API."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from ..config import PROJECT_ROOT
from ..core import MonitoringCore
from ..incident import IncidentState
from .render import write_scenario_video
from .runner import DEMO_REPLY_TEXT
from .scenarios import Scenario

DEMO_VIDEO_DIR = PROJECT_ROOT / "artifacts" / "demo"


def ensure_demo_video(scenario: Scenario, directory: Path = DEMO_VIDEO_DIR) -> Path:
    """Génère la vidéo de démonstration si elle manque (dossier ignoré par Git)."""
    path = directory / f"{scenario.name}.mp4"
    if not path.is_file():
        write_scenario_video(scenario, path)
    return path


@dataclass
class DemoDirector:
    """Simule l'action externe de la démo : l'annulation éventuelle et la réponse du proche.

    À appeler régulièrement avec l'horloge du cœur. Aucune de ces actions n'utilise de réseau.
    """

    core: MonitoringCore
    reply_delay_s: float | None = 6.0  # None : pas de réponse simulée
    reply_text: str = DEMO_REPLY_TEXT
    cancel_after_s: float | None = None  # annulation simulée après l'ouverture de l'incident
    _reply_sent: set[str] = field(default_factory=set)
    _cancel_done: set[str] = field(default_factory=set)

    def tick(self) -> None:
        view = self.core.engine.view()
        if view is None:
            return
        incident = next(i for i in self.core.engine.incidents() if i.incident_id == view.incident_id)
        now = self.core.clock.monotonic()
        if (
            self.cancel_after_s is not None and view.state is IncidentState.COUNTDOWN
            and incident.incident_id not in self._cancel_done
            and now - incident.created_monotonic >= self.cancel_after_s
        ):
            self._cancel_done.add(incident.incident_id)
            self.core.engine.cancel("annulation simulée par la démo")
            return
        gateway = self.core.gateway
        if (
            self.reply_delay_s is not None and incident.sms_requested_wall is not None
            and incident.incident_id not in self._reply_sent and hasattr(gateway, "inject_reply")
        ):
            elapsed = (self.core.clock.wall() - incident.sms_requested_wall).total_seconds()
            if elapsed >= self.reply_delay_s:
                self._reply_sent.add(incident.incident_id)
                config = self.core.config
                gateway.inject_reply(self.reply_text, config.contact_number, config.twilio_number)
                self.core.log.add("Démo : réponse simulée du proche injectée")
