"""Suivi de deux personnes au plus : identifiants temporaires, sans reconnaissance faciale.

Chaque trace garde un historique d'observations horodatées et une référence de taille propre à la
personne. Les vitesses ne sont jamais estimées à travers une interruption plus longue que
`motion_max_gap_s` : après une occultation, les mesures repartent des observations valides.
"""

from __future__ import annotations

import math
import statistics
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from itertools import permutations

from .config import DetectionThresholds
from .features import BodyFeatures, pose_bbox_px, pose_centroid_px, pose_extent_px
from .model import PersonPose, Posture

_HISTORY_S = 8.0  # historique conservé par personne, au-delà de la fenêtre de descente
PREDICTION_MAX_S = 0.6  # durée maximale d'extrapolation du centre d'une trace masquée
PREDICTION_MAX_TORSO = 1.5  # déplacement extrapolé maximal, en torses
CONTAINED_DUPLICATE_RATIO = 0.8  # part d'une boîte contenue dans une autre au-delà de laquelle c'est un doublon
_REFERENCE_POSTURES = (Posture.STANDING, Posture.WALKING)  # posture debout : référence de hauteur
_TORSO_REFERENCE_POSTURES = (Posture.STANDING, Posture.WALKING, Posture.SITTING)


class FallState(str, Enum):
    DISARMED = "disarmed"  # pas encore de posture de départ exploitable
    ARMED = "armed"
    SUSPECTED = "suspected"
    CONFIRMED = "confirmed"  # chute confirmée, en attente d'une reprise de posture


@dataclass
class TrackSample:
    t: float
    features: BodyFeatures | None
    center_px: tuple[float, float]
    posture: Posture = Posture.INDETERMINATE


@dataclass
class PersonTrack:
    track_id: int
    created_t: float
    thresholds: DetectionThresholds
    slot: int = 0  # numéro affiché (« Personne 1 » ou « Personne 2 »), attribué à la confirmation
    hits: int = 0  # observations appariées ; la trace est confirmée après `track_confirm_hits`
    samples: deque[TrackSample] = field(default_factory=deque)
    height_torso_ratios: deque[tuple[float, float]] = field(default_factory=deque)
    reference_torso_px: float = 0.0
    reference_updated_t: float = 0.0
    last_seen_t: float = 0.0
    last_center_px: tuple[float, float] = (0.0, 0.0)
    velocity_px_s: tuple[float, float] = (0.0, 0.0)
    present: bool = True
    fall_state: FallState = FallState.DISARMED
    # Mémoire du détecteur de chute.
    upright_since_t: float | None = None
    not_upright_since_t: float | None = None
    suspicion_upright_since_t: float | None = None
    low_since_t: float | None = None
    recovery_since_t: float | None = None
    suspicion_started_t: float | None = None
    suspicion_fast: bool = False  # la descente qui a ouvert la suspicion était rapide
    last_seen_low: bool = False
    last_seen_near_edge: bool = False
    last_hip_drop_torso: float = 0.0
    descent_start_t: float | None = None
    contact_t: float | None = None
    pose_lost_since_t: float | None = None
    floor_hip_y_px: deque[float] = field(default_factory=lambda: deque(maxlen=90))
    last_posture: Posture | None = None
    lost_reported: bool = False
    reported_posture: Posture | None = None
    pending_posture: Posture | None = None
    pending_posture_since_t: float = 0.0
    displayed_posture: Posture | None = None
    display_candidate: Posture | None = None
    display_candidate_since_t: float = 0.0
    last_fast_fall_t: float = -1e9

    @property
    def confirmed(self) -> bool:
        """Trace observée sur plusieurs images : une pose parasite isolée n'est ni affichée ni comptée."""
        return self.slot > 0

    # -- références de taille -------------------------------------------------------------------
    @property
    def last_features(self) -> BodyFeatures | None:
        for sample in reversed(self.samples):
            if sample.features is not None:
                return sample.features
        return None

    def scale_px(self) -> float:
        """Échelle du corps : torse de référence pris redressé, sinon le dernier torse observé."""
        if self.reference_torso_px > 0:
            return self.reference_torso_px
        features = self.last_features
        return features.torso_length_px if features is not None else 1.0

    def expected_height_px(self) -> float | None:
        """Hauteur debout attendue à la distance actuelle : rapport hauteur/torse × torse de référence.

        Le rapport ne dépend pas de la distance à la caméra, contrairement à une hauteur en pixels :
        une personne qui s'est approchée puis éloignée n'invalide pas sa référence.
        """
        if not self.height_torso_ratios or self.reference_torso_px <= 0:
            return None
        return statistics.median(r for _, r in self.height_torso_ratios) * self.reference_torso_px

    def height_ratio(self, features: BodyFeatures) -> float | None:
        """Hauteur apparente / hauteur debout attendue, None si l'une des deux n'est pas fiable."""
        expected = self.expected_height_px()
        if expected is None or not features.height_reliable:
            return None
        return features.bbox_height_px / expected

    def update_references(self, t: float, features: BodyFeatures, posture: Posture) -> None:
        """Met à jour la référence avec une observation redressée (appelé après le classement)."""
        th = self.thresholds
        if posture not in _TORSO_REFERENCE_POSTURES or features.torso_angle_deg >= th.upright_max_torso_angle_deg:
            return
        if self.fall_state in (FallState.SUSPECTED, FallState.CONFIRMED):
            return  # une personne tombée n'est pas debout, quel que soit l'angle apparent du torse
        _, descent_speed = self.hip_velocity_torso(th.motion_window_s, t)
        if descent_speed > th.reference_max_descent_speed_torso_per_s:
            return  # le torse peut se raccourcir en basculant vers la caméra : pas une référence
        if self.reference_torso_px <= 0:
            self.reference_torso_px = features.torso_length_px
        else:
            elapsed = max(t - self.reference_updated_t, 0.0)
            blend = 1.0 - math.exp(-elapsed / th.reference_torso_tau_s)
            self.reference_torso_px += blend * (features.torso_length_px - self.reference_torso_px)
        self.reference_updated_t = t
        if posture in _REFERENCE_POSTURES and features.height_reliable:
            ratio = features.bbox_height_px / features.torso_length_px
            if ratio >= th.min_height_torso_ratio:
                self.height_torso_ratios.append((t, ratio))
        while self.height_torso_ratios and t - self.height_torso_ratios[0][0] > th.reference_window_s:
            self.height_torso_ratios.popleft()

    def seed_reference(self, features: BodyFeatures | None) -> None:
        """Échelle provisoire pour une personne vue pour la première fois dans une posture non debout."""
        if features is not None and self.reference_torso_px <= 0:
            self.reference_torso_px = features.torso_length_px

    # -- historique et mouvement -------------------------------------------------------------------
    def record(self, t: float, features: BodyFeatures | None, center_px: tuple[float, float]) -> TrackSample:
        """Ajoute l'observation courante avant toute mesure : déplacement et durée portent sur les
        mêmes échantillons. La posture de l'échantillon est renseignée après le classement."""
        sample = TrackSample(t, features, center_px)
        self.hits += 1
        self.samples.append(sample)
        while self.samples and t - self.samples[0].t > _HISTORY_S:
            self.samples.popleft()
        self.last_seen_t = t
        self.last_center_px = center_px
        self.present = True
        self.velocity_px_s = self._center_velocity(t)
        return sample

    def _center_velocity(self, now_t: float) -> tuple[float, float]:
        """Vitesse du centre sur la fenêtre de mouvement continue (pente entre ses extrémités).

        Un écart entre deux images seulement amplifie le bruit de MediaPipe : une prédiction
        faussée projetterait la trace hors de sa porte et créerait une nouvelle identité.
        """
        window = [s for s in self.continuous_samples(self.thresholds.motion_window_s, now_t)]
        if len(window) < 2 or window[-1].t - window[0].t < 1e-3:
            return 0.0, 0.0
        elapsed = window[-1].t - window[0].t
        return (
            (window[-1].center_px[0] - window[0].center_px[0]) / elapsed,
            (window[-1].center_px[1] - window[0].center_px[1]) / elapsed,
        )

    def continuous_samples(self, window_s: float, now_t: float) -> list[TrackSample]:
        """Échantillons exploitables de la fenêtre, sans traverser d'interruption trop longue."""
        chosen: list[TrackSample] = []
        newer_t = None
        for sample in reversed(self.samples):
            if now_t - sample.t > window_s:
                break
            if newer_t is not None and newer_t - sample.t > self.thresholds.motion_max_gap_s:
                break
            newer_t = sample.t
            if sample.features is not None:
                chosen.append(sample)
        chosen.reverse()
        return chosen

    def hip_velocity_torso(self, window_s: float, now_t: float) -> tuple[float, float]:
        """(vx, vy) du centre des hanches en torses/s sur la fenêtre continue, y vers le bas."""
        window = self.continuous_samples(window_s, now_t)
        if len(window) < 2 or window[-1].t - window[0].t < 1e-3:
            return 0.0, 0.0
        first, last = window[0].features, window[-1].features
        elapsed = window[-1].t - window[0].t
        scale = self.scale_px()
        return (
            (last.hip_center_px[0] - first.hip_center_px[0]) / scale / elapsed,
            (last.hip_center_px[1] - first.hip_center_px[1]) / scale / elapsed,
        )

    def ground_speed_torso(self, window_s: float, now_t: float) -> tuple[float, float]:
        """Déplacement net au sol (torses/s) et variation relative de taille (1/s) sur la fenêtre.

        La marche de profil déplace les hanches latéralement ; la marche vers la caméra ou en sens
        inverse déplace les pieds verticalement dans l'image et change la taille apparente. Un
        balancement sur place oscille mais ne produit pas de déplacement net sur une seconde.
        """
        window = self.continuous_samples(window_s, now_t)
        if len(window) < 2 or window[-1].t - window[0].t < 0.5 * window_s:
            return 0.0, 0.0
        first, last = window[0].features, window[-1].features
        elapsed = window[-1].t - window[0].t
        scale = max(self.scale_px(), 1e-6)
        dx = last.hip_center_px[0] - first.hip_center_px[0]  # le torse avance quand on se penche, pas les hanches
        dy = 0.0
        if first.ankle_center_px is not None and last.ankle_center_px is not None:
            dy = last.ankle_center_px[1] - first.ankle_center_px[1]
        speed = math.hypot(dx, dy) / scale / elapsed
        # Marcher vers la caméra agrandit torse et silhouette dans les mêmes proportions ; s'effondrer
        # vers elle raccourcit la silhouette bien plus que le torse.
        torso_change = math.log(last.torso_length_px / first.torso_length_px)
        if first.height_reliable and last.height_reliable:
            height_change = math.log(max(last.bbox_height_px, 1.0) / max(first.bbox_height_px, 1.0))
            proportional = abs(height_change - torso_change) <= self.thresholds.walking_max_proportion_change
            scale_rate = abs(torso_change) / elapsed if proportional else 0.0
        else:
            # Pieds hors cadre : seul un torse qui grandit (approche) est un indice sûr ; un torse qui
            # rétrécit peut aussi basculer vers la caméra.
            scale_rate = max(torso_change, 0.0) / elapsed
        return speed, scale_rate

    def leg_swing_torso(self, window_s: float, now_t: float) -> float:
        """Amplitude de l'alternance des chevilles sur la fenêtre, en torses.

        En marchant, l'écart gauche-droite des chevilles change de signe à chaque pas, même à
        petite vitesse ou face à la caméra ; debout ou en se balançant, il reste stable.
        """
        offsets = [s.features.ankle_offset_px for s in self.continuous_samples(window_s, now_t)
                   if s.features.ankle_offset_px is not None]
        if len(offsets) < 3:
            return 0.0
        scale = max(self.scale_px(), 1e-6)

        def alternation(values: list[float]) -> float:
            # Seule une inversion compte : se lever ou s'asseoir déplace les pieds sans les alterner.
            return 2.0 * min(max(values), -min(values))

        return max(alternation([o[0] for o in offsets]), alternation([o[1] for o in offsets]), 0.0) / scale

    def hip_above_ankles_torso(self, features: BodyFeatures) -> float | None:
        """Hauteur des hanches au-dessus des chevilles, en torses : environ 1,8 debout, 0,9 accroupi
        ou à genoux, moins de 0,5 allongé. None si les chevilles sont masquées."""
        if features.ankle_center_px is None:
            return None
        return (features.ankle_center_px[1] - features.hip_center_px[1]) / max(self.scale_px(), 1e-6)

    def is_foreshortened(self, features: BodyFeatures) -> bool:
        """Torse nettement plus court que sa référence redressée : il pointe vers la caméra ou à l'opposé."""
        return (self.reference_torso_px > 0 and features.torso_length_px
                <= self.thresholds.tipping_torso_foreshortening_ratio * self.reference_torso_px)

    def is_tipping(self, features: BodyFeatures) -> bool:
        """Corps qui bascule : torse incliné ou nettement raccourci (basculement vers la caméra)."""
        return features.torso_angle_deg >= self.thresholds.falling_label_min_torso_angle_deg or self.is_foreshortened(features)

    def peak_descent_speed_torso(self, start_t: float, end_t: float, tipping_only: bool = False) -> float:
        """Vitesse de descente des hanches la plus forte de l'intervalle, en torses/s.

        `tipping_only` ne retient que les descentes où le corps bascule : s'asseoir d'un coup fait
        descendre les hanches aussi vite qu'une chute, mais torse droit.

        Chaque vitesse est mesurée sur au moins `speed_min_span_s` d'observations continues : entre
        deux images consécutives à 30 images/s, quelques pixels de bruit de MediaPipe suffiraient
        à simuler une descente rapide.
        """
        th = self.thresholds
        window = [s for s in self.samples if s.features is not None and start_t - th.speed_min_span_s <= s.t <= end_t]
        scale = self.scale_px()
        peak = 0.0
        begin = 0
        for end, sample in enumerate(window):
            if end > 0 and sample.t - window[end - 1].t > th.motion_max_gap_s:
                begin = end  # pas de vitesse à travers une interruption
                continue
            while begin < end and sample.t - window[begin + 1].t >= th.speed_min_span_s:
                begin += 1
            first = window[begin]
            span = sample.t - first.t
            if tipping_only and not (self.is_tipping(sample.features) or self.is_tipping(first.features)):
                continue
            if span >= th.speed_min_span_s and sample.t >= start_t:
                speed = (sample.features.hip_center_px[1] - first.features.hip_center_px[1]) / scale / span
                peak = max(peak, speed)
        return peak

    def predicted_center_px(self, t: float) -> tuple[float, float]:
        """Centre attendu : prédiction à vitesse constante, bornée en durée et en distance (une
        chute ne se prolonge pas, un croisement masqué dure rarement plus d'une demi-seconde)."""
        dt = min(t - self.last_seen_t, PREDICTION_MAX_S)
        shift_x, shift_y = self.velocity_px_s[0] * dt, self.velocity_px_s[1] * dt
        limit = PREDICTION_MAX_TORSO * self.scale_px()
        length = math.hypot(shift_x, shift_y)
        if length > limit:
            shift_x, shift_y = shift_x * limit / length, shift_y * limit / length
        return self.last_center_px[0] + shift_x, self.last_center_px[1] + shift_y


@dataclass(frozen=True)
class Detection:
    pose: PersonPose
    features: BodyFeatures | None
    center_px: tuple[float, float]
    extent_px: float
    bbox_px: tuple[float, float, float, float]

    @property
    def size_px(self) -> float:
        """Taille servant à comparer deux poses : torse si disponible, sinon diagonale / 3."""
        return self.features.torso_length_px if self.features is not None else self.extent_px / 3.0


def make_detection(pose: PersonPose, features: BodyFeatures | None,
                   frame_size_px: tuple[int, int], min_visibility: float = 0.5) -> Detection:
    center = features.torso_center_px if features is not None else pose_centroid_px(pose, frame_size_px)
    return Detection(pose, features, center, pose_extent_px(pose, frame_size_px, min_visibility),
                     pose_bbox_px(pose, frame_size_px, min_visibility))


def _overlap_of_first(first: tuple[float, float, float, float], second: tuple[float, float, float, float]) -> float:
    """Part de la boîte `first` recouverte par `second` (0 à 1)."""
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    area = max((first[2] - first[0]) * (first[3] - first[1]), 1e-6)
    return max(width, 0.0) * max(height, 0.0) / area


def drop_duplicates(detections: list[Detection], size_ratio: float, overlap_ratio: float) -> list[Detection]:
    """Écarte les doublons de MediaPipe : une pose nettement plus petite qui recouvre une autre.

    Une seconde personne réelle a une taille comparable ; une pose parasite posée sur une partie du
    corps (tête, jambes) est bien plus petite et largement contenue dans la silhouette principale.
    """
    kept: list[Detection] = []
    # Les plus grandes poses d'abord : un doublon est comparé à celle qu'il imite.
    for detection in sorted(detections, key=lambda d: d.size_px, reverse=True):
        nested = any(
            (detection.size_px < size_ratio * other.size_px
             and _overlap_of_first(detection.bbox_px, other.bbox_px) >= overlap_ratio)
            # Presque entièrement contenue dans une autre silhouette : seconde estimation du même
            # corps (fréquente pour une personne allongée). Un témoin debout près d'une personne au
            # sol dépasse largement de sa boîte et reste conservé.
            or _overlap_of_first(detection.bbox_px, other.bbox_px) >= CONTAINED_DUPLICATE_RATIO
            for other in kept
        )
        # Deux poses de taille comparable dont les torses se superposent décrivent la même personne :
        # deux personnes réelles, même l'une derrière l'autre, n'ont pas le même centre de torse.
        same_torso = any(
            math.dist(detection.center_px, other.center_px) < 0.5 * min(detection.size_px, other.size_px)
            for other in kept
        )
        if not nested and not same_torso:
            kept.append(detection)
    return kept


class PersonTracker:
    """Apparie les poses de l'image courante aux traces existantes.

    Le coût d'appariement combine la distance au centre prédit et l'écart de taille ; la meilleure
    affectation globale est retenue (au plus deux personnes : toutes les permutations sont testées),
    ce qui évite l'échange d'identités quand deux personnes se croisent. La porte d'appariement
    s'élargit avec la durée d'absence, sans dépasser `track_gate_max_torso`.
    """

    def __init__(self, thresholds: DetectionThresholds) -> None:
        self._thresholds = thresholds
        self._tracks: dict[int, PersonTrack] = {}
        self._next_id = 1

    @property
    def tracks(self) -> list[PersonTrack]:
        return sorted(self._tracks.values(), key=lambda track: track.track_id)

    def associate(self, t: float, detections: list[Detection]) -> list[tuple[PersonTrack, Detection]]:
        limit = self._thresholds.max_people
        detections = drop_duplicates(detections, self._thresholds.duplicate_size_ratio,
                                     self._thresholds.duplicate_overlap_ratio)
        detections = sorted(detections, key=lambda d: d.extent_px, reverse=True)[:limit]
        pairs = self._match(t, detections)
        matched_detection_ids = {id(detection) for _, detection in pairs}
        for detection in detections:
            if id(detection) in matched_detection_ids:
                continue
            track = self._new_track(t)
            if track is not None:
                pairs.append((track, detection))
        return pairs

    def _cost(self, track: PersonTrack, detection: Detection, t: float) -> float | None:
        th = self._thresholds
        scale = max(track.scale_px(), detection.size_px, 20.0)
        absence_s = max(t - track.last_seen_t, 0.0)
        gate = min(th.track_gate_torso + th.track_gate_growth_per_s * absence_s, th.track_gate_max_torso) * scale
        distance = math.dist(track.predicted_center_px(t), detection.center_px)
        if distance > gate:
            return None
        size_gap = abs(math.log(max(detection.size_px, 1.0) / max(track.scale_px(), 1.0)))
        cost = distance / scale + th.track_size_weight * size_gap
        if track.fall_state in (FallState.SUSPECTED, FallState.CONFIRMED):
            # À coût voisin, la pose revient à la personne en train de tomber ou tombée : une
            # seconde trace au même endroit ne doit pas lui voler ses observations.
            cost -= th.track_fall_state_bonus
        return cost

    def _match(self, t: float, detections: list[Detection]) -> list[tuple[PersonTrack, Detection]]:
        tracks = list(self._tracks.values())
        if not tracks or not detections:
            return []
        costs = {(i, j): self._cost(track, detection, t)
                 for i, track in enumerate(tracks) for j, detection in enumerate(detections)}
        best: tuple[int, float, list[tuple[int, int]]] = (0, 0.0, [])
        small, large = (tracks, detections) if len(tracks) <= len(detections) else (detections, tracks)
        for chosen in permutations(range(len(large)), len(small)):
            pairs = [(i, j) if small is tracks else (j, i) for i, j in zip(range(len(small)), chosen)]
            valid = [(i, j) for i, j in pairs if costs[(i, j)] is not None]
            total = sum(costs[pair] for pair in valid)
            # Priorité au plus grand nombre d'appariements, puis au coût total le plus faible.
            if len(valid) > best[0] or (len(valid) == best[0] and valid and total < best[1]):
                best = (len(valid), total, valid)
        return [(tracks[i], detections[j]) for i, j in best[2]]

    def _new_track(self, t: float) -> PersonTrack | None:
        if len(self._tracks) >= self._thresholds.max_people:
            # Remplace la trace perdue la plus ancienne, jamais une personne en suspicion de chute ou
            # tombée encore en mémoire : une pose parasite ne doit pas effacer une chute en cours.
            replaceable = [
                track for track in self._tracks.values()
                if not track.present and (not track.confirmed or track.fall_state not in (
                    FallState.SUSPECTED, FallState.CONFIRMED))
            ]
            if not replaceable:
                return None
            del self._tracks[min(replaceable, key=lambda track: track.last_seen_t).track_id]
        track = PersonTrack(self._next_id, t, self._thresholds)
        self._next_id += 1
        self._tracks[track.track_id] = track
        return track

    def confirm_tracks(self) -> None:
        """Confirme les traces observées assez souvent et leur attribue le plus petit numéro libre."""
        for track in self.tracks:
            if not track.confirmed and track.hits >= self._thresholds.track_confirm_hits:
                used = {other.slot for other in self._tracks.values()}
                track.slot = next(s for s in range(1, self._thresholds.max_people + 1) if s not in used)

    def expire(self, t: float) -> list[PersonTrack]:
        """Oublie les traces perdues depuis trop longtemps. Retourne les traces confirmées oubliées."""
        forgotten = []
        for track in list(self._tracks.values()):
            if not track.confirmed:
                memory = self._thresholds.track_lost_after_s  # pose parasite : oubliée sans bruit
            elif track.fall_state in (FallState.SUSPECTED, FallState.CONFIRMED):
                memory = self._thresholds.confirmed_track_memory_s
            else:
                memory = self._thresholds.track_memory_s
            if t - track.last_seen_t > memory:
                self._tracks.pop(track.track_id)
                if track.confirmed:
                    forgotten.append(track)
        return forgotten
