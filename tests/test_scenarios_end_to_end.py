"""Parcours complets : détection, voix, décompte, SMS simulé et réponse, rejoués avec une horloge contrôlable."""

from __future__ import annotations

from faaall_alarm.incident import IncidentState
from faaall_alarm.model import FallCause, SourceKind
from faaall_alarm.simulation.runner import DEMO_REPLY_TEXT, RecordingVoice, ScenarioRunner, demo_config
from faaall_alarm.simulation.scenarios import get_scenario
from faaall_alarm.sms_contract import ReplyState, SmsStatus
from faaall_alarm.sms_simulated import SimulatedReplyInterpreter, SimulatedSmsGateway


def states(runner):
    return [incident.state for incident in runner.core.engine.incidents()]


def test_chute_debout_voix_une_fois_decompte_sms_simule_et_reponse_du_proche():
    runner = ScenarioRunner(get_scenario("chute_debout"))
    runner.at(24.0, lambda run: run.inject_reply(DEMO_REPLY_TEXT))
    result = runner.run(until_s=30.0)
    view = result.core.engine.view()
    assert len(result.voice.spoken) == 1 and len(result.gateway.submitted_requests) == 1
    assert view.state is IncidentState.HANDLED and view.sms_status is SmsStatus.SIMULATED
    assert (view.reply.state, view.reply.delay_minutes) == (ReplyState.EN_ROUTE, 20)
    assert "Réponse du proche : En route, délai annoncé : environ 20 min" in result.log_text
    assert DEMO_REPLY_TEXT not in result.log_text


def test_decompte_de_dix_secondes_par_defaut_entre_confirmation_et_soumission():
    runner = ScenarioRunner(get_scenario("chute_debout"))
    result = runner.run(until_s=30.0)
    incident = result.core.engine.incidents()[0]
    assert incident.countdown_seconds == 10.0
    submission_time = incident.sms_requested_wall - incident.created_wall
    assert 9.9 <= submission_time.total_seconds() <= 10.3


def test_annulation_avant_l_echeance_aucun_sms():
    runner = ScenarioRunner(get_scenario("chute_debout"))
    runner.at(12.0, lambda run: run.core.engine.cancel())
    result = runner.run(until_s=30.0)
    assert result.gateway.submitted_requests == [] and states(runner) == [IncidentState.CANCELLED]
    assert "Alerte annulée" in result.log_text


def test_annulation_juste_apres_la_decision_d_envoyer_est_sans_effet():
    runner = ScenarioRunner(get_scenario("chute_debout"))
    runner.at(16.6, lambda run: run.core.engine.cancel())  # la soumission a eu lieu à ~16,5 s
    result = runner.run(until_s=22.0)
    assert len(result.gateway.submitted_requests) == 1 and states(runner) == [IncidentState.HANDLED]


def test_reprise_de_posture_avant_l_echeance_annule_le_sms():
    result = ScenarioRunner(get_scenario("chute_puis_reprise")).run()
    assert result.gateway.submitted_requests == []
    assert "reprise de posture fiable" in result.log_text


def test_affaissement_lent_et_chute_assise_declenchent_un_incident():
    for name in ("affaissement_lent", "chute_assise"):
        result = ScenarioRunner(get_scenario(name)).run(until_s=25.0)
        assert len(result.incident_ids) == 1 and len(result.gateway.submitted_requests) == 1


def test_gestes_ordinaires_ne_creent_aucun_incident_ni_voix():
    for name in ("gestes_ordinaires", "coucher_normal", "allongee_au_depart", "croisement"):
        result = ScenarioRunner(get_scenario(name)).run()
        assert result.incident_ids == [] and result.voice.spoken == [], name


def test_collision_sans_chute_est_journalisee_sans_sms_de_chute():
    result = ScenarioRunner(get_scenario("collision_sans_chute")).run()
    assert result.incident_ids == [] and result.gateway.submitted_requests == []
    assert "collision probable sans chute détectée" in result.log_text
    history = [r["evenement"] for r in result.core.history.read()]
    assert "collision_sans_chute" in history


def test_collision_avec_chute_un_seul_incident_meme_si_les_deux_tombent():
    result = ScenarioRunner(get_scenario("collision_avec_chute_2")).run(until_s=25.0)
    assert len(result.incident_ids) == 1 and len(result.voice.spoken) == 1
    assert len(result.gateway.submitted_requests) == 1
    request = result.gateway.submitted_requests[0]
    assert request.cause is FallCause.COLLISION and request.first_name is None


def test_collision_une_personne_tombe_prenom_omis_et_cause_collision():
    result = ScenarioRunner(get_scenario("collision_avec_chute")).run(until_s=25.0)
    assert result.gateway.submitted_requests[0].cause is FallCause.COLLISION


def test_chute_avec_temoin_individuelle_mais_prenom_omis_car_attribution_incertaine():
    result = ScenarioRunner(get_scenario("chute_avec_temoin")).run(until_s=25.0)
    request = result.gateway.submitted_requests[0]
    assert request.cause is FallCause.INDIVIDUAL and request.first_name is None


def test_chute_seule_garde_le_prenom_configure():
    result = ScenarioRunner(get_scenario("chute_debout")).run(until_s=25.0)
    assert result.gateway.submitted_requests[0].first_name == "Marie"


def test_contact_incertain_alerte_avec_cause_indeterminee():
    result = ScenarioRunner(get_scenario("chute_contact_incertain")).run(until_s=25.0)
    assert result.gateway.submitted_requests[0].cause is FallCause.UNDETERMINED
    assert "Cause indéterminée" in result.log_text


def test_perte_de_suivi_apres_confirmation_conserve_incident_et_voix_unique():
    result = ScenarioRunner(get_scenario("chute_perte_de_suivi")).run(until_s=25.0)
    assert len(result.incident_ids) == 1 and len(result.voice.spoken) == 1
    assert len(result.gateway.submitted_requests) == 1


def test_perte_camera_pendant_l_alerte_le_decompte_continue():
    result = ScenarioRunner(get_scenario("chute_perte_camera")).run(until_s=25.0)
    assert len(result.gateway.submitted_requests) == 1
    assert "Caméra inactive : l'incident" in result.log_text


def test_perte_camera_sans_chute_signale_la_panne_sans_sms():
    result = ScenarioRunner(get_scenario("perte_camera_sans_chute")).run()
    assert result.gateway.submitted_requests == [] and "Surveillance indisponible" in result.log_text
    assert "Caméra inactive" not in result.core.status_line()  # la caméra est revenue


def test_statut_pendant_la_panne_camera():
    runner = ScenarioRunner(get_scenario("perte_camera_sans_chute"))
    seen = []
    runner.at(5.0, lambda run: seen.append(run.core.status_line()))
    runner.run()
    assert seen[0] == "Caméra inactive · SMS simulé"


def test_rearmement_apres_cloture_une_nouvelle_chute_cree_un_second_incident():
    runner = ScenarioRunner(get_scenario("deux_chutes"))
    runner.at(8.0, lambda run: run.core.engine.cancel())  # annule la première, avant l'échéance
    result = runner.run(until_s=40.0)
    assert len(result.incident_ids) == 2 and len(result.voice.spoken) == 2


def test_personne_toujours_au_sol_apres_cloture_pas_de_second_sms():
    runner = ScenarioRunner(get_scenario("chute_debout"))
    runner.at(20.0, lambda run: run.core.engine.close())
    result = runner.run(until_s=60.0)
    assert len(result.incident_ids) == 1 and len(result.gateway.submitted_requests) == 1


def test_voix_indisponible_poursuit_le_decompte_et_le_sms():
    runner = ScenarioRunner(get_scenario("chute_debout"), voice=RecordingVoice(available=False))
    result = runner.run(until_s=25.0)
    assert len(result.gateway.submitted_requests) == 1
    assert "Voix indisponible" in result.log_text
    assert "Voix indisponible" in result.core.status_line()


def test_sms_refuse_conserve_l_alerte_locale_et_ne_renvoie_pas():
    gateway = SimulatedSmsGateway(forced_result=SmsStatus.FAILED)
    result = ScenarioRunner(get_scenario("chute_debout"), gateway=gateway).run(until_s=40.0)
    assert result.core.engine.view().sms_status is SmsStatus.FAILED
    assert len(gateway.submitted_requests) == 1


def test_resultat_reseau_incertain_aucun_renvoi_automatique():
    gateway = SimulatedSmsGateway(forced_result=SmsStatus.UNKNOWN)
    result = ScenarioRunner(get_scenario("chute_debout"), gateway=gateway).run(until_s=60.0)
    assert result.core.engine.view().sms_status is SmsStatus.UNKNOWN and len(gateway.submitted_requests) == 1


def test_interpreteur_indisponible_affiche_reponse_a_verifier():
    runner = ScenarioRunner(get_scenario("chute_debout"), interpreter=SimulatedReplyInterpreter(available=False))
    runner.at(24.0, lambda run: run.inject_reply(DEMO_REPLY_TEXT))
    result = runner.run(until_s=30.0)
    assert result.core.engine.view().reply.needs_review
    assert "Réponse reçue, à vérifier" in result.log_text


def test_reponse_d_un_numero_etranger_est_ignoree():
    runner = ScenarioRunner(get_scenario("chute_debout"))
    runner.at(24.0, lambda run: run.inject_reply(DEMO_REPLY_TEXT, sender="+15145550001"))
    result = runner.run(until_s=30.0)
    assert result.core.engine.view().reply.state is None
    assert "expéditeur ou d'un numéro inattendu" in result.log_text


def test_mode_mp4_et_simulation_n_utilisent_jamais_un_fournisseur_reel(monkeypatch):
    """Même avec un module réel présent et une configuration live valide, MP4 et tests restent simulés."""
    from faaall_alarm import integration

    class RealGatewayDoubleurDeTest:
        calls = 0

        def check_availability(self):
            raise AssertionError("ne doit pas être appelé")

        def submit_alert(self, request):
            type(self).calls += 1
            raise AssertionError("SMS réel interdit")

    monkeypatch.setattr(
        integration, "_load",
        lambda entry: (lambda config: RealGatewayDoubleurDeTest()) if entry == integration.REAL_GATEWAY_FACTORY else None,
    )
    live = demo_config(sms_mode="live", sms_live_confirmed=True)
    for kind in (SourceKind.VIDEO_FILE, SourceKind.SIMULATION):
        gateway = integration.build_sms_gateway(live, kind, ScenarioRunner(get_scenario("chute_debout")).clock)
        assert isinstance(gateway, SimulatedSmsGateway), kind
    runner = ScenarioRunner(get_scenario("chute_debout"), config=live, source_kind=SourceKind.VIDEO_FILE,
                            gateway=integration.build_sms_gateway(live, SourceKind.VIDEO_FILE, ScenarioRunner(get_scenario("chute_debout")).clock))
    runner.run(until_s=25.0)
    assert RealGatewayDoubleurDeTest.calls == 0


def test_la_garde_bloque_un_adaptateur_reel_hors_webcam(monkeypatch):
    from faaall_alarm.integration import SendGuard

    class RealGateway:
        submitted = 0

        def check_availability(self):
            return None

        def submit_alert(self, request):
            type(self).submitted += 1

    runner = ScenarioRunner(get_scenario("chute_debout"))
    guard = SendGuard(RealGateway(), lambda: False, runner.clock)
    request = runner.gateway.submitted_requests  # liste vide : on fabrique une demande minimale
    from faaall_alarm.sms_contract import AlertRequest

    outcome = guard.submit_alert(AlertRequest("X", runner.clock.wall(), FallCause.INDIVIDUAL, None, "", "", ""))
    assert outcome.status is SmsStatus.FAILED and RealGateway.submitted == 0 and request == []


def test_reel_exige_mode_live_activation_explicite_et_webcam():
    from faaall_alarm.integration import real_sms_allowed

    assert not real_sms_allowed(demo_config(), SourceKind.WEBCAM)
    assert not real_sms_allowed(demo_config(sms_mode="live"), SourceKind.WEBCAM)
    assert not real_sms_allowed(demo_config(sms_mode="live", sms_live_confirmed=True), SourceKind.VIDEO_FILE)
    assert real_sms_allowed(demo_config(sms_mode="live", sms_live_confirmed=True), SourceKind.WEBCAM)


def test_journal_complet_conserve_toute_la_session_dans_l_ordre():
    result = ScenarioRunner(get_scenario("deux_chutes")).run(until_s=40.0)
    lines = result.log_text.splitlines()
    assert lines == sorted(lines, key=lambda line: line[:8])  # horodatage HH:mm:ss croissant
    assert all(line[2] == ":" and line[5] == ":" for line in lines)
