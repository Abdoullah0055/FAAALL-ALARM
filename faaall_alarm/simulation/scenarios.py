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
}


def get_scenario(name: str) -> Scenario:
    try:
        return SCENARIOS[name]()
    except KeyError as error:
        raise KeyError(f"Scénario inconnu : {name}. Disponibles : {', '.join(SCENARIOS)}") from error
