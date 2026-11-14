"""Scénarios de recette simulés. Chaque scénario produit des poses horodatées et une vidéo de mannequins."""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable

import numpy as np

from ..model import FrameObservation, PersonPose
from .skeleton import (
    BENDING, CROUCHING, LYING, SITTING, STANDING, BodyState, blend, build_pose,
)

FRAME_SIZE_PX = (640, 480)
GROUND_Y_PX = 440.0
TORSO_PX = 90.0
GAIT_HZ = 1.5


class Timeline:
    """Trajectoire d'une personne, définie par des états clés enchaînés."""

    def __init__(self, x_px: float, facing: int = 1, start: BodyState = STANDING,
                 torso_px: float = TORSO_PX) -> None:
        self.torso_px = torso_px
        self.keys: list[tuple[float, BodyState]] = [(0.0, replace(start, x_px=x_px, facing=facing))]
        self.hidden: list[tuple[float, float]] = []

    @property
    def end_t(self) -> float:
        return self.keys[-1][0]

    @property
    def last(self) -> BodyState:
        return self.keys[-1][1]

    def go(self, duration_s: float, template: BodyState, x_px: float | None = None,
           facing: int | None = None, **changes) -> "Timeline":
        state = replace(
            template, x_px=self.last.x_px if x_px is None else x_px,
            facing=self.last.facing if facing is None else facing, **changes,
        )
        self.keys.append((self.end_t + duration_s, state))
        return self

    def hold(self, duration_s: float) -> "Timeline":
        self.keys.append((self.end_t + duration_s, self.last))
        return self

    def walk_to(self, x_px: float, duration_s: float) -> "Timeline":
        walking = replace(STANDING, x_px=x_px, gait_deg=25.0, facing=1 if x_px >= self.last.x_px else -1)
        self.keys.append((self.end_t + duration_s, walking))
        return self

    def hide(self, start_s: float, end_s: float) -> "Timeline":
        self.hidden.append((start_s, end_s))
        return self

    def state_at(self, t: float) -> BodyState | None:
        if any(start <= t < end for start, end in self.hidden):
            return None
        if t <= self.keys[0][0]:
            return self.keys[0][1]
        for (t0, s0), (t1, s1) in zip(self.keys, self.keys[1:]):
            if t0 <= t <= t1:
                return s1 if t1 - t0 < 1e-9 else blend(s0, s1, (t - t0) / (t1 - t0))
        return self.keys[-1][1]


@dataclass
class Scenario:
    name: str
    description: str
    persons: list[Timeline]
    duration_s: float
    fps: float = 15.0
    expected: str = ""  # résultat attendu, repris dans la documentation
    lying_zones_px: list[tuple[float, float, float, float]] = field(default_factory=list)
    chair_x_px: float | None = None
    camera_loss: list[tuple[float, float]] = field(default_factory=list)
    frame_size_px: tuple[int, int] = FRAME_SIZE_PX
    expected_falls: int | None = None  # incidents attendus, pour le banc d'évaluation
    fall_contact_s: float | None = None  # instant où les hanches atteignent le sol (premier incident)
    expected_recoveries: int = 0  # reprises de posture fiables attendues

    @property
    def lying_zones(self) -> list[tuple[float, float, float, float]]:
        width, height = self.frame_size_px
        return [(x0 / width, y0 / height, x1 / width, y1 / height) for x0, y0, x1, y1 in self.lying_zones_px]

    def camera_available(self, t: float) -> bool:
        return not any(start <= t < end for start, end in self.camera_loss)

    def poses_at(self, t: float) -> list[PersonPose]:
        poses = []
        for person in self.persons:
            state = person.state_at(t)
            if state is not None:
                phase = 2 * math.pi * GAIT_HZ * t
                poses.append(build_pose(state, GROUND_Y_PX, person.torso_px, self.frame_size_px, phase))
        return poses

    def observation_at(self, t: float) -> FrameObservation:
        return FrameObservation(t, self.frame_size_px, tuple(self.poses_at(t)))

    def frame_times(self) -> np.ndarray:
        return np.arange(0.0, self.duration_s, 1.0 / self.fps)


# -- scénarios --------------------------------------------------------------------------------------
def _stand_walk_fall(x_start: float, fall_state: BodyState, fall_s: float, hold_s: float,
                     facing: int = 1) -> Timeline:
    timeline = Timeline(x_start, facing)
    timeline.hold(2.0).walk_to(x_start + 50 * facing, 2.0).go(0.5, STANDING).go(fall_s, fall_state, x_px=x_start + 140 * facing)
    return timeline.hold(hold_s)


def fall_standing() -> Scenario:
    person = _stand_walk_fall(250, replace(LYING, facing=1), 0.7, 30.0)
    return Scenario("chute_debout", "Une personne marche puis tombe et reste au sol.", [person], person.end_t,
                    expected="Un incident, voix une fois, décompte, SMS simulé.")


def fall_sitting() -> Scenario:
    person = Timeline(300, start=SITTING)
    person.hold(3.0).go(0.9, replace(LYING, facing=1), x_px=420).hold(40.0)
    return Scenario("chute_assise", "Une personne assise sur une chaise tombe de la chaise.", [person],
                    person.end_t, chair_x_px=300, expected="Un incident.")


def slow_collapse() -> Scenario:
    person = Timeline(300)
    person.hold(2.0).go(4.5, replace(LYING, facing=1), x_px=380).hold(40.0)
    return Scenario("affaissement_lent", "Une personne s'affaisse lentement jusqu'au sol.", [person],
                    person.end_t, expected="Un incident malgré l'absence de pic de vitesse.")


def fall_and_recover(recover_after_s: float = 5.0) -> Scenario:
    person = _stand_walk_fall(250, replace(LYING, facing=1), 0.7, recover_after_s)
    person.go(2.5, STANDING, x_px=person.last.x_px - 30).hold(12.0)
    return Scenario("chute_puis_reprise", "Chute puis reprise de la posture debout.", [person], person.end_t,
                    expected="Annulation automatique avant l'échéance si la reprise est soutenue.")


def ordinary_gestures() -> Scenario:
    person = Timeline(300)
    person.hold(2.0)
    person.walk_to(360, 2.0).go(0.5, STANDING)
    person.go(1.2, SITTING).hold(3.0).go(1.2, STANDING).hold(1.0)  # s'asseoir ordinairement
    person.go(1.0, CROUCHING).hold(2.0).go(1.0, STANDING).hold(1.0)  # s'accroupir
    person.go(1.0, BENDING).hold(2.0).go(1.0, STANDING).hold(2.0)  # se pencher
    return Scenario("gestes_ordinaires", "Marche, assise, accroupissement et penché en avant.", [person],
                    person.end_t, expected="Aucun incident.")


def lying_down_normally() -> Scenario:
    person = Timeline(450)
    person.hold(1.5).go(1.2, SITTING).hold(1.0).go(3.0, replace(LYING, facing=1), x_px=500).hold(8.0)
    return Scenario("coucher_normal", "Une personne s'allonge dans la zone du lit.", [person], person.end_t,
                    lying_zones_px=[(380.0, 360.0, 620.0, 450.0)], expected="Aucun incident (zone de repos).")


def already_lying_at_start() -> Scenario:
    person = Timeline(300, start=replace(LYING, facing=1))
    person.hold(6.0).go(2.5, STANDING).hold(3.0)
    return Scenario("allongee_au_depart", "Une personne déjà allongée au démarrage, puis debout.", [person],
                    person.end_t, expected="Aucun incident.")


def fall_next_to_bed() -> Scenario:
    person = _stand_walk_fall(250, replace(LYING, facing=1), 0.7, 30.0)
    return Scenario("chute_pres_du_lit", "Chute vers le sol à côté d'un lit.", [person], person.end_t,
                    lying_zones_px=[(470.0, 360.0, 640.0, 450.0)], expected="Un incident malgré le lit voisin.")


def fall_with_witness() -> Scenario:
    faller = _stand_walk_fall(300, replace(LYING, facing=-1), 0.7, 40.0, facing=-1)
    witness = Timeline(540, facing=-1)
    witness.hold(faller.end_t)
    return Scenario("chute_avec_temoin", "Une personne tombe, un témoin reste à distance.", [faller, witness],
                    faller.end_t, expected="Chute individuelle malgré la présence d'un témoin.")


def _approach(a_to: float, b_to: float) -> tuple[Timeline, Timeline]:
    first, second = Timeline(120), Timeline(520, facing=-1)
    first.hold(1.0).walk_to(a_to, 2.6)
    second.hold(1.0).walk_to(b_to, 2.6)
    return first, second


def collision_with_fall(both_fall: bool = False) -> Scenario:
    first, second = _approach(300, 340)
    first.go(0.7, replace(LYING, facing=-1), x_px=215).hold(40.0)
    if both_fall:
        second.go(0.8, replace(LYING, facing=1), x_px=440).hold(40.0)
    else:
        second.go(0.5, STANDING, x_px=360, facing=-1).hold(40.0)
    return Scenario("collision_avec_chute_2" if both_fall else "collision_avec_chute",
                    "Deux personnes se heurtent et la première tombe.", [first, second], first.end_t,
                    expected="Collision probable · chute possible, un seul incident.")


def collision_without_fall() -> Scenario:
    first, second = _approach(300, 340)
    first.go(0.6, STANDING, x_px=230, facing=-1).hold(10.0)
    second.go(0.6, STANDING, x_px=410).hold(10.0)
    return Scenario("collision_sans_chute", "Deux personnes se heurtent sans tomber.", [first, second],
                    first.end_t, expected="Collision journalisée, aucun SMS de chute.")


def crossing_without_contact() -> Scenario:
    first, second = Timeline(100), Timeline(540, facing=-1)
    first.hold(0.5).walk_to(540, 5.0).hold(3.0)
    second.hold(0.5).walk_to(100, 5.0).hold(3.0)
    return Scenario("croisement", "Deux personnes se croisent à vitesse constante.", [first, second],
                    first.end_t, expected="Aucune collision.")


def fall_with_uncertain_contact() -> Scenario:
    faller = Timeline(300)
    faller.hold(2.5).go(0.7, replace(LYING, facing=-1), x_px=190).hold(40.0)
    neighbour = Timeline(380, facing=-1)
    neighbour.hold(faller.end_t)
    return Scenario("chute_contact_incertain", "Une personne tombe à côté d'une autre, contact incertain.",
                    [faller, neighbour], faller.end_t, expected="Alerte avec Cause indéterminée.")


def fall_with_pose_loss() -> Scenario:
    scenario = fall_standing()
    scenario.persons[0].hide(7.0, 9.5)
    scenario.name, scenario.description = "chute_perte_de_suivi", "Chute puis disparition brève du squelette."
    scenario.expected = "L'incident reste ouvert avec le même identifiant, la voix ne recommence pas."
    return scenario


def fall_with_camera_loss() -> Scenario:
    scenario = fall_standing()
    scenario.camera_loss = [(7.0, 11.0)]
    scenario.name, scenario.description = "chute_perte_camera", "Chute puis coupure de la caméra pendant le décompte."
    scenario.expected = "Le décompte continue malgré la panne caméra."
    return scenario


def camera_loss_without_fall() -> Scenario:
    person = Timeline(300)
    person.hold(12.0)
    return Scenario("perte_camera_sans_chute", "Coupure de la caméra sans chute.", [person], person.end_t,
                    camera_loss=[(3.0, 8.0)], expected="Caméra inactive, aucun SMS.")


def two_falls_with_recovery() -> Scenario:
    person = _stand_walk_fall(250, replace(LYING, facing=1), 0.7, 4.0)
    person.go(2.5, STANDING, x_px=250).hold(2.0)
    person.go(0.7, replace(LYING, facing=1), x_px=390).hold(40.0)
    return Scenario("deux_chutes", "Chute, reprise, puis nouvelle chute.", [person], person.end_t,
                    expected="Deux incidents distincts.")


# -- gestes ordinaires et cadrages difficiles (reproduction des défauts de posture) -------------------
def fast_sit_down() -> Scenario:
    person = Timeline(320)
    person.hold(2.0).go(0.45, SITTING).hold(3.0).go(0.8, STANDING).hold(2.0)
    return Scenario("assise_rapide", "Une personne s'assoit d'un coup sur une chaise.", [person], person.end_t,
                    chair_x_px=320, expected="SITTING sans FALLING, aucun incident.")


def fast_crouch() -> Scenario:
    person = Timeline(320)
    person.hold(2.0).go(0.5, CROUCHING).hold(2.0).go(0.7, STANDING).hold(2.0)
    return Scenario("accroupissement_rapide", "Accroupissement rapide puis relevé.", [person], person.end_t,
                    expected="Aucun FALLING, aucun incident.")


def pick_up_object() -> Scenario:
    picking = replace(CROUCHING, torso_deg=70.0, hip_height_t=0.95)
    person = Timeline(300)
    person.hold(2.0).walk_to(360, 1.5).go(0.3, STANDING).go(0.6, picking).hold(1.0).go(0.6, STANDING).hold(2.0)
    return Scenario("ramasser_objet", "Une personne se baisse vite pour ramasser un objet.", [person],
                    person.end_t, expected="Aucun FALLING, aucun incident.")


def walk_towards_camera() -> Scenario:
    person = Timeline(320, start=replace(STANDING, depth_scale=0.7))
    person.hold(2.0)
    person.go(4.0, replace(STANDING, gait_deg=25.0, depth_scale=1.9)).go(0.4, replace(STANDING, depth_scale=1.9))
    person.hold(5.0)
    return Scenario("marche_vers_camera", "Une personne marche vers la caméra ; ses jambes sortent du cadre.",
                    [person], person.end_t, expected="WALKING puis posture debout ou indéterminée, aucun incident.")


def walk_away_from_camera() -> Scenario:
    person = Timeline(320, start=replace(STANDING, depth_scale=1.6))
    person.hold(2.0)
    person.go(4.0, replace(STANDING, gait_deg=25.0, depth_scale=0.7)).go(0.4, replace(STANDING, depth_scale=0.7))
    person.hold(3.0)
    return Scenario("marche_eloignement", "Une personne s'éloigne de la caméra.", [person], person.end_t,
                    expected="WALKING puis STANDING, aucun incident.")


def swaying_in_place() -> Scenario:
    person = Timeline(320)
    person.hold(1.5)
    for _ in range(8):  # balancement latéral de ±14 px (≈ 0,15 torse) à 1,25 Hz
        person.go(0.4, STANDING, x_px=334).go(0.4, STANDING, x_px=306)
    person.go(0.4, STANDING, x_px=320).hold(1.5)
    return Scenario("balancement", "Une personne debout se balance sur place.", [person], person.end_t,
                    expected="STANDING sans WALKING, aucun incident.")


def sit_facing_camera() -> Scenario:
    facing_sit = replace(SITTING, thigh_scale=0.3)
    person = Timeline(320)
    person.hold(2.0).go(1.2, facing_sit).hold(4.0).go(1.2, STANDING).hold(2.0)
    return Scenario("assise_de_face", "Une personne s'assoit face à la caméra (cuisses raccourcies).", [person],
                    person.end_t, expected="SITTING, aucun incident.")


def legs_out_of_frame() -> Scenario:
    hidden_legs = replace(STANDING, lower_body_visible=False)
    person = Timeline(250, start=hidden_legs)
    person.hold(3.0).go(3.0, replace(hidden_legs, gait_deg=25.0), x_px=400).go(0.4, hidden_legs).hold(3.0)
    return Scenario("jambes_hors_cadre", "Seul le haut du corps est visible (jambes masquées).", [person],
                    person.end_t, expected="Posture indéterminée plutôt qu'une posture devinée, aucun incident.")


# -- chutes difficiles -----------------------------------------------------------------------------------
def fall_towards_camera() -> Scenario:
    # Les pieds restent au fond, le corps s'allonge vers la caméra : à l'image, les jambes raccourcies
    # partent vers le haut depuis les hanches et le torse raccourci reste presque vertical.
    collapsed = replace(LYING, torso_deg=12.0, torso_scale=0.35, thigh_deg=175.0, thigh_scale=0.35,
                        shin_scale=0.35, hip_height_t=0.25, depth_scale=1.1)
    person = Timeline(320)
    person.hold(3.0).go(0.8, collapsed).hold(30.0)
    return Scenario("chute_vers_camera", "Chute vers la caméra : le torse reste presque vertical à l'image.",
                    [person], person.end_t, expected="Un incident malgré un torse peu incliné à l'image.")


def slow_collapse_with_occlusion() -> Scenario:
    scenario = slow_collapse()
    scenario.persons[0].hide(4.0, 5.6)  # la descente est masquée en partie (meuble, autre personne)
    scenario.name = "affaissement_lent_occulte"
    scenario.description = "Affaissement lent dont une partie de la descente est masquée."
    scenario.expected = "Un incident."
    return scenario


def fall_with_pose_lost_during_descent() -> Scenario:
    scenario = fall_standing()
    scenario.persons[0].hide(4.7, 5.5)  # MediaPipe perd la pose pendant la descente rapide
    scenario.name = "chute_pose_perdue_descente"
    scenario.description = "La pose disparaît pendant la descente puis revient au sol."
    scenario.expected = "Un incident."
    return scenario


def fall_from_sofa() -> Scenario:
    person = Timeline(470, start=SITTING)
    person.hold(3.0).go(0.9, replace(LYING, facing=-1), x_px=330).hold(30.0)
    return Scenario("chute_depuis_canape", "Une personne assise sur le canapé tombe au sol juste devant.",
                    [person], person.end_t, lying_zones_px=[(380.0, 330.0, 620.0, 425.0)],
                    expected="Un incident : le sol devant le canapé n'est pas une zone de repos.")


def fall_with_hips_in_bed_zone() -> Scenario:
    person = _stand_walk_fall(300, replace(LYING, facing=1), 0.7, 30.0)
    return Scenario("chute_hanches_zone_lit", "Chute rapide au sol, les hanches finissent dans le cadre du lit.",
                    [person], person.end_t, lying_zones_px=[(420.0, 340.0, 640.0, 470.0)],
                    expected="Un incident : la descente est une chute, pas un coucher.")


def partial_recovery_kneeling() -> Scenario:
    kneeling = replace(STANDING, hip_height_t=0.9, thigh_deg=0.0, knee_flex_deg=90.0)
    person = _stand_walk_fall(250, replace(LYING, facing=1), 0.7, 4.0)
    person.go(2.0, kneeling, x_px=person.last.x_px - 20).hold(14.0)
    return Scenario("reprise_partielle_genoux", "Après la chute, la personne se redresse à genoux sans se lever.",
                    [person], person.end_t, expected="L'incident reste ouvert : à genoux n'est pas une reprise.")


def partial_recovery_sitting_hidden_legs() -> Scenario:
    sitting_floor = replace(STANDING, hip_height_t=0.12, thigh_deg=90.0, lower_body_visible=False)
    person = _stand_walk_fall(250, replace(LYING, facing=1), 0.7, 4.0)
    person.go(1.5, replace(LYING, facing=1, lower_body_visible=False)).go(1.5, sitting_floor).hold(14.0)
    return Scenario("reprise_assise_au_sol", "Après la chute, assise au sol torse droit, jambes masquées.",
                    [person], person.end_t, expected="L'incident reste ouvert : le torse redressé ne suffit pas.")


def crossing_at_depths() -> Scenario:
    near = Timeline(80, start=replace(STANDING, depth_scale=1.1))
    far = Timeline(560, facing=-1, start=replace(STANDING, depth_scale=0.85))
    near.hold(0.5).go(5.0, replace(STANDING, gait_deg=25.0, depth_scale=1.1, facing=1), x_px=560).hold(2.0)
    far.hold(0.5).go(5.0, replace(STANDING, gait_deg=25.0, depth_scale=0.85, facing=-1), x_px=80).hold(2.0)
    return Scenario("croisement_profondeurs", "Deux personnes se croisent à des distances différentes.",
                    [near, far], near.end_t, expected="Aucune collision, identités conservées.")


def crossing_with_occlusion() -> Scenario:
    scenario = crossing_at_depths()
    scenario.persons[1].hide(2.6, 3.4)  # la personne du fond est masquée au moment du croisement
    scenario.name = "croisement_occulte"
    scenario.description = "Croisement pendant lequel la personne du fond est masquée."
    scenario.expected = "Aucune collision, identités conservées après l'occultation."
    return scenario


def pick_up_behind_furniture() -> Scenario:
    """Se baisser vite derrière un meuble (jambes masquées), disparaître, puis réapparaître debout."""
    hidden_legs = replace(STANDING, lower_body_visible=False)
    picking = replace(CROUCHING, torso_deg=75.0, hip_height_t=0.9, lower_body_visible=False)
    person = Timeline(320, start=hidden_legs)
    person.hold(2.5).go(0.45, picking).hold(3.5).go(0.6, hidden_legs).hold(3.0)
    person.hide(3.2, 6.3)  # derrière le meuble, plus rien n'est visible
    return Scenario("ramasser_derriere_meuble", "Se baisse vite derrière un meuble, disparaît puis se relève.",
                    [person], person.end_t, expected="Aucun incident : la disparition n'est pas une chute.")


def fall_while_crossing_occluded() -> Scenario:
    """Chute lente pendant qu'une autre personne passe devant (occultation et proximité)."""
    faller = Timeline(330)
    faller.hold(2.0).go(4.0, replace(LYING, facing=1), x_px=400).hold(30.0)
    walker = Timeline(80, start=replace(STANDING, depth_scale=1.15))
    walker.hold(1.5).go(5.0, replace(STANDING, gait_deg=25.0, depth_scale=1.15), x_px=600).hold(30.0)
    faller.hide(3.6, 4.6)
    return Scenario("chute_lente_pendant_passage", "Affaissement lent masqué par une personne qui passe devant.",
                    [faller, walker], faller.end_t, expected="Un incident, cause individuelle ou indéterminée.")


# Incidents attendus et instant où les hanches touchent le sol (banc d'évaluation). Les reprises
# attendues sont dans RECOVERIES ; ailleurs, aucune reprise ne doit être déclarée.
EXPECTATIONS: dict[str, tuple[int, float | None]] = {
    "chute_debout": (1, 5.2), "chute_assise": (1, 3.9), "affaissement_lent": (1, 6.5),
    "chute_puis_reprise": (1, 5.2), "gestes_ordinaires": (0, None), "coucher_normal": (0, None),
    "allongee_au_depart": (0, None), "chute_pres_du_lit": (1, 5.2), "chute_avec_temoin": (1, 5.2),
    "collision_avec_chute": (1, 4.3), "collision_avec_chute_2": (1, 4.3), "collision_sans_chute": (0, None),
    "croisement": (0, None), "chute_contact_incertain": (1, 3.2), "chute_perte_de_suivi": (1, 5.2),
    "chute_perte_camera": (1, 5.2), "perte_camera_sans_chute": (0, None), "deux_chutes": (2, 5.2),
    "assise_rapide": (0, None), "accroupissement_rapide": (0, None), "ramasser_objet": (0, None),
    "marche_vers_camera": (0, None), "marche_eloignement": (0, None), "balancement": (0, None),
    "assise_de_face": (0, None), "jambes_hors_cadre": (0, None), "chute_vers_camera": (1, 3.8),
    "affaissement_lent_occulte": (1, 6.5), "chute_pose_perdue_descente": (1, 5.2),
    "chute_depuis_canape": (1, 3.9), "chute_hanches_zone_lit": (1, 5.2),
    "reprise_partielle_genoux": (1, 5.2), "reprise_assise_au_sol": (1, 5.2),
    "croisement_profondeurs": (0, None), "croisement_occulte": (0, None),
    "ramasser_derriere_meuble": (0, None), "chute_lente_pendant_passage": (1, 6.0),
}
RECOVERIES = {"chute_puis_reprise": 1, "deux_chutes": 1}


SCENARIOS: dict[str, Callable[[], Scenario]] = {
    "chute_debout": fall_standing,
    "chute_assise": fall_sitting,
    "affaissement_lent": slow_collapse,
    "chute_puis_reprise": fall_and_recover,
    "gestes_ordinaires": ordinary_gestures,
    "coucher_normal": lying_down_normally,
    "allongee_au_depart": already_lying_at_start,
    "chute_pres_du_lit": fall_next_to_bed,
    "chute_avec_temoin": fall_with_witness,
    "collision_avec_chute": collision_with_fall,
    "collision_avec_chute_2": lambda: collision_with_fall(both_fall=True),
    "collision_sans_chute": collision_without_fall,
    "croisement": crossing_without_contact,
    "chute_contact_incertain": fall_with_uncertain_contact,
    "chute_perte_de_suivi": fall_with_pose_loss,
    "chute_perte_camera": fall_with_camera_loss,
    "perte_camera_sans_chute": camera_loss_without_fall,
    "deux_chutes": two_falls_with_recovery,
    "assise_rapide": fast_sit_down,
    "accroupissement_rapide": fast_crouch,
    "ramasser_objet": pick_up_object,
    "marche_vers_camera": walk_towards_camera,
    "marche_eloignement": walk_away_from_camera,
    "balancement": swaying_in_place,
    "assise_de_face": sit_facing_camera,
    "jambes_hors_cadre": legs_out_of_frame,
    "chute_vers_camera": fall_towards_camera,
    "affaissement_lent_occulte": slow_collapse_with_occlusion,
    "chute_pose_perdue_descente": fall_with_pose_lost_during_descent,
    "chute_depuis_canape": fall_from_sofa,
    "chute_hanches_zone_lit": fall_with_hips_in_bed_zone,
    "reprise_partielle_genoux": partial_recovery_kneeling,
    "reprise_assise_au_sol": partial_recovery_sitting_hidden_legs,
    "croisement_profondeurs": crossing_at_depths,
    "croisement_occulte": crossing_with_occlusion,
    "ramasser_derriere_meuble": pick_up_behind_furniture,
    "chute_lente_pendant_passage": fall_while_crossing_occluded,
}


def get_scenario(name: str) -> Scenario:
    try:
        scenario = SCENARIOS[name]()
    except KeyError as error:
        raise KeyError(f"Scénario inconnu : {name}. Disponibles : {', '.join(SCENARIOS)}") from error
    scenario.expected_falls, scenario.fall_contact_s = EXPECTATIONS.get(name, (None, None))
    scenario.expected_recoveries = RECOVERIES.get(name, 0)
    return scenario
