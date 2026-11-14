"""SMS et interpréteur simulés, voix locale eSpeak NG et textes de l'écran."""

from __future__ import annotations

from datetime import datetime

import pytest

from faaall_alarm.clock import ManualClock
from faaall_alarm.sms_contract import (
    AlertRequest, InterpretationUnavailable, ReplyState, SmsAvailability, SmsStatus,
)
from faaall_alarm.sms_simulated import SimulatedReplyInterpreter, SimulatedSmsGateway
from faaall_alarm.model import FallCause
from faaall_alarm.incident import ReplyRecord
from faaall_alarm.voice import EspeakVoice, UnavailableVoice, find_espeak
from faaall_alarm.ui.viewmodel import format_countdown, reply_sentence, main_posture
from faaall_alarm.model import PersonView, Posture


@pytest.mark.parametrize("text, state, delay", [
    ("Je suis en route, j'arrive dans environ 20 minutes", ReplyState.EN_ROUTE, 20),
    ("Je suis en route", ReplyState.EN_ROUTE, None),
    ("J'arrive dans 1 heure", ReplyState.EN_ROUTE, 60),
    ("OK", ReplyState.TO_CHECK, None),
    ("Tu es à 20 minutes ?", ReplyState.TO_CHECK, None),
    ("Finalement je ne peux pas venir", ReplyState.UNAVAILABLE, None),
    ("Je ne suis pas en route, impossible ce soir", ReplyState.UNAVAILABLE, None),
    ("Je m'en occupe", ReplyState.CONFIRMED, None),
])
def test_interpreteur_simule_regles(text, state, delay):
    result = SimulatedReplyInterpreter().interpret(text)
    assert (result.state, result.announced_delay_minutes) == (state, delay)


def test_interpreteur_coupe_leve_l_exception_du_contrat():
    with pytest.raises(InterpretationUnavailable):
        SimulatedReplyInterpreter(available=False).interpret("Je suis en route")


def test_passerelle_simulee_soumet_sans_reseau_et_livre_chaque_reponse_une_fois():
    clock = ManualClock()
    gateway = SimulatedSmsGateway(clock)
    request = AlertRequest("INC-001-14h32", clock.wall(), FallCause.INDIVIDUAL, "Marie", "123, rue Exemple",
                           "+15145550123", "+15145550199")
    submission = gateway.submit_alert(request)
    assert submission.status is SmsStatus.SIMULATED and submission.provider_ref and gateway.submitted_requests == [request]
    assert gateway.check_availability() is SmsAvailability.SIMULATED
    gateway.inject_reply("Je suis en route", "+15145550123", "+15145550199", message_id="m1")
    since = datetime(2026, 1, 15, 14, 0, 0)
    assert [sms.message_id for sms in gateway.fetch_replies(since)] == ["m1"]
    assert gateway.fetch_replies(since) == []  # déduplication par identifiant


def test_passerelle_simulee_peut_simuler_refus_et_reseau_incertain():
    clock = ManualClock()
    request = AlertRequest("X", clock.wall(), FallCause.INDIVIDUAL, None, "", "+15145550123", "+15145550199")
    assert SimulatedSmsGateway(clock, SmsStatus.FAILED).submit_alert(request).status is SmsStatus.FAILED
    assert SimulatedSmsGateway(clock, SmsStatus.UNKNOWN).submit_alert(request).status is SmsStatus.UNKNOWN


def test_voix_absente_retourne_un_echec_sans_exception():
    voice = EspeakVoice(executable=r"C:\introuvable\espeak-ng.exe")
    if find_espeak(r"C:\introuvable\espeak-ng.exe") is None:
        assert not voice.is_available()
    assert not UnavailableVoice().speak("test").ok


@pytest.mark.skipif(find_espeak() is None, reason="eSpeak NG n'est pas installé sur ce poste")
def test_espeak_ng_synthese_francaise_hors_ligne_produit_un_wav():
    wav = EspeakVoice().synthesize_wav("Une chute possible a été détectée.")
    assert wav[:4] == b"RIFF" and len(wav) > 5000


def test_formats_d_affichage():
    assert format_countdown(10.0) == "00:10" and format_countdown(0.2) == "00:01" and format_countdown(0) == "00:00"
    reply = ReplyRecord(ReplyState.EN_ROUTE, 20, datetime(2026, 1, 15, 14, 33))
    assert reply_sentence(reply, "Sophie") == "Sophie a répondu. En route. Arrivée estimée dans 20 min. (reçue à 14:33)"
    no_delay = ReplyRecord(ReplyState.EN_ROUTE, None, datetime(2026, 1, 15, 14, 33))
    assert "Délai non précisé" in reply_sentence(no_delay, "Sophie")
    assert "Indisponible" in reply_sentence(ReplyRecord(ReplyState.UNAVAILABLE, None, None), "Sophie")


def test_posture_principale_a_deux_personnes_privilegie_la_plus_critique():
    persons = [PersonView(1, "Personne 1", Posture.STANDING), PersonView(2, "Personne 2", Posture.FALLING)]
    assert main_posture(persons) is Posture.FALLING and main_posture([]) is Posture.NO_PERSON
