"""Détection sur séquences simulées indépendantes : chutes, gestes ordinaires, témoins et collisions."""

from __future__ import annotations

import numpy as np
import pytest

from faaall_alarm.config import DetectionThresholds
from faaall_alarm.detection import DetectionPipeline
from faaall_alarm.model import EventKind, FallCause, FrameObservation, Posture
from faaall_alarm.simulation.scenarios import SCENARIOS, get_scenario

from .conftest import analyse, compress, kinds, posture_timeline

FALL_SCENARIOS = ("chute_debout", "chute_assise", "affaissement_lent", "chute_pres_du_lit")


@pytest.mark.parametrize("name", FALL_SCENARIOS)
def test_chutes_individuelles_confirmees_une_seule_fois(name):
    confirmed = kinds(analyse(get_scenario(name)), EventKind.FALL_CONFIRMED)
    assert len(confirmed) == 1
    assert confirmed[0].cause is FallCause.INDIVIDUAL


def test_affaissement_lent_sans_pic_de_vitesse_est_detecte():
    # Descente répartie sur 4,5 s : aucune image ne dépasse le seuil de chute rapide.
    events = analyse(get_scenario("affaissement_lent"))
    assert len(kinds(events, EventKind.FALL_CONFIRMED)) == 1


@pytest.mark.parametrize("name", ("gestes_ordinaires", "coucher_normal", "allongee_au_depart"))
def test_gestes_ordinaires_sans_incident(name):
    events = analyse(get_scenario(name))
    assert not kinds(events, EventKind.FALL_SUSPECTED)
    assert not kinds(events, EventKind.FALL_CONFIRMED)


def test_coucher_dans_la_zone_du_lit_est_signale_sans_alerte():
    events = analyse(get_scenario("coucher_normal"))
    assert len(kinds(events, EventKind.RESTING_ZONE)) == 1


def test_chute_a_cote_du_lit_reste_detectable():
    scenario = get_scenario("chute_pres_du_lit")
    assert scenario.lying_zones  # le lit est bien défini dans la scène
    assert len(kinds(analyse(scenario), EventKind.FALL_CONFIRMED)) == 1


def test_chute_avec_temoin_a_distance_reste_individuelle():
    confirmed = kinds(analyse(get_scenario("chute_avec_temoin")), EventKind.FALL_CONFIRMED)
    assert [event.cause for event in confirmed] == [FallCause.INDIVIDUAL]


def test_croisement_ordinaire_ne_compte_pas_comme_collision():
    events = analyse(get_scenario("croisement"))
    assert not kinds(events, EventKind.COLLISION_DETECTED)
    assert not kinds(events, EventKind.FALL_CONFIRMED)
    assert kinds(events, EventKind.CROSSING_IGNORED)


def test_collision_sans_chute_est_journalisee_sans_chute():
    events = analyse(get_scenario("collision_sans_chute"))
    assert len(kinds(events, EventKind.COLLISION_DETECTED)) == 1
    assert len(kinds(events, EventKind.COLLISION_WITHOUT_FALL)) == 1
    assert not kinds(events, EventKind.FALL_CONFIRMED)


def test_collision_avec_chute_est_classee_collision_et_ne_devient_pas_sans_chute():
    events = analyse(get_scenario("collision_avec_chute"))
    confirmed = kinds(events, EventKind.FALL_CONFIRMED)
    assert [event.cause for event in confirmed] == [FallCause.COLLISION]
    assert not kinds(events, EventKind.COLLISION_WITHOUT_FALL)


def test_deux_personnes_tombent_apres_collision_deux_confirmations_meme_cause():
    confirmed = kinds(analyse(get_scenario("collision_avec_chute_2")), EventKind.FALL_CONFIRMED)
    assert len(confirmed) == 2
    assert {event.cause for event in confirmed} == {FallCause.COLLISION}
    assert {event.track_ids for event in confirmed} == {(1,), (2,)}


def test_contact_incertain_conserve_la_chute_avec_cause_indeterminee():
    confirmed = kinds(analyse(get_scenario("chute_contact_incertain")), EventKind.FALL_CONFIRMED)
    assert [event.cause for event in confirmed] == [FallCause.UNDETERMINED]


def test_pose_masquee_apres_une_chute_rapide_au_sol_est_confirmee_sans_reprise():
    # MediaPipe perd souvent la personne allongée : après un basculement rapide observé jusqu'au
    # sol, la pose masquée confirme la chute au lieu d'effacer la suspicion.
    scenario = get_scenario("chute_debout")  # au sol à 5,2 s
    scenario.persons[0].hide(5.4, 9.0)
    events = analyse(scenario)
    confirmed = kinds(events, EventKind.FALL_CONFIRMED)
    assert len(confirmed) == 1 and "pose masquée" in confirmed[0].detail
    assert 5.4 + DetectionThresholds().lost_after_fast_fall_confirm_s <= confirmed[0].timestamp_s < 9.0
    assert not kinds(events, EventKind.PERSON_RECOVERED)


def test_pose_perdue_apres_une_descente_lente_n_est_pas_confirmee_a_l_aveugle():
    scenario = get_scenario("affaissement_lent")
    scenario.persons[0].hide(6.0, 14.0)  # disparaît avant le maintien au sol
    events = analyse(scenario)
    assert kinds(events, EventKind.FALL_SUSPECTED)
    assert not [e for e in kinds(events, EventKind.FALL_CONFIRMED) if e.timestamp_s < 14.0]
    assert kinds(events, EventKind.SUSPICION_CLEARED)


def test_reprise_de_posture_apres_chute_est_signalee_une_fois():
    events = analyse(get_scenario("chute_puis_reprise"))
    assert len(kinds(events, EventKind.PERSON_RECOVERED)) == 1


def test_apres_reprise_une_nouvelle_chute_est_une_nouvelle_confirmation():
    confirmed = kinds(analyse(get_scenario("deux_chutes")), EventKind.FALL_CONFIRMED)
    assert len(confirmed) == 2


def test_personne_au_sol_ne_redeclenche_pas_a_chaque_image():
    # 30 s allongée après la chute : une seule confirmation malgré des centaines d'images.
    assert len(kinds(analyse(get_scenario("chute_debout")), EventKind.FALL_CONFIRMED)) == 1


def test_chute_detectee_malgre_des_images_irregulieres_et_basse_cadence():
    scenario = get_scenario("chute_debout")
    rng = np.random.default_rng(7)
    pipeline = DetectionPipeline()
    t, events = 0.0, []
    while t < 14.0:
        events += pipeline.process(scenario.observation_at(t)).events
        t += float(rng.uniform(0.1, 0.4))  # 2,5 à 10 images par seconde, irrégulières
    assert len(kinds(events, EventKind.FALL_CONFIRMED)) == 1


def test_bas_du_corps_hors_cadre_la_chute_reste_detectee():
    scenario = get_scenario("chute_debout")
    pipeline = DetectionPipeline()
    events = []
    for t in scenario.frame_times():
        observation = scenario.observation_at(float(t))
        for pose in observation.poses:
            pose.landmarks[[25, 26, 27, 28], 2] = 0.05  # genoux et chevilles masqués
        events += pipeline.process(observation).events
    assert len(kinds(events, EventKind.FALL_CONFIRMED)) == 1


def test_posture_affichee_suit_la_scene_dans_l_ordre_et_au_bon_moment():
    # L'ordre et le moment comptent : STANDING avant la chute, FALLING pendant et juste après la
    # descente (0,5 à 0,7 s), LYING ensuite, sans retour à une posture debout.
    scenario = get_scenario("chute_debout")  # descente de 4,5 à 5,2 s
    timeline = posture_timeline(scenario)
    sequence = compress([p for _, p in timeline])
    assert sequence[0] is Posture.STANDING and Posture.WALKING in sequence
    assert sequence[-2:] == [Posture.FALLING, Posture.LYING]  # aucune posture debout après la chute
    first_falling = next(t for t, p in timeline if p is Posture.FALLING)
    first_lying = next(t for t, p in timeline if p is Posture.LYING)
    assert 4.5 <= first_falling <= 5.2
    assert first_falling < first_lying <= 7.5


def test_aucune_pose_ne_cree_ni_personne_ni_evenement():
    pipeline = DetectionPipeline()
    analysis = pipeline.process(FrameObservation(0.0, (640, 480), ()))
    assert analysis.persons == [] and analysis.events == []


def _process_until(pipeline, scenario, until_s):
    analysis = None
    for t in scenario.frame_times():
        if t > until_s:
            break
        analysis = pipeline.process(scenario.observation_at(float(t)))
    return analysis


def test_libelles_des_personnes():
    one = _process_until(DetectionPipeline(), get_scenario("chute_debout"), 1.0)
    assert [view.label for view in one.persons] == ["Personne"]
    two = _process_until(DetectionPipeline(), get_scenario("collision_avec_chute"), 1.0)
    assert sorted(view.label for view in two.persons) == ["Personne 1", "Personne 2"]


def test_une_nouvelle_personne_n_est_affichee_qu_apres_plusieurs_images():
    scenario = get_scenario("chute_debout")
    pipeline = DetectionPipeline()
    shown = [bool(pipeline.process(scenario.observation_at(k / 15)).persons) for k in range(4)]
    assert shown == [False, False, True, True]  # confirmée à la 3e observation


def test_au_plus_deux_personnes_suivies():
    scenario = get_scenario("croisement")
    observation = scenario.observation_at(1.0)
    extra = observation.poses[0]
    pipeline = DetectionPipeline()
    analysis = pipeline.process(FrameObservation(1.0, observation.frame_size_px, observation.poses + (extra, extra)))
    assert len(analysis.persons) <= DetectionThresholds().max_people


def test_zone_surveillee_ignore_les_personnes_hors_zone():
    scenario = get_scenario("chute_debout")
    pipeline = DetectionPipeline(monitored_zone=(0.9, 0.0, 1.0, 1.0))
    analysis = pipeline.process(scenario.observation_at(1.0))
    assert analysis.persons == []


def test_tous_les_scenarios_se_rejouent_sans_erreur():
    for name in SCENARIOS:
        analyse(get_scenario(name))
