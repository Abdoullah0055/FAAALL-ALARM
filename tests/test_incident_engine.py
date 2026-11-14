"""Moteur d'incident : décompte, annulation, clôture, réarmement et rattachement des réponses.

Horloge contrôlable et ports factices : aucun test n'attend dix secondes réelles ni n'envoie de SMS.
"""

from __future__ import annotations

from datetime import timedelta

from faaall_alarm.incident import IncidentState, ReplyRouting
from faaall_alarm.model import DetectionEvent, EventKind, FallCause
from faaall_alarm.simulation.runner import DEMO_CONTACT_NUMBER, DEMO_TWILIO_NUMBER
from faaall_alarm.sms_contract import (
    IncomingSms, ReplyInterpretation, ReplyState, SmsStatus, SmsSubmission,
)

from .conftest import fall_event, recovered_event


def open_incident(engine, clock, **kwargs):
    engine.handle_event(fall_event(**kwargs))
    return engine.view()


def reach_deadline(engine, clock, seconds=10.0):
    clock.advance(seconds)
    engine.tick()


def sms_from_contact(clock, body="Je suis en route", message_id="m1", received_at=None, sender=DEMO_CONTACT_NUMBER,
                     recipient=DEMO_TWILIO_NUMBER):
    return IncomingSms(message_id, sender, recipient, body, received_at or clock.wall())


# -- ouverture, voix et décompte --------------------------------------------------------------------
def test_chute_confirmee_ouvre_un_incident_parle_une_fois_et_demarre_le_decompte(engine, clock, ports):
    view = open_incident(engine, clock)
    assert view.state is IncidentState.COUNTDOWN and view.remaining_s == 10.0
    assert view.headline == "Chute possible" and view.can_cancel and not view.can_close
    assert ports.spoken == [(view.incident_id, "Message d'alerte de test")]


def test_decompte_se_base_sur_le_temps_ecoule_pas_sur_les_images(engine, clock):
    open_incident(engine, clock)
    clock.advance(4.0)  # aucune image analysée entre-temps
    assert engine.view().remaining_s == 6.0
    clock.advance(7.0)
    engine.tick()
    assert engine.view().state is IncidentState.HANDLED


def test_decompte_reglable(clock, ports):
    from faaall_alarm.incident import IncidentEngine

    from .conftest import default_settings

    custom = IncidentEngine(clock, ports, lambda: default_settings(countdown_seconds=3.0))
    custom.handle_event(fall_event())
    clock.advance(3.0)
    custom.tick()
    assert custom.view().state is IncidentState.HANDLED


def test_voix_ne_recommence_pas_sur_une_nouvelle_confirmation_du_meme_incident(engine, clock, ports):
    open_incident(engine, clock)
    engine.handle_event(fall_event())  # même personne, même incident
    engine.handle_event(fall_event(track=9))  # nouvel identifiant de suivi, posture basse conservée
    assert len(ports.spoken) == 1 and len(engine.incidents()) == 1


# -- échéance, soumission unique ---------------------------------------------------------------------
def test_echeance_soumet_une_seule_demande_avec_les_donnees_du_message(engine, clock, ports):
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    reach_deadline(engine, clock, 5.0)  # ticks suivants : aucun doublon
    request = ports.submitted[0]
    assert len(ports.submitted) == 1
    assert request.recipient_number == DEMO_CONTACT_NUMBER and request.sender_number == DEMO_TWILIO_NUMBER
    assert request.first_name == "Marie" and request.cause is FallCause.INDIVIDUAL
    assert request.occurred_at == engine.incidents()[0].created_wall


def test_intention_d_envoi_enregistree_durablement_avant_l_appel_reseau(engine, clock, ports):
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    assert ports.calls.index("record:sms_intention") < ports.calls.index("submit_sms")
    assert dict(ports.records)["sms_intention"]["durable"] is True


def test_prenom_omis_si_un_temoin_etait_visible_ou_cause_incertaine(engine, clock, ports):
    engine.note_person_count(2)
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    assert ports.submitted[0].first_name is None


def test_prenom_omis_pour_une_collision(engine, clock, ports):
    open_incident(engine, clock, cause=FallCause.COLLISION)
    reach_deadline(engine, clock)
    assert ports.submitted[0].first_name is None and ports.submitted[0].cause is FallCause.COLLISION


# -- annulation et arbitrage -------------------------------------------------------------------------------
def test_annulation_avant_l_echeance_empeche_toute_soumission(engine, clock, ports):
    open_incident(engine, clock)
    clock.advance(9.9)
    assert engine.cancel() is True
    reach_deadline(engine, clock, 5.0)
    assert ports.submitted == [] and engine.view() is None
    assert engine.incidents()[0].state is IncidentState.CANCELLED


def test_annulation_apres_la_decision_d_envoyer_est_refusee(engine, clock, ports):
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    assert engine.cancel() is False
    assert len(ports.submitted) == 1 and engine.view().can_close and not engine.view().can_cancel


def test_fermer_la_fenetre_n_est_pas_une_annulation(engine, clock):
    open_incident(engine, clock)
    clock.advance(3.0)
    assert engine.view() is not None  # aucune action « fermer » dans le moteur : le décompte continue
    assert engine.view().state is IncidentState.COUNTDOWN


def test_reprise_de_posture_avant_l_echeance_annule(engine, clock, ports):
    open_incident(engine, clock)
    clock.advance(4.0)
    engine.handle_event(recovered_event())
    reach_deadline(engine, clock, 10.0)
    assert ports.submitted == [] and engine.incidents()[0].state is IncidentState.CANCELLED


def test_deux_personnes_tombees_la_reprise_d_une_seule_n_annule_pas(engine, clock, ports):
    open_incident(engine, clock, cause=FallCause.COLLISION)
    engine.handle_event(fall_event(track=2, cause=FallCause.COLLISION))
    engine.handle_event(recovered_event(track=1))
    assert engine.view().state is IncidentState.COUNTDOWN
    engine.handle_event(recovered_event(track=2))
    assert engine.incidents()[0].state is IncidentState.CANCELLED


def test_une_deuxieme_chute_dans_l_incident_reste_un_seul_incident_et_une_seule_voix(engine, clock, ports):
    open_incident(engine, clock, cause=FallCause.COLLISION)
    engine.handle_event(fall_event(track=2, cause=FallCause.COLLISION))
    reach_deadline(engine, clock)
    assert len(engine.incidents()) == 1 and len(ports.spoken) == 1 and len(ports.submitted) == 1


def test_chute_sans_collision_puis_deuxieme_personne_devient_cause_indeterminee(engine, clock):
    open_incident(engine, clock)
    engine.handle_event(fall_event(track=2, cause=FallCause.INDIVIDUAL))
    assert engine.view().cause is FallCause.UNDETERMINED


def test_chute_individuelle_puis_collision_est_amelioree_en_collision(engine, clock):
    open_incident(engine, clock)
    engine.handle_event(fall_event(track=2, cause=FallCause.COLLISION))
    assert engine.view().cause is FallCause.COLLISION


# -- clôture et réarmement ---------------------------------------------------------------------------------
def test_cloture_manuelle_apres_soumission_ferme_l_incident(engine, clock):
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    assert engine.close() is True and engine.view() is None
    assert engine.incidents()[0].state is IncidentState.CLOSED


def test_pas_de_cloture_pendant_le_decompte(engine, clock):
    open_incident(engine, clock)
    assert engine.close() is False and engine.view().state is IncidentState.COUNTDOWN


def test_reprise_de_posture_apres_soumission_clot_l_incident(engine, clock):
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    engine.handle_event(recovered_event())
    assert engine.view() is None and engine.incidents()[0].state is IncidentState.CLOSED


def test_meme_posture_au_sol_apres_cloture_ne_relance_pas_l_alerte(engine, clock, ports):
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    engine.close()
    engine.handle_event(fall_event())  # le détecteur ne devrait pas le refaire, le moteur le refuse aussi
    assert engine.view() is None and len(engine.incidents()) == 1 and len(ports.spoken) == 1


def test_meme_posture_au_sol_apres_annulation_ne_relance_pas_l_alerte(engine, clock):
    open_incident(engine, clock)
    engine.cancel()
    engine.handle_event(fall_event())
    assert engine.view() is None and len(engine.incidents()) == 1


def test_nouvelle_chute_apres_reprise_cree_un_nouvel_incident(engine, clock, ports):
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    engine.close()
    engine.handle_event(recovered_event())  # reprise de posture : réarmement
    engine.handle_event(fall_event())
    assert len(engine.incidents()) == 2 and len(ports.spoken) == 2


def test_incident_garde_son_identifiant_quand_le_suivi_change(engine, clock):
    first = open_incident(engine, clock, track=1)
    engine.handle_event(fall_event(track=7))
    assert engine.view().incident_id == first.incident_id


def test_suspicion_en_cours_empeche_la_cloture_automatique(engine, clock):
    open_incident(engine, clock, cause=FallCause.COLLISION)
    engine.handle_event(DetectionEvent(EventKind.FALL_SUSPECTED, 0.0, (2,)))
    engine.handle_event(recovered_event(track=1))
    assert engine.view().state is IncidentState.COUNTDOWN  # la seconde personne est peut-être tombée


# -- panne de caméra -----------------------------------------------------------------------------------------
def test_perte_camera_apres_confirmation_le_decompte_continue(engine, clock, ports):
    open_incident(engine, clock)
    engine.set_camera_available(False)
    clock.advance(10.0)
    engine.tick()
    assert len(ports.submitted) == 1 and engine.view().camera_available is False
    assert "Caméra inactive" in ports.log_text


def test_perte_camera_sans_incident_ne_produit_aucun_sms(engine, clock, ports):
    engine.set_camera_available(False)
    clock.advance(60.0)
    engine.tick()
    assert ports.submitted == [] and engine.view() is None


# -- voix ----------------------------------------------------------------------------------------------------
def test_voix_indisponible_est_affichee_et_le_decompte_continue(engine, clock, ports):
    view = open_incident(engine, clock)
    engine.on_voice_result(view.incident_id, False, "eSpeak NG introuvable")
    assert engine.view().voice_ok is False and "Voix indisponible" in ports.log_text
    reach_deadline(engine, clock)
    assert len(ports.submitted) == 1


def test_voix_desactivee_dans_les_reglages_ne_parle_pas(clock, ports):
    from faaall_alarm.incident import IncidentEngine

    from .conftest import default_settings

    silent = IncidentEngine(clock, ports, lambda: default_settings(voice_enabled=False))
    silent.handle_event(fall_event())
    assert ports.spoken == [] and silent.view().voice_ok is False


# -- statut du SMS -------------------------------------------------------------------------------------------
def test_echec_sms_garde_l_alerte_locale_disponible(engine, clock, ports):
    view = open_incident(engine, clock)
    reach_deadline(engine, clock)
    engine.on_sms_result(view.incident_id, SmsSubmission(SmsStatus.FAILED, None, "refus"))
    assert engine.view().sms_status is SmsStatus.FAILED and engine.view().can_close


def test_resultat_reseau_inconnu_est_conserve_sans_renvoi_automatique(engine, clock, ports):
    view = open_incident(engine, clock)
    reach_deadline(engine, clock)
    engine.on_sms_result(view.incident_id, SmsSubmission(SmsStatus.UNKNOWN, None, "délai"))
    reach_deadline(engine, clock, 120.0)
    assert engine.view().sms_status is SmsStatus.UNKNOWN and len(ports.submitted) == 1
    assert "aucun renvoi automatique" in ports.log_text


def test_livre_exige_un_retour_explicite_et_n_est_pas_retrograde(engine, clock):
    view = open_incident(engine, clock)
    reach_deadline(engine, clock)
    engine.on_sms_result(view.incident_id, SmsSubmission(SmsStatus.PENDING, "SM-FACTICE"))
    assert engine.view().sms_status is SmsStatus.PENDING
    engine.on_sms_status(view.incident_id, SmsStatus.DELIVERED)
    engine.on_sms_status(view.incident_id, SmsStatus.PENDING)
    assert engine.view().sms_status is SmsStatus.DELIVERED


def test_exception_du_port_sms_marque_un_echec_sans_boucler(engine, clock, ports):
    ports.fail_submit_with = RuntimeError("panne")
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    reach_deadline(engine, clock, 30.0)
    assert engine.view().sms_status is SmsStatus.FAILED and ports.calls.count("submit_sms") == 1


# -- réponses du proche --------------------------------------------------------------------------------------
def submitted_incident(engine, clock):
    view = open_incident(engine, clock)
    reach_deadline(engine, clock)
    engine.on_sms_result(view.incident_id, SmsSubmission(SmsStatus.SIMULATED, "SIM-1"))
    return view.incident_id


def test_reponse_rattachee_puis_interpretee_met_a_jour_l_incident_sans_le_clore(engine, clock, ports):
    incident_id = submitted_incident(engine, clock)
    clock.advance(30.0)
    sms = sms_from_contact(clock)
    assert engine.handle_incoming_sms(sms) is ReplyRouting.ATTACHED
    assert ports.interpretation_requests == [(incident_id, "m1", "Je suis en route")]
    engine.on_reply_interpreted(incident_id, "m1", ReplyInterpretation(ReplyState.EN_ROUTE, 20))
    reply = engine.view().reply
    assert (reply.state, reply.delay_minutes, reply.received_at) == (ReplyState.EN_ROUTE, 20, sms.received_at)
    assert engine.view().state is IncidentState.HANDLED  # le SMS entrant ne ferme pas l'incident


def test_reponse_sans_delai_n_invente_aucun_delai(engine, clock):
    incident_id = submitted_incident(engine, clock)
    engine.handle_incoming_sms(sms_from_contact(clock))
    engine.on_reply_interpreted(incident_id, "m1", ReplyInterpretation(ReplyState.EN_ROUTE, None))
    assert engine.view().reply.delay_minutes is None


def test_echec_de_l_interpretation_affiche_reponse_a_verifier(engine, clock):
    incident_id = submitted_incident(engine, clock)
    engine.handle_incoming_sms(sms_from_contact(clock))
    engine.on_reply_interpreted(incident_id, "m1", None)
    assert engine.view().reply.needs_review and engine.view().reply.state is None


def test_ok_ambigu_donne_a_verifier(engine, clock):
    incident_id = submitted_incident(engine, clock)
    engine.handle_incoming_sms(sms_from_contact(clock, "OK"))
    engine.on_reply_interpreted(incident_id, "m1", ReplyInterpretation(ReplyState.TO_CHECK))
    assert engine.view().reply.needs_review


def test_reponse_dupliquee_traitee_une_seule_fois(engine, clock, ports):
    submitted_incident(engine, clock)
    sms = sms_from_contact(clock)
    assert engine.handle_incoming_sms(sms) is ReplyRouting.ATTACHED
    assert engine.handle_incoming_sms(sms) is ReplyRouting.DUPLICATE
    assert len(ports.interpretation_requests) == 1


def test_expediteur_inattendu_ignore_sans_mise_a_jour(engine, clock, ports):
    submitted_incident(engine, clock)
    assert engine.handle_incoming_sms(sms_from_contact(clock, sender="+15145559999")) is ReplyRouting.IGNORED_SENDER
    assert engine.handle_incoming_sms(sms_from_contact(clock, message_id="m2", recipient="+15145558888")) \
        is ReplyRouting.IGNORED_SENDER
    assert ports.interpretation_requests == [] and "+1514" not in ports.log_text


def test_reponse_avant_l_alerte_n_est_pas_rattachee(engine, clock, ports):
    submitted_incident(engine, clock)
    early = sms_from_contact(clock, received_at=clock.wall() - timedelta(minutes=5))
    assert engine.handle_incoming_sms(early) is ReplyRouting.NEEDS_REVIEW
    assert engine.view().reply.needs_review and ports.interpretation_requests == []


def test_reponse_sans_incident_ouvert_est_a_verifier_sans_incident_modifie(engine, clock, ports):
    assert engine.handle_incoming_sms(sms_from_contact(clock)) is ReplyRouting.NEEDS_REVIEW
    assert engine.view() is None and ports.interpretation_requests == []


def test_reponse_tardive_apres_cloture_n_est_pas_rattachee(engine, clock):
    submitted_incident(engine, clock)
    engine.close()
    clock.advance(900.0)
    assert engine.handle_incoming_sms(sms_from_contact(clock)) is ReplyRouting.NEEDS_REVIEW


def test_reponse_possiblement_destinee_a_l_alerte_precedente_est_a_verifier(engine, clock):
    submitted_incident(engine, clock)  # première alerte sans réponse
    engine.close()
    engine.handle_event(recovered_event())
    clock.advance(120.0)
    open_incident(engine, clock)
    reach_deadline(engine, clock)
    assert engine.handle_incoming_sms(sms_from_contact(clock, message_id="late")) is ReplyRouting.NEEDS_REVIEW


def test_reponse_plus_ancienne_recue_hors_ordre_ne_remplace_pas_la_plus_recente(engine, clock):
    incident_id = submitted_incident(engine, clock)
    clock.advance(60.0)
    newer = sms_from_contact(clock, "Finalement je ne peux pas venir", message_id="new")
    older = sms_from_contact(clock, "Je suis en route", message_id="old", received_at=newer.received_at - timedelta(seconds=30))
    engine.handle_incoming_sms(newer)
    engine.on_reply_interpreted(incident_id, "new", ReplyInterpretation(ReplyState.UNAVAILABLE))
    assert engine.handle_incoming_sms(older) is ReplyRouting.IGNORED_OLDER
    assert engine.view().reply.state is ReplyState.UNAVAILABLE


def test_correction_explicite_remplace_la_reponse_precedente(engine, clock):
    incident_id = submitted_incident(engine, clock)
    engine.handle_incoming_sms(sms_from_contact(clock, message_id="a"))
    engine.on_reply_interpreted(incident_id, "a", ReplyInterpretation(ReplyState.EN_ROUTE, 20))
    clock.advance(30.0)
    engine.handle_incoming_sms(sms_from_contact(clock, "Finalement je ne peux pas venir", message_id="b"))
    engine.on_reply_interpreted(incident_id, "b", ReplyInterpretation(ReplyState.UNAVAILABLE))
    assert engine.view().reply.state is ReplyState.UNAVAILABLE and engine.view().reply.delay_minutes is None


def test_le_texte_brut_de_la_reponse_n_est_jamais_journalise(engine, clock, ports):
    incident_id = submitted_incident(engine, clock)
    engine.handle_incoming_sms(sms_from_contact(clock, "secret-texte-prive"))
    engine.on_reply_interpreted(incident_id, "m1", ReplyInterpretation(ReplyState.EN_ROUTE, 5))
    everything = ports.log_text + str(ports.records)
    assert "secret-texte-prive" not in everything
