from dataclasses import replace
from types import SimpleNamespace
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from faaall_alarm.clock import ManualClock
from faaall_alarm.config import AppConfig, apply_config
from faaall_alarm.http import ServiceError
from faaall_alarm.integration import build_sms_gateway
from faaall_alarm.model import FallCause, SourceKind
from faaall_alarm.sms_contract import AlertRequest, ReplyState, SmsStatus
from faaall_alarm.adapters import ReplyInterpreterAdapter
from faaall_alarm.simulation.runner import ScenarioRunner, demo_config
from faaall_alarm.simulation.scenarios import get_scenario
from faaall_alarm.telegram import TelegramChannel, TelegramError, TelegramGateway
from faaall_alarm.settings_page import SettingsPage
from faaall_alarm.settings import configuration
from faaall_alarm.app import AppOptions, MonitoringApp


def test_normal_webcam_start_selects_telegram_without_authorizing_send(monkeypatch, tmp_path):
    monkeypatch.setattr("faaall_alarm.app.read_config", lambda path: AppConfig(sms_mode="simulated"))
    app = MonitoringApp.__new__(MonitoringApp)
    config, source = app._resolve_config_and_kind(AppOptions())
    assert source is SourceKind.WEBCAM
    assert (config.sms_mode, config.sms_live_confirmed) == ("telegram", False)
    gateway = build_sms_gateway(config, source, ManualClock(), tmp_path, {})
    assert "à activer" in gateway.check_availability().value
    submission = gateway.submit_alert(REQUEST)
    assert submission.status is SmsStatus.FAILED
    assert "Enregistrer" in submission.error
    video_config, _ = app._resolve_config_and_kind(AppOptions(video_path="factice.mp4"))
    assert video_config.sms_mode == "simulated"


NOW = 1_800_000_000
TOKEN = "123:token_factice"
WHEN = datetime.fromtimestamp(NOW, timezone.utc)
REQUEST = AlertRequest("incident-factice", WHEN, FallCause.COLLISION, "Marie",
                       "123 rue Exemple", "", "")


def test_settings_can_send_another_test_before_receiving_a_reply(tmp_path, monkeypatch):
    channel, api = channel_and_api(tmp_path)
    pair(channel, api)
    page = SettingsPage.__new__(SettingsPage)
    scheduled = []
    page.window = SimpleNamespace(after=lambda delay, callback: scheduled.append(callback))
    page.backend = SimpleNamespace(allows_telegram_test=True)
    page.telegram = channel
    page.telegram_busy = False
    page.telegram_test = None
    page.telegram_status = SimpleNamespace(set=lambda value: None)
    page.run_telegram = lambda operation, completed: completed(operation())
    monkeypatch.setattr("faaall_alarm.settings_page.messagebox.askyesno", lambda *args, **kwargs: True)

    page.send_telegram_test()
    first = page.telegram_test
    page.send_telegram_test()
    second = page.telegram_test

    assert len(api.posts) == 2
    assert first[0] != second[0]
    scheduled[0]()
    assert page.telegram_test is second


class FakeTelegram:
    def __init__(self):
        self.updates = []
        self.posts = []
        self.failure = None
        self.now = NOW

    def __call__(self, url, headers, body):
        method = url.rsplit("/", 1)[1]
        form = parse_qs(body.decode())
        if method == "getMe":
            result = {"id": 123, "username": "faaall_factice_bot"}
        elif method == "getChat":
            result = {"id": 42, "type": "private"}
        elif method == "getUpdates":
            result = [item for item in self.updates if item["update_id"] >= int(form["offset"][0])]
        else:
            assert method == "sendMessage"
            self.posts.append(form)
            if self.failure:
                raise self.failure
            result = {"message_id": len(self.posts)}
        return {"ok": True, "result": result}

    def update(self, text, chat=42, quote=None, kind="private", date=None):
        self.updates.append({"update_id": len(self.updates) + 1, "message": {
            "chat": {"id": chat, "type": kind}, "from": {"id": chat},
            "text": text, "date": self.now if date is None else date,
            "reply_to_message": {"message_id": quote},
        }})


def channel_and_api(tmp_path):
    api = FakeTelegram()
    return TelegramChannel(tmp_path / "telegram.sqlite3", TOKEN, api, lambda: api.now), api


def pair(channel, api):
    code = parse_qs(urlsplit(channel.start_pairing()).query)["start"][0]
    api.update(f"/start {code}", chat=99, kind="group")
    api.update("/start faux", chat=99)
    assert not channel.pairing_ready()
    api.update(f"/start {code}")
    assert channel.pairing_ready()
    assert channel.chat() is None
    channel.confirm_pairing()
    assert channel.chat() == 42


def test_pairing_expiration_and_bot_change(tmp_path):
    channel, api = channel_and_api(tmp_path)
    channel.start_pairing()
    api.now += 601
    with pytest.raises(TelegramError, match="expiré"):
        channel.pairing_ready()
    pair(channel, api)
    assert TelegramChannel(channel.database, "124:other", api).chat() is None


def test_restart_recovers_saved_channel_and_pairing(tmp_path, monkeypatch):
    channel, api = channel_and_api(tmp_path)
    pair(channel, api)
    path = tmp_path / "config.local.json"
    monkeypatch.setattr("faaall_alarm.config.load_dotenv", lambda: None)
    assert apply_config({"sms_mode": "telegram", "sms_live_confirmed": True,
                         "monitored_first_name": "Marie", "alert_address": "123 rue Exemple"}, path).ok
    app = MonitoringApp.__new__(MonitoringApp)
    config, source = app._resolve_config_and_kind(AppOptions(config_path=path))
    gateway = build_sms_gateway(config, source, ManualClock(), tmp_path,
                                {"TELEGRAM_BOT_TOKEN": TOKEN}, api)
    assert gateway.chat == 42
    assert gateway.check_availability().value == "Bot Telegram accessible"
    assert not api.posts


def test_service_configuration_uses_project_path_from_any_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    paths = []

    def fake_dotenv(path, *, interpolate):
        paths.append(path)
        return {"TELEGRAM_BOT_TOKEN": TOKEN}

    monkeypatch.setattr("faaall_alarm.settings.dotenv_values", fake_dotenv)
    assert configuration()["TELEGRAM_BOT_TOKEN"] == TOKEN
    assert paths[0].is_absolute() and paths[0].name == ".env"


def test_telegram_log_records_pairing_and_refusal_without_private_values(tmp_path):
    channel, api = channel_and_api(tmp_path)
    channel.log_path = tmp_path / "telegram-logs.txt"
    pair(channel, api)
    api.failure = ServiceError(403)
    channel.send("test-factice", "Texte privé factice", "test", 42)
    log = channel.log_path.read_text(encoding="utf-8")
    assert "code reçu et validé" in log
    assert "proche confirmé" in log
    assert "sendMessage · échec · HTTP 403" in log
    assert "Texte privé factice" not in log and TOKEN not in log
    assert all(message["message"]["text"] not in log for message in api.updates)


def test_telegram_alert_and_test_replies_are_isolated_and_acknowledged(tmp_path):
    channel, api = channel_and_api(tmp_path)
    pair(channel, api)
    gateway = TelegramGateway(channel)
    result = gateway.submit_alert(REQUEST)
    assert result.status is SmsStatus.TELEGRAM_ACCEPTED
    assert "COLLISION" in api.posts[0]["text"][0]
    assert gateway.submit_alert(REQUEST) == result
    channel.send("test-factice", "Message de test", "test", 42)
    api.update("Étranger", chat=99, quote=1)
    api.update("Tardif", quote=1, date=NOW + 1801)
    api.update("Sans citation")
    api.update("Réponse au test", quote=2)
    api.update("J’arrive dans 20 min", quote=1)
    (reply,) = gateway.fetch_replies(WHEN)
    assert reply.incident_ref == REQUEST.incident_id
    assert reply.body == "J’arrive dans 20 min"
    gateway.acknowledge_reply(reply.message_id)
    restarted = TelegramChannel(channel.database, TOKEN, api, lambda: api.now)
    assert restarted.replies("incident", 42, WHEN) == []
    (test_reply,) = restarted.replies("test", 42, WHEN, "test-factice")
    assert test_reply.body == "Réponse au test"
    restarted.acknowledge_reply(test_reply.message_id)
    assert restarted.replies("test", 42, WHEN) == []


@pytest.mark.parametrize(("failure", "status"), [(403, SmsStatus.FAILED), (None, SmsStatus.TELEGRAM_UNKNOWN)])
def test_telegram_provider_failure_never_retries(tmp_path, failure, status):
    channel, api = channel_and_api(tmp_path)
    pair(channel, api)
    api.failure = ServiceError(failure)
    gateway = TelegramGateway(channel)
    assert gateway.submit_alert(REQUEST).status is status
    assert gateway.submit_alert(REQUEST).status is status
    assert len(api.posts) == 1


@pytest.mark.parametrize("source", [SourceKind.VIDEO_FILE, SourceKind.SIMULATION, SourceKind.WEBCAM])
def test_telegram_requires_live_webcam_and_confirmation(tmp_path, source):
    config = replace(AppConfig(), sms_mode="telegram", sms_live_confirmed=source is not SourceKind.WEBCAM)
    def forbidden(*args):
        pytest.fail("Aucun appel réseau en simulation")
    gateway = build_sms_gateway(config, source, ManualClock(), tmp_path,
                                {"TELEGRAM_BOT_TOKEN": TOKEN}, forbidden)
    expected = SmsStatus.FAILED if source is SourceKind.WEBCAM else SmsStatus.SIMULATED
    assert gateway.submit_alert(REQUEST).status is expected


def test_surveillance_attaches_quoted_telegram_reply(tmp_path):
    channel, api = channel_and_api(tmp_path)
    config = demo_config(sms_mode="telegram", sms_live_confirmed=True)
    runner = ScenarioRunner(get_scenario("chute_debout"), config=config, source_kind=SourceKind.WEBCAM)
    channel.now = lambda: runner.clock.wall().timestamp()
    pair(channel, api)
    runner.gateway = runner.core.gateway = TelegramGateway(channel)
    runner.core.interpreter = ReplyInterpreterAdapter(None)
    runner.at(24.0, lambda run: api.update(
        "Je suis en route, j’arrive dans environ 20 minutes", quote=1, date=int(channel.now())))
    result = runner.run(until_s=40.0)
    assert len(api.posts) == 1, result.log_text
    view = result.core.engine.view()
    assert view.sms_status is SmsStatus.TELEGRAM_ACCEPTED
    assert (view.reply.state, view.reply.delay_minutes) == (ReplyState.EN_ROUTE, 20)
