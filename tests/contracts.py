"""Vérifications de contrat réutilisables par les adaptateurs du lot SMS (voir docs/RACCORDEMENT.md).

Exemple côté adaptateur Twilio, avec un faux client HTTP :

    from tests.contracts import assert_gateway_contract
    assert_gateway_contract(TwilioGateway(config, http=FakeHttp()))
"""

from __future__ import annotations

from datetime import datetime

from faaall_alarm.model import FallCause
from faaall_alarm.sms_contract import (
    AlertRequest, IncomingSms, InterpretationUnavailable, ReplyInterpretation, ReplyInterpreter, SmsAvailability,
    SmsGateway, SmsStatus, SmsSubmission,
)

FICTIONAL_REQUEST = AlertRequest(
    incident_id="INC-001-14h32", occurred_at=datetime(2026, 1, 15, 14, 32), cause=FallCause.INDIVIDUAL,
    first_name="Marie", address="123, rue Exemple, Montréal", recipient_number="+15145550123",
    sender_number="+15145550199",
)


def assert_gateway_contract(gateway: SmsGateway) -> None:
    """Contrat minimal d'un adaptateur SMS : types retournés et états autorisés. Aucun envoi réel n'est fait
    tant que `gateway` est un adaptateur simulé ou branché sur un faux client réseau."""
    assert isinstance(gateway, SmsGateway)
    assert isinstance(gateway.check_availability(), SmsAvailability)
    submission = gateway.submit_alert(FICTIONAL_REQUEST)
    assert isinstance(submission, SmsSubmission) and isinstance(submission.status, SmsStatus)
    assert submission.status is not SmsStatus.DELIVERED, "une soumission ne peut pas être « Livré » d'emblée"
    for number in (FICTIONAL_REQUEST.recipient_number, FICTIONAL_REQUEST.sender_number):
        assert number not in (submission.error or ""), "l'erreur ne doit contenir aucun numéro"
    if submission.provider_ref:
        assert isinstance(gateway.get_status(submission.provider_ref), SmsStatus)
    replies = gateway.fetch_replies(datetime(2026, 1, 15, 0, 0))
    assert all(isinstance(reply, IncomingSms) for reply in replies)
    assert len({reply.message_id for reply in replies}) == len(replies), "réponses dédupliquées par message_id"


def assert_interpreter_contract(interpreter: ReplyInterpreter) -> None:
    """Contrat d'un interpréteur : un état valide, un délai seulement s'il est annoncé, l'échec par exception."""
    assert isinstance(interpreter, ReplyInterpreter)
    explicit = interpreter.interpret("Je suis en route, j'arrive dans environ 20 minutes")
    assert isinstance(explicit, ReplyInterpretation)
    assert explicit.announced_delay_minutes == 20
    assert interpreter.interpret("Je suis en route").announced_delay_minutes is None
    assert interpreter.interpret("OK").announced_delay_minutes is None
    try:
        interpreter.interpret("Tu es à 20 minutes ?")
    except InterpretationUnavailable:
        pass
