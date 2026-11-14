"""Régressions des défauts de vision reproduits (passe 1 et réaudit) sur poses simulées.

Chaque test vérifie un défaut observé sur le corpus UR Fall ou reproduit en simulation : posture
FALLING sur un geste ordinaire, marche manquée ou devinée, pistes parasites, reprise prématurée,
chutes vers la caméra ou masquées. Les postures sont vérifiées dans l'ordre et au bon moment.
"""

from __future__ import annotations

import numpy as np
import pytest

from faaall_alarm.config import DetectionThresholds
from faaall_alarm.detection import CollisionDetector, DetectionPipeline
from faaall_alarm.features import compute_features
from faaall_alarm.model import EventKind, FallCause, FrameObservation, PersonPose, Posture
from faaall_alarm.simulation.runner import ScenarioRunner
from faaall_alarm.simulation.scenarios import get_scenario
from faaall_alarm.tracking import PersonTrack

from .conftest import analyse, compress, kinds, posture_timeline

UPRIGHT = {Posture.STANDING, Posture.WALKING, Posture.SITTING}


# -- gestes ordinaires ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", ("assise_rapide", "accroupissement_rapide", "ramasser_objet", "gestes_ordinaires"))
def test_geste_ordinaire_rapide_n_affiche_jamais_falling_ni_suspicion(name):
    scenario = get_scenario(name)
    assert Posture.FALLING not in {p for _, p in posture_timeline(scenario)}
    events = analyse(scenario)
    assert not kinds(events, EventKind.FALL_SUSPECTED) and not kinds(events, EventKind.FALL_CONFIRMED)


def test_assise_rapide_affiche_sitting_apres_la_descente_puis_standing():
    timeline = posture_timeline(get_scenario("assise_rapide"))  # assise de 2,0 à 2,45 s, relevé à 5,45 s
    during = {p for t, p in timeline if 3.0 <= t <= 5.3}
    assert during == {Posture.SITTING}
    assert timeline[-1][1] is Posture.STANDING


def test_vitesse_mesuree_sur_les_memes_echantillons_que_la_duree():
    # Défaut d'origine : la classification lisait la vitesse avant d'ajouter l'image courante,
    # le déplacement s'arrêtait à l'image précédente mais la durée allait jusqu'à l'image courante.
    thresholds = DetectionThresholds()
    track = PersonTrack(1, 0.0, thresholds)
    scenario = get_scenario("chute_debout")
    for t in (4.6, 4.7, 4.8):
        pose = scenario.poses_at(t)[0]
        track.record(t, compute_features(pose, scenario.frame_size_px, thresholds), (0.0, 0.0))
    track.reference_torso_px = 90.0
    _, vy = track.hip_velocity_torso(0.5, 4.8)
    first = compute_features(scenario.poses_at(4.6)[0], scenario.frame_size_px, thresholds)
    last = compute_features(scenario.poses_at(4.8)[0], scenario.frame_size_px, thresholds)
    expected = (last.hip_center_px[1] - first.hip_center_px[1]) / 90.0 / 0.2
    assert vy == pytest.approx(expected, rel=1e-6)


def test_balancement_sur_place_reste_standing():
    timeline = posture_timeline(get_scenario("balancement"))
    assert {p for t, p in timeline if t >= 1.0} == {Posture.STANDING}


@pytest.mark.parametrize("name", ("marche_vers_camera", "marche_eloignement"))
def test_marche_dans_l_axe_de_la_camera_est_reconnue_sans_incident(name):
    scenario = get_scenario(name)  # marche de 2,0 à 6,0 s, départ progressif (amplitude et vitesse)
    timeline = posture_timeline(scenario)
    walking = [t for t, p in timeline if p is Posture.WALKING]
    assert walking and min(walking) <= 4.5 and max(walking) - min(walking) >= 1.5
    assert not ({p for _, p in timeline} & {Posture.FALLING, Posture.LYING})
    assert not kinds(analyse(scenario), EventKind.FALL_SUSPECTED)


def test_jambes_hors_cadre_donnent_posture_indeterminee_plutot_qu_une_posture_devinee():
    timeline = posture_timeline(get_scenario("marche_vers_camera"))
    assert timeline[-1][1] is Posture.INDETERMINATE  # proche de la caméra, jambes sorties du cadre
    hidden = posture_timeline(get_scenario("jambes_hors_cadre"))
    still = [p for t, p in hidden if t <= 2.8]
    assert set(still) == {Posture.INDETERMINATE}
    assert Posture.STANDING not in {p for _, p in hidden}


def test_assise_face_a_la_camera_est_sitting():
    timeline = posture_timeline(get_scenario("assise_de_face"))  # assise de 3,2 à 7,2 s
    assert {p for t, p in timeline if 3.8 <= t <= 7.0} == {Posture.SITTING}


def test_postures_affichees_ne_clignotent_pas():
    timeline = posture_timeline(get_scenario("gestes_ordinaires"))
    # Cinq gestes enchaînés : au plus une dizaine de changements affichés, pas un par image.
    assert len(compress([p for _, p in timeline])) <= 12


# -- chutes difficiles ----------------------------------------------------------------------------
@pytest.mark.parametrize("name", (
    "chute_vers_camera", "affaissement_lent_occulte", "chute_pose_perdue_descente", "chute_depuis_canape",
    "chute_hanches_zone_lit",
))
def test_chute_difficile_confirmee_une_seule_fois(name):
    events = analyse(get_scenario(name))
    confirmed = kinds(events, EventKind.FALL_CONFIRMED)
    assert len(confirmed) == 1 and confirmed[0].cause is FallCause.INDIVIDUAL
    assert not kinds(events, EventKind.RESTING_ZONE)


def test_delai_entre_contact_au_sol_et_confirmation_reste_court():
    for name in ("chute_debout", "chute_vers_camera", "chute_depuis_canape"):
        scenario = get_scenario(name)
        confirmed = kinds(analyse(scenario), EventKind.FALL_CONFIRMED)[0]
        assert 0.0 < confirmed.timestamp_s - scenario.fall_contact_s <= 1.5, name


def test_reference_de_taille_non_faussee_par_un_torse_qui_bascule_vers_la_camera():
    scenario = get_scenario("chute_vers_camera")
    pipeline = DetectionPipeline()
    for t in scenario.frame_times():
        pipeline.process(scenario.observation_at(float(t)))
    track = pipeline.tracker.tracks[0]
    assert track.reference_torso_px >= 0.9 * 90.0  # torse debout de 90 px, raccourci à 35 % au sol


@pytest.mark.parametrize("name", ("reprise_partielle_genoux", "reprise_assise_au_sol"))
def test_redressement_au_sol_n_est_pas_une_reprise(name):
    events = analyse(get_scenario(name))
    assert len(kinds(events, EventKind.FALL_CONFIRMED)) == 1
    assert not kinds(events, EventKind.PERSON_RECOVERED)
    result = ScenarioRunner(get_scenario(name)).run(until_s=20.0)
    assert len(result.gateway.submitted_requests) == 1  # l'alerte n'est pas annulée


def test_reprise_debout_reste_detectee_apres_la_chute():
    events = analyse(get_scenario("chute_puis_reprise"))
    recovered = kinds(events, EventKind.PERSON_RECOVERED)
    assert len(recovered) == 1
    assert recovered[0].timestamp_s > kinds(events, EventKind.FALL_CONFIRMED)[0].timestamp_s


def test_coucher_dans_le_lit_reste_sans_alerte_malgre_une_descente_assise_rapide():
    scenario = get_scenario("coucher_normal")
    events = analyse(scenario)
    assert kinds(events, EventKind.RESTING_ZONE) and not kinds(events, EventKind.FALL_CONFIRMED)


def test_chute_et_postures_avec_cadence_irreguliere():
    scenario = get_scenario("chute_debout")
    rng = np.random.default_rng(11)
    pipeline = DetectionPipeline()
    t, events, postures = 0.0, [], []
    while t < 14.0:
        analysis = pipeline.process(scenario.observation_at(t))
        events += analysis.events
        postures += [p.posture for p in analysis.persons]
        t += float(rng.choice([1 / 30, 1 / 15, 0.2, 0.35]))
    assert len(kinds(events, EventKind.FALL_CONFIRMED)) == 1
    sequence = compress(postures)
    assert sequence[-1] is Posture.LYING and Posture.FALLING in sequence
    assert not set(sequence[sequence.index(Posture.FALLING):]) & UPRIGHT


# -- suivi -----------------------------------------------------------------------------------------
def _pose_shifted(pose: PersonPose, dx: float = 0.0, scale: float = 1.0) -> PersonPose:
    landmarks = pose.landmarks.copy()
    center = landmarks[:, :2].mean(axis=0)
    landmarks[:, :2] = center + (landmarks[:, :2] - center) * scale + np.array([dx, 0.0])
    return PersonPose(landmarks)


@pytest.mark.parametrize("copy", ("identique", "emboitee"))
def test_doublon_de_mediapipe_ne_cree_pas_de_seconde_personne(copy):
    scenario = get_scenario("chute_debout")
    pipeline = DetectionPipeline()
    labels = set()
    for t in scenario.frame_times()[:60]:
        observation = scenario.observation_at(float(t))
        pose = observation.poses[0]
        duplicate = _pose_shifted(pose, dx=0.004) if copy == "identique" else _pose_shifted(pose, scale=0.4)
        analysis = pipeline.process(FrameObservation(observation.timestamp_s, observation.frame_size_px,
                                                     (pose, duplicate)))
        labels |= {person.label for person in analysis.persons}
        assert len(analysis.persons) <= 1
    assert labels == {"Personne"}


def test_pose_parasite_isolee_n_affiche_pas_de_personne_2():
    scenario = get_scenario("chute_debout")
    ghost = _pose_shifted(scenario.poses_at(1.0)[0], dx=0.4)
    pipeline = DetectionPipeline()
    for k, t in enumerate(scenario.frame_times()[:45]):
        observation = scenario.observation_at(float(t))
        poses = observation.poses + ((ghost,) if k == 30 else ())
        analysis = pipeline.process(FrameObservation(observation.timestamp_s, observation.frame_size_px, poses))
        if k < DetectionThresholds().track_confirm_hits - 1:
            continue  # trace réelle encore provisoire
        assert [p.label for p in analysis.persons] == ["Personne"]
        assert not [e for e in analysis.events if e.kind is EventKind.TRACK_LOST]


def test_pose_absente_une_image_garde_la_posture_affichee():
    scenario = get_scenario("chute_debout")
    pipeline = DetectionPipeline()
    for t in scenario.frame_times()[:30]:
        pipeline.process(scenario.observation_at(float(t)))
    gap = pipeline.process(FrameObservation(2.0, scenario.frame_size_px, ()))
    assert [p.posture for p in gap.persons] == [Posture.STANDING]
    assert gap.persons[0].landmarks is None  # aucun squelette redessiné sans points courants


@pytest.mark.parametrize("name", ("croisement", "croisement_profondeurs", "croisement_occulte"))
def test_croisement_conserve_les_identites_et_ne_cree_pas_de_collision(name):
    scenario = get_scenario(name)
    pipeline = DetectionPipeline()
    positions: dict[int, list[float]] = {}
    events = []
    for t in scenario.frame_times():
        analysis = pipeline.process(scenario.observation_at(float(t)))
        events += analysis.events
        for person in analysis.persons:
            if person.landmarks is not None:
                positions.setdefault(person.track_id, []).append(float(person.landmarks[[23, 24], 0].mean()))
    assert set(positions) == {1, 2}
    # La personne partie de la gauche finit à droite, et inversement : aucune identité échangée.
    trends = sorted(np.sign(xs[-1] - xs[0]) for xs in positions.values())
    assert trends == [-1.0, 1.0]
    assert all(abs(xs[-1] - xs[0]) > 0.5 for xs in positions.values())
    assert not kinds(events, EventKind.COLLISION_DETECTED)


def test_libelles_stables_quand_l_autre_trace_est_remplacee():
    pipeline = DetectionPipeline()
    scenario = get_scenario("croisement")
    for t in scenario.frame_times()[:20]:
        pipeline.process(scenario.observation_at(float(t)))
    slots = {track.track_id: track.slot for track in pipeline.tracker.tracks}
    assert sorted(slots.values()) == [1, 2]
    # La première personne disparaît : la seconde garde son numéro.
    second = max(slots)
    for k in range(80):
        t = 20 / 15 + k / 15
        observation = scenario.observation_at(t)
        analysis = pipeline.process(FrameObservation(t, observation.frame_size_px, observation.poses[1:]))
    assert pipeline.label_for(second) in ("Personne", f"Personne {slots[second]}")
    assert all(track.slot == slots[track.track_id] for track in pipeline.tracker.tracks if track.track_id in slots)


def test_remplacement_de_trace_pendant_un_contact_candidat_ne_leve_pas_d_erreur():
    # Défaut d'origine : KeyError dans CollisionDetector quand un identifiant change pendant la
    # fenêtre d'observation d'un contact ; l'application perdait alors toutes les images à deux.
    thresholds = DetectionThresholds()
    detector = CollisionDetector(thresholds)
    scenario = get_scenario("collision_sans_chute")
    tracks = {i: PersonTrack(i, 0.0, thresholds, slot=i) for i in (1, 2, 3)}
    for t in scenario.frame_times():
        if t > 4.5:
            break
        poses = scenario.poses_at(float(t))
        ids = (1, 2) if t < 3.62 else (1, 3)  # la seconde trace est remplacée juste après le contact
        present = []
        for track_id, pose in zip(ids, poses):
            features = compute_features(pose, scenario.frame_size_px, thresholds)
            tracks[track_id].reference_torso_px = 90.0
            tracks[track_id].record(float(t), features, features.torso_center_px)
            present.append(tracks[track_id])
        detector.update(float(t), present)  # ne doit lever aucune exception


# -- réaudit : difficultés combinées ---------------------------------------------------------------
def test_se_baisser_derriere_un_meuble_puis_disparaitre_n_est_pas_une_chute():
    events = analyse(get_scenario("ramasser_derriere_meuble"))
    assert not kinds(events, EventKind.FALL_CONFIRMED)


def test_chute_lente_masquee_par_un_passant_reste_signalee():
    confirmed = kinds(analyse(get_scenario("chute_lente_pendant_passage")), EventKind.FALL_CONFIRMED)
    assert len(confirmed) == 1
    assert confirmed[0].cause in (FallCause.INDIVIDUAL, FallCause.UNDETERMINED)  # jamais « collision »


def test_temps_masque_ne_compte_pas_dans_le_maintien_au_sol():
    scenario = get_scenario("affaissement_lent")  # au sol à 6,5 s
    scenario.persons[0].hide(6.6, 9.0)
    confirmed = kinds(analyse(scenario), EventKind.FALL_CONFIRMED)
    assert confirmed and confirmed[0].timestamp_s >= 9.0 + DetectionThresholds().fast_fall_confirm_hold_s - 0.1


def test_assise_avec_images_douteuses_arme_quand_meme_le_detecteur():
    # Une image inexploitable isolée (faible lumière) ne doit pas empêcher l'armement d'une
    # personne assise, sans quoi sa chute depuis la chaise passerait inaperçue.
    scenario = get_scenario("chute_assise")
    pipeline = DetectionPipeline()
    events = []
    for k, t in enumerate(scenario.frame_times()):
        observation = scenario.observation_at(float(t))
        if k % 7 == 3 and t < 3.0:
            dark = observation.poses[0].landmarks.copy()
            dark[:, 2] = 0.1
            observation = FrameObservation(observation.timestamp_s, observation.frame_size_px, (PersonPose(dark),))
        events += pipeline.process(observation).events
    assert len(kinds(events, EventKind.FALL_CONFIRMED)) == 1


def test_apres_une_chute_vers_la_camera_la_posture_reste_au_sol():
    timeline = posture_timeline(get_scenario("chute_vers_camera"))  # au sol à 3,8 s
    after = {p for t, p in timeline if t >= 5.5}
    assert after == {Posture.LYING}
