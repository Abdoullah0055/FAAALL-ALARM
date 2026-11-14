import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Event
from urllib.parse import parse_qs, urlsplit

from faaall_alarm.demo import Demo
from faaall_alarm.http import ServiceError
from faaall_alarm.replies import (Care, Interpretation, OpenAI, ReplyTarget, associate,
                                  interpret_simulated, newer)
from faaall_alarm.sms import Alert, Delivery, Incoming, Mode, SmsConfig, SmsService, Source


NOW = datetime(2026, 11, 14, 14, 32, tzinfo=timezone.utc)
CONFIG = SmsConfig(Mode.LIVE, "+15145550101", "+15145550102", "AC" + "a" * 32, "factice")
SID = "SM" + "a" * 32
ALERT = Alert("incident-factice", NOW, "123, rue Exemple, Montréal", "Marie", True)


class SmsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "sms.sqlite3"

    def service(self, transport, source=Source.CAMERA, config=CONFIG, now=NOW):
        return SmsService(self.database, config, source, transport=transport, now=lambda: now)

    def test_simulation_isolates_provider_even_with_valid_config(self):
        def forbidden(*args):
            self.fail("Le canal réel est interdit en simulation.")

        for source in (Source.DEMO, Source.MP4, Source.TEST):
            with self.subTest(source=source):
                service = self.service(forbidden, source)
                self.assertEqual(service.check(), "SMS simulé")
                self.assertEqual(service.submit(ALERT).state, Delivery.SIMULATED)
                self.assertEqual(service.refresh(ALERT.incident_id).state, Delivery.SIMULATED)
                self.assertEqual(service.poll_replies(NOW), [])
        simulated_camera = self.service(forbidden, config=replace(CONFIG, mode=Mode.SIMULATED))
        self.assertEqual(simulated_camera.submit(ALERT).state, Delivery.SIMULATED)
        default_service = self.service(forbidden, config=SmsConfig())
        self.assertEqual(default_service.submit(replace(ALERT, address="", first_name="")).state,
                         Delivery.SIMULATED)

    def test_format_and_local_time(self):
        local = NOW.astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(replace(ALERT, occurred_at=local).message(),
                         "ALERTE : MARIE EST TOMBÉE À 09:32 à 123, rue Exemple, Montréal !")
        self.assertIn("CHUTE POSSIBLE", replace(ALERT, name_reliable=False).message())
        self.assertIn("COLLISION ET CHUTE POSSIBLES", replace(ALERT, collision=True).message())
        with self.assertRaises(ValueError):
            replace(ALERT, occurred_at=NOW.replace(tzinfo=None)).message()

    def test_submission_status_and_restart(self):
        calls = []

        def transport(url, headers, body=None):
            calls.append((url, body))
            if body:
                self.assertEqual(parse_qs(body.decode())["Body"], [ALERT.message()])
                self.assertEqual(parse_qs(body.decode())["To"], [CONFIG.recipient])
                return {"sid": SID, "status": "sent"}
            return {"status": "delivered"}

        service = self.service(transport)
        self.assertEqual(service.submit(ALERT).state, Delivery.PENDING)
        restarted = self.service(transport)
        self.assertEqual(restarted.submit(ALERT).provider_sid, SID)
        self.assertEqual(restarted.refresh(ALERT.incident_id).state, Delivery.DELIVERED)
        self.assertEqual(restarted.refresh(ALERT.incident_id).state, Delivery.DELIVERED)
        self.assertEqual(len(calls), 2)
        self.assertTrue(calls[-1][0].endswith(f"/{SID}.json"))

    def test_timeout_http_errors_and_crash_do_not_resend(self):
        for status, expected in ((None, Delivery.UNKNOWN), (400, Delivery.FAILED),
                                 (408, Delivery.UNKNOWN), (500, Delivery.UNKNOWN)):
            alert = replace(ALERT, incident_id=f"incident-{status}")

            def transport(*args):
                # L'intention est lisible depuis une connexion distincte avant le POST.
                self.assertEqual(self.service(transport).lookup(alert.incident_id).state, Delivery.UNKNOWN)
                raise ServiceError(status)

            self.assertEqual(self.service(transport).submit(alert).state, expected)
            self.assertEqual(self.service(lambda *args: self.fail("Renvoi")).submit(alert).state, expected)
        with self.assertRaises(KeyboardInterrupt):
            def crash(*args):
                raise KeyboardInterrupt()
            self.service(crash).submit(ALERT)
        self.assertEqual(self.service(lambda *args: self.fail("Renvoi")).submit(ALERT).state, Delivery.UNKNOWN)

    def test_concurrent_claim(self):
        entered, release = Event(), Event()
        calls = []

        def transport(*args):
            calls.append(args)
            entered.set()
            self.assertTrue(release.wait(3))
            return {"sid": SID, "status": "queued"}

        service = self.service(transport)
        with ThreadPoolExecutor(max_workers=2) as worker:
            first = worker.submit(service.submit, ALERT)
            try:
                self.assertTrue(entered.wait(3))
                self.assertEqual(self.service(transport).submit(ALERT).state, Delivery.UNKNOWN)
            finally:
                release.set()
            self.assertEqual(first.result().state, Delivery.PENDING)
        self.assertEqual(len(calls), 1)

    def test_real_manual_test_requires_explicit_confirmation(self):
        def transport(url, headers, body):
            self.assertIn("SMS+de+test", body.decode())
            return {"sid": SID, "status": "queued"}

        service = self.service(transport, Source.MANUAL_SMS)
        with self.assertRaises(ValueError):
            service.submit(ALERT)
        self.assertEqual(service.submit(ALERT, confirmed_test=True).state, Delivery.PENDING)

    def test_delivery_polling_failure_then_explicit_failure(self):
        def transport(url, headers, body=None):
            if body:
                return {"sid": SID, "status": "queued"}
            raise ServiceError()

        self.service(transport).submit(ALERT)
        self.assertEqual(self.service(transport).refresh(ALERT.incident_id).state, Delivery.UNKNOWN)
        self.assertEqual(self.service(lambda *args: {"status": "undelivered"}).refresh(
            ALERT.incident_id).state, Delivery.FAILED)

    def test_inbox_filters_and_deduplicates_across_pages_and_restarts(self):
        valid = {"sid": SID, "direction": "inbound", "from": CONFIG.recipient, "to": CONFIG.sender,
                 "body": "OK", "date_sent": "Sat, 14 Nov 2026 14:32:15 +0000"}

        def transport(url, headers):
            parsed = urlsplit(url)
            query = parse_qs(parsed.query)
            if "Page" in query:
                return {"messages": [valid], "next_page_uri": None}
            self.assertEqual(query["From"], [CONFIG.recipient])
            self.assertEqual(query["To"], [CONFIG.sender])
            self.assertIn("DateSent>", query)
            return {"messages": [valid, valid | {"from": "+15145550103"},
                                 valid | {"to": "+15145550103"},
                                 valid | {"direction": "outbound-api"},
                                 valid | {"date_sent": "Sat, 14 Nov 2026 14:31:59 +0000"},
                                 valid | {"date_sent": "Sat, 14 Nov 2026 14:32:21 +0000"}],
                    "next_page_uri": f"{parsed.path}?Page=1"}

        service = self.service(transport, now=NOW + timedelta(seconds=20))
        (message,) = service.poll_replies(NOW)
        self.assertEqual(message.sid, SID)
        service.acknowledge_reply(message.sid)
        self.assertEqual(self.service(transport, now=service.now()).poll_replies(NOW), [])

    def test_demo_runs_both_directions_without_provider(self):
        demo = Demo.create(self.database)
        self.assertEqual(demo.send().state, Delivery.SIMULATED)
        _, interpretation = demo.reply()
        self.assertEqual(interpretation, Interpretation(Care.ON_WAY, 20))


class ReplyTests(unittest.TestCase):
    def test_association_rejects_ambiguous_late_wrong_and_older_replies(self):
        target = ReplyTarget(ALERT.incident_id, NOW, CONFIG.recipient, CONFIG.sender, True)
        message = Incoming(SID, NOW + timedelta(seconds=10), CONFIG.recipient, CONFIG.sender, "OK")
        self.assertEqual(associate(message, [target]), target)
        for rejected in (replace(message, sender="+15145550103"), replace(message, recipient="+15145550103"),
                         replace(message, received_at=NOW),
                         replace(message, received_at=NOW + timedelta(minutes=31))):
            self.assertIsNone(associate(rejected, [target]))
        closed = replace(target, incident_id="ancienne", open=False, submitted_at=NOW - timedelta(minutes=1))
        self.assertIsNone(associate(message, [closed]))
        self.assertIsNone(associate(message, [closed, target]))
        self.assertIsNone(associate(message, [replace(closed, submitted_at=NOW - timedelta(hours=2)), target]))
        self.assertFalse(newer(message, replace(message, received_at=NOW + timedelta(seconds=20))))

    def test_offline_replies(self):
        cases = [
            ("J’arrive dans 20 minutes", Care.ON_WAY, 20),
            ("Je suis en route", Care.ON_WAY, None),
            ("OK", Care.REVIEW, None),
            ("Je ne peux pas venir", Care.UNAVAILABLE, None),
            ("Finalement je ne peux pas venir", Care.UNAVAILABLE, None),
            ("Tu arrives dans 20 minutes ?", Care.REVIEW, None),
            ("J’arrive mais je ne peux pas venir", Care.REVIEW, None),
            ("Je m’en occupe", Care.CONFIRMED, None),
        ]
        for body, state, delay in cases:
            with self.subTest(body=body):
                self.assertEqual(interpret_simulated(body), Interpretation(state, delay))

    def test_remote_structure_redaction_and_delay_evidence(self):
        def model(state, eta):
            def transport(url, headers, body):
                request = json.loads(body)
                self.assertEqual(url, "https://api.openai.com/v1/responses")
                self.assertEqual(headers["Authorization"], "Bearer factice")
                self.assertEqual(request["model"], "gpt-6-luna")
                self.assertFalse(request["store"])
                self.assertEqual(request["reasoning"], {"effort": "none"})
                self.assertTrue(request["text"]["format"]["strict"])
                self.assertNotIn("rue Exemple", request["input"])
                self.assertNotIn("514", request["input"])
                content = json.dumps({"state": state, "eta_minutes": eta})
                return {"status": "completed", "output": [
                    {"type": "reasoning", "summary": []},
                    {"type": "message", "role": "assistant", "status": "completed",
                     "content": [{"type": "output_text", "text": content}]},
                ]}
            return OpenAI("factice", transport=transport)

        self.assertEqual(model(Care.ON_WAY, 20).interpret("J’arrive dans 20 minutes"),
                         Interpretation(Care.ON_WAY, 20))
        self.assertEqual(model(Care.ON_WAY, 20).interpret(
            "J’arrive dans 20 minutes. 123, rue Exemple, +1 514 555 0101",
            redact=("123, rue Exemple",)), Interpretation(Care.ON_WAY, 20))
        self.assertEqual(model(Care.ON_WAY, 42).interpret("J’arrive dans 20 minutes"),
                         Interpretation(Care.ON_WAY))
        self.assertEqual(model(Care.UNAVAILABLE, None).interpret("Finalement je ne peux pas venir"),
                         Interpretation(Care.UNAVAILABLE))
        self.assertEqual(model(Care.ON_WAY, 20).interpret("OK"), Interpretation())
        self.assertEqual(model(Care.ON_WAY, 20).interpret("Je ne peux pas venir"), Interpretation())
        self.assertTrue(model(Care.ON_WAY, True).interpret("J’arrive dans 20 minutes").service_unavailable)

    def test_remote_unavailable_and_invalid_payload(self):
        def offline(*args):
            raise ServiceError(503)

        for model in (OpenAI(""), OpenAI("factice", transport=offline),
                      OpenAI("factice", transport=lambda *args: {"status": "completed", "output": []}),
                      OpenAI("factice", transport=lambda *args: {"status": "incomplete", "output": []})):
            self.assertEqual(model.interpret("J’arrive dans 20 minutes"),
                             Interpretation(service_unavailable=True))

    def test_openai_refusal_requires_review(self):
        def transport(url, headers, body):
            self.assertEqual(json.loads(body)["model"], "modele-factice")
            return {"status": "completed", "output": [{
                "type": "message", "role": "assistant", "status": "completed",
                "content": [{"type": "refusal", "refusal": "Refus factice"}],
            }]}

        model = OpenAI("factice", "modele-factice", transport)
        self.assertEqual(model.interpret("J’arrive dans 20 minutes"), Interpretation())
