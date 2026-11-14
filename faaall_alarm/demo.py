"""Scénario factice et reproductible, indépendant du moteur de surveillance."""

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from .replies import DEMO_REPLY, Interpretation, ReplyTarget, associate, interpret_simulated
from .sms import Alert, Incoming, SmsConfig, SmsService, Source, Submission


@dataclass
class Demo:
    sms: SmsService
    alert: Alert

    @classmethod
    def create(cls, database: Path) -> "Demo":
        sms = SmsService(database, SmsConfig(sender="+15145550101", recipient="+15145550102"),
                         Source.DEMO)
        alert = Alert(f"demo-{uuid4()}", datetime.now().astimezone(),
                      "123, rue Exemple, Montréal", "Marie", True)
        return cls(sms, alert)

    def send(self) -> Submission:
        return self.sms.submit(self.alert)

    def reply(self, body: str = DEMO_REPLY) -> tuple[Incoming, Interpretation]:
        submission = self.sms.lookup(self.alert.incident_id)
        if submission is None:
            raise ValueError("Simulez d’abord l’envoi de l’alerte.")
        target = ReplyTarget(self.alert.incident_id, submission.attempted_at,
                             self.sms.config.recipient, self.sms.config.sender, True)
        self.sms.inject_reply(body, f"demo-{uuid4()}", self.sms.now())
        for message in self.sms.poll_replies(target.submitted_at):
            if associate(message, [target]) is not None:
                result = interpret_simulated(message.body)
                self.sms.acknowledge_reply(message.sid)
                return message, result
        raise RuntimeError("La réponse simulée est introuvable.")
