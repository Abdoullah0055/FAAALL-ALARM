"""Raccordement des deux lots : moteur d'incident (lot 1) avec SmsService, OpenAI et la page Réglages (lot 2).

Le transport Twilio et OpenAI est factice : aucun réseau, aucun SMS réel, aucune clé API.
"""

from __future__ import annotations

import json
from datetime import timezone
from email.utils import format_datetime

import cv2
import numpy as np
import pytest

from faaall_alarm.adapters import ReplyInterpreterAdapter, SmsServiceGateway
from faaall_alarm.config import AppConfig, read_config
from faaall_alarm.incident import IncidentState
from faaall_alarm.integration import build_interpreter, build_sms_gateway
from faaall_alarm.model import SourceKind
from faaall_alarm.settings import LocalSettings, Settings
from faaall_alarm.settings_backend import AppSettingsBackend
from faaall_alarm.simulation.runner import (
    DEMO_CONTACT_NUMBER, DEMO_REPLY_TEXT, DEMO_TWILIO_NUMBER, ScenarioRunner, demo_config,
)
from faaall_alarm.simulation.scenarios import get_scenario
from faaall_alarm.sms import Mode, SmsConfig, SmsService, Source
from faaall_alarm.sms_contract import InterpretationUnavailable, ReplyState, SmsStatus

from .contracts import assert_gateway_contract, assert_interpreter_contract

TWILIO_ENV = {"TWILIO_ACCOUNT_SID": "AC" + "0" * 32, "TWILIO_AUTH_TOKEN": "jeton-factice", "OPENAI_API_KEY": "cle-factice"}
SENT_SID, REPLY_SID = "SM" + "a" * 32, "SM" + "b" * 32


def simulated_gateway(tmp_path, clock):
    config = SmsConfig(Mode.SIMULATED, DEMO_TWILIO_NUMBER, DEMO_CONTACT_NUMBER)
    now = lambda: clock.wall().astimezone(timezone.utc)  # noqa: E731
    return SmsServiceGateway(SmsService(tmp_path / "sms.sqlite3", config, Source.TEST, now=now))


def test_parcours_complet_avec_le_service_sms_du_lot_2_en_simulation(tmp_path):
    runner = ScenarioRunner(get_scenario("chute_debout"))
    runner.gateway = runner.core.gateway = simulated_gateway(tmp_path, runner.clock)
    runner.core.interpreter = ReplyInterpreterAdapter(None)
    runner.at(24.0, lambda run: run.inject_reply(DEMO_REPLY_TEXT))
    result = runner.run(until_s=40.0)
    incident = result.core.engine.incidents()[0]
    stored = runner.gateway.service.lookup(incident.incident_id)
    assert stored.state.value == "Simulé" and len(result.incident_ids) == 1 and len(result.voice.spoken) == 1
    view = result.core.engine.view()
    assert (view.reply.state, view.reply.delay_minutes) == (ReplyState.EN_ROUTE, 20)
    assert view.state is IncidentState.HANDLED  # la réponse ne ferme pas l'incident
    assert runner.gateway.service.poll_replies(incident.sms_requested_wall.astimezone()) == []  # acquittée


def test_une_annulation_avant_l_echeance_ne_cree_aucune_tentative_dans_la_base(tmp_path):
    runner = ScenarioRunner(get_scenario("chute_debout"))
    runner.gateway = runner.core.gateway = simulated_gateway(tmp_path, runner.clock)
    runner.at(12.0, lambda run: run.core.engine.cancel())
    result = runner.run(until_s=30.0)
    assert runner.gateway.service.lookup(result.incident_ids[0]) is None


def test_chaque_incident_a_un_identifiant_unique_meme_a_la_meme_minute():
    ids = {ScenarioRunner(get_scenario("chute_debout")).run(until_s=8.0).incident_ids[0] for _ in range(5)}
    assert len(ids) == 5  # une base SMS persistante ne doit jamais confondre deux incidents


class FakeTwilio:
    """Transport factice : envoi, statut de livraison et boîte de réception Twilio."""

    def __init__(self, clock):
        self.clock = clock
        self.posts, self.reply_available = [], False

    def __call__(self, url, headers, body=None):
        if body is not None:
            self.posts.append(body.decode())
            return {"sid": SENT_SID, "status": "queued"}
        if url.endswith(f"/{SENT_SID}.json"):
            return {"status": "delivered"}
        messages = []
        if self.reply_available:
            moment = self.clock.wall().astimezone(timezone.utc).replace(microsecond=0)
            messages.append({"direction": "inbound", "from": DEMO_CONTACT_NUMBER, "to": DEMO_TWILIO_NUMBER,
                             "sid": REPLY_SID, "body": DEMO_REPLY_TEXT, "date_sent": format_datetime(moment)})
        return {"messages": messages, "next_page_uri": None}


def test_chemin_reel_avec_transports_factices_envoi_livraison_reponse_et_openai(tmp_path):
    live = demo_config(sms_mode="live", sms_live_confirmed=True)
    runner = ScenarioRunner(get_scenario("chute_debout"), config=live, source_kind=SourceKind.WEBCAM)
    twilio = FakeTwilio(runner.clock)
    now = lambda: runner.clock.wall().astimezone(timezone.utc)  # noqa: E731
    runner.gateway = runner.core.gateway = build_sms_gateway(
        live, SourceKind.WEBCAM, runner.clock, tmp_path, TWILIO_ENV, twilio, now=now)
    openai_bodies = []

    def openai(url, headers, body=None):
        assert url == "https://api.openai.com/v1/responses"
        openai_bodies.append(body.decode())
        content = json.dumps({"state": "En route", "eta_minutes": 20})
        return {"status": "completed", "output": [{
            "type": "message", "role": "assistant", "status": "completed",
            "content": [{"type": "output_text", "text": content}],
        }]}

    runner.core.interpreter = build_interpreter(live, SourceKind.WEBCAM, TWILIO_ENV, openai)
    runner.at(24.0, lambda run: setattr(twilio, "reply_available", True))
    result = runner.run(until_s=40.0)
    assert len(twilio.posts) == 1 and "ALERTE" in twilio.posts[0].replace("+", " ").upper()
    assert "MARIE" in twilio.posts[0].upper()  # prénom attribuable : une seule personne, cause individuelle
    view = result.core.engine.view()
    assert view.sms_status is SmsStatus.DELIVERED  # statut de livraison explicite relu par le suivi
    assert (view.reply.state, view.reply.delay_minutes) == (ReplyState.EN_ROUTE, 20)
    request = json.loads(openai_bodies[0])
    assert request["model"] == "gpt-6-luna" and request["store"] is False
    assert request["input"].startswith("Je suis en route") and DEMO_CONTACT_NUMBER not in openai_bodies[0]


def test_openai_en_panne_affiche_reponse_a_verifier(tmp_path):
    live = demo_config(sms_mode="live", sms_live_confirmed=True)

    def panne(url, headers, body=None):
        from faaall_alarm.http import ServiceError
        raise ServiceError()

    interpreter = build_interpreter(live, SourceKind.WEBCAM, TWILIO_ENV, panne)
    with pytest.raises(InterpretationUnavailable):
        interpreter.interpret(DEMO_REPLY_TEXT)


def test_contrats_des_adaptateurs_du_lot_2(tmp_path):
    from faaall_alarm.clock import ManualClock

    assert_gateway_contract(simulated_gateway(tmp_path, ManualClock()))
    assert_interpreter_contract(ReplyInterpreterAdapter(None))


# -- page Réglages ---------------------------------------------------------------------------------------
@pytest.fixture
def backend(tmp_path):
    runner = ScenarioRunner(get_scenario("chute_debout"), config=AppConfig())
    services = LocalSettings(tmp_path / "reglages.json", {})
    path = tmp_path / "config.local.json"
    return AppSettingsBackend(runner.core, services, SourceKind.VIDEO_FILE, lambda: None, path), runner.core, path


def test_webcam_settings_reject_simulated_channel(backend):
    offline, core, path = backend
    webcam = AppSettingsBackend(core, offline._services, SourceKind.WEBCAM, lambda: None, path)
    assert "sms_mode" in webcam.validate(Settings())
    with pytest.raises(ValueError):
        webcam.apply(Settings())
    assert not path.exists()


def test_reglages_charger_valider_appliquer_et_rouvrir(backend):
    backend, core, path = backend
    settings = backend.load()
    assert settings.countdown_seconds == 10 and settings.zone == (0, 0, 100, 100) and settings.rest_zone is None
    changed = Settings(**{**settings.__dict__, "zone": (10, 5, 95, 100), "rest_zone": (50, 10, 80, 65),
                          "countdown_seconds": 15, "volume": 80, "first_name": "Marie"})
    assert backend.validate(changed) == {}
    backend.apply(changed)
    assert core.config.countdown_seconds == 15 and core.config.lying_zones == [[0.5, 0.1, 0.8, 0.65]]
    reloaded = backend.load()
    assert (reloaded.zone, reloaded.rest_zone, reloaded.volume) == ((10, 5, 95, 100), (50, 10, 80, 65), 80)
    assert read_config(path, use_environment=False).monitored_zone == [0.1, 0.05, 0.95, 1.0]


def test_reglages_invalides_aucune_ecriture_et_erreurs_par_champ(backend):
    backend, core, path = backend
    base = backend.load()
    errors = backend.validate(Settings(**{**base.__dict__, "zone": (50, 50, 20, 20), "countdown_seconds": 0,
                                           "recipient": "514-555-0123"}))
    assert {"zone", "countdown_seconds", "recipient"} <= set(errors)
    with pytest.raises(ValueError):
        backend.apply(Settings(**{**base.__dict__, "countdown_seconds": 0}))
    assert not path.exists()


def test_choisir_reel_dans_la_page_vaut_activation_explicite(backend):
    from faaall_alarm.settings_backend import config_values_from_settings

    base = backend[0].load()
    live = Settings(**{**base.__dict__, "sms_mode": "live"})
    assert config_values_from_settings(live)["sms_live_confirmed"] is True
    assert config_values_from_settings(base)["sms_live_confirmed"] is False


def test_apercu_simule_hors_webcam_et_png_aux_dimensions_attendues_avec_la_webcam(tmp_path):
    runner = ScenarioRunner(get_scenario("chute_debout"))
    services = LocalSettings(tmp_path / "r.json", {})
    simulated = AppSettingsBackend(runner.core, services, SourceKind.VIDEO_FILE, lambda: None)
    assert simulated.preview(0) is None
    frame = np.full((480, 640, 3), 90, np.uint8)
    webcam = AppSettingsBackend(runner.core, services, SourceKind.WEBCAM, lambda: frame)
    png = webcam.preview(runner.core.config.camera_index)
    assert cv2.imdecode(np.frombuffer(png, np.uint8), cv2.IMREAD_COLOR).shape == (253, 450, 3)


def test_les_reglages_appliques_reconstruisent_les_services_seulement_au_repos():
    runner = ScenarioRunner(get_scenario("chute_debout"))
    built = []
    runner.core._services_factory = lambda config: (built.append(config.countdown_seconds) or (
        runner.gateway, runner.core.interpreter, runner.voice))
    runner.at(8.0, lambda run: run.core.apply_config(AppConfig(**{**run.config.to_dict(), "countdown_seconds": 30.0})))
    runner.run(until_s=12.0)  # incident ouvert : rien n'est reconstruit
    assert built == []
    runner.core.engine.cancel()
    runner.core.tick()
    assert built == [30.0]
