"""Détection des chutes et des collisions à partir d'observations de pose horodatées."""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass

from .config import DetectionThresholds
from .features import BodyFeatures, compute_features
from .model import (
    DetectionEvent, EventKind, FallCause, FrameAnalysis, FrameObservation, PersonView, Posture,
)
from .posture import PostureSmoother, classify_posture, is_low_arrival, is_upright, stays_low
from .tracking import (
    Detection, FallState, PersonTrack, PersonTracker, make_detection,
)

_UPRIGHT_POSTURES = (Posture.STANDING, Posture.WALKING, Posture.SITTING)
_PAIR_LOG_S = 15.0


Rectangle = tuple[float, float, float, float]


def _point_in_zone(point_px: tuple[float, float], zone: Rectangle, size_px: tuple[int, int]) -> bool:
    x, y = point_px[0] / size_px[0], point_px[1] / size_px[1]
    return zone[0] <= x <= zone[2] and zone[1] <= y <= zone[3]


class FallDetector:
    """Machine à états par personne : posture de départ, suspicion, confirmation et reprise.

    Une chute exige une descente des hanches (rapide ou lente), une posture d'arrivée basse et
    son maintien. Une posture déjà basse au démarrage ne déclenche rien : il faut d'abord observer
    une posture de départ exploitable (réarmement).

    MediaPipe perd souvent une personne allongée au sol. Une suspicion survit donc à une perte de
    pose : elle est confirmée si la pose revient basse et s'y maintient, ou si elle reste perdue
    après une chute rapide observée jusqu'à une posture basse, loin du bord de l'image. Une pose
    masquée n'est jamais une reprise.
    """

    def __init__(self, thresholds: DetectionThresholds, lying_zones: list[Rectangle] | None = None) -> None:
        self._th = thresholds
        self.lying_zones = lying_zones or []

    # -- images où la personne est visible ------------------------------------------------------
    def update_present(
        self, track: PersonTrack, t: float, features: BodyFeatures | None, posture: Posture,
        frame_size_px: tuple[int, int],
    ) -> list[DetectionEvent]:
        if features is None:
            return self.update_absent(track, t)
        track.pose_lost_since_t = None
        upright = is_upright(features, track, self._th) and posture is not Posture.LYING
        handler = {
            FallState.DISARMED: self._when_disarmed,
            FallState.ARMED: self._when_armed,
            FallState.SUSPECTED: self._when_suspected,
            FallState.CONFIRMED: self._when_confirmed,
        }[track.fall_state]
        return handler(track, t, features, posture, upright, frame_size_px)

    # -- images où la pose est absente ou inexploitable -------------------------------------------
    def update_absent(self, track: PersonTrack, t: float) -> list[DetectionEvent]:
        track.upright_since_t = None
        track.recovery_since_t = None
        if track.fall_state is not FallState.SUSPECTED:
            return []
        if track.pose_lost_since_t is None:
            track.pose_lost_since_t = t
        lost_s = t - track.pose_lost_since_t
        landed = track.last_seen_low or track.last_hip_drop_torso >= self._th.lost_confirm_min_drop_torso
        if track.suspicion_fast and landed and not track.last_seen_near_edge and (
                lost_s >= self._th.lost_after_fast_fall_confirm_s):
            return [self._confirm(track, t, "chute rapide jusqu'au sol puis pose masquée")]
        if t - (track.suspicion_started_t or t) > self._th.suspicion_timeout_s:
            return [self._clear_suspicion(track, t, "perte de pose avant confirmation", FallState.DISARMED)]
        return []

    def _when_disarmed(self, track, t, features, posture, upright, size):
        if not upright:
            track.upright_since_t = None
            return []
        if track.upright_since_t is None:
            track.upright_since_t = t
        if t - track.upright_since_t >= self._th.rearm_hold_s:
            track.fall_state = FallState.ARMED
        return []

    def _when_armed(self, track, t, features, posture, upright, size):
        track.upright_since_t = t if upright else None
        drop_torso, descent_start_t = self._hip_drop(track, t, features)
        if drop_torso < self._th.min_hip_drop_torso or not is_low_arrival(features, track, self._th):
            return []
        # Chute rapide : basculement rapide juste avant l'arrivée basse. S'asseoir au bord du lit en
        # se penchant puis s'allonger enchaîne deux gestes séparés par une pause : seul le dernier compte.
        recent_start_t = max(descent_start_t, t - self._th.fast_fall_recent_s)
        peak_speed = track.peak_descent_speed_torso(recent_start_t, t, tipping_only=True)
        fast = peak_speed >= self._th.falling_descent_speed_torso_per_s
        in_zone = any(_point_in_zone(features.hip_center_px, zone, size) for zone in self.lying_zones)
        if in_zone and peak_speed < self._th.zone_fall_descent_speed_torso_per_s:
            # S'allonger sur le lit ou le canapé, même en s'y laissant tomber, est une descente
            # contrôlée. Une chute franche qui finit dans le cadre d'une zone (sol juste devant le
            # lit, zone tracée trop large) reste une chute possible.
            track.fall_state = FallState.DISARMED
            return [DetectionEvent(
                EventKind.RESTING_ZONE, t, (track.track_id,), posture=Posture.LYING,
                detail="Posture allongée dans une zone de repos, aucune alerte",
            )]
        track.fall_state = FallState.SUSPECTED
        track.suspicion_started_t = t
        track.suspicion_upright_since_t = None
        track.suspicion_fast = fast
        track.last_seen_low = True
        track.last_seen_near_edge = False
        track.last_hip_drop_torso = drop_torso
        track.descent_start_t = descent_start_t
        track.contact_t = t
        track.low_since_t = t
        track.floor_hip_y_px.clear()
        speed_text = "rapide" if fast else "lente"
        return [DetectionEvent(
            EventKind.FALL_SUSPECTED, t, (track.track_id,), posture=Posture.FALLING,
            detail=f"descente {speed_text} des hanches de {drop_torso:.1f} torse, posture basse",
            contact_timestamp_s=t,
        )]

    def _when_suspected(self, track, t, features, posture, upright, size):
        self._note_suspicion_evidence(track, t, features, size)
        if upright:
            # Une seule image redressée peut venir d'une pose bruitée : le geste n'est terminé
            # qu'après un redressement soutenu.
            if track.suspicion_upright_since_t is None:
                track.suspicion_upright_since_t = t
            if t - track.suspicion_upright_since_t >= self._th.suspicion_clear_hold_s:
                return [self._clear_suspicion(track, t, "geste terminé sans chute", FallState.ARMED)]
            return []
        track.suspicion_upright_since_t = None
        if t - (track.suspicion_started_t or t) > self._th.suspicion_timeout_s:
            return [self._clear_suspicion(track, t, "posture basse non maintenue", FallState.DISARMED)]
        if not track.last_seen_low:
            track.low_since_t = None
            return []
        track.floor_hip_y_px.append(features.hip_center_px[1])
        if track.low_since_t is None:
            track.low_since_t = t
        speed = math.hypot(*track.hip_velocity_torso(self._th.motion_window_s, t))
        # Une chute franche (basculement rapide) est confirmée plus tôt qu'une descente lente, plus
        # proche d'un geste volontaire : les deux maintiens sont réglés séparément.
        hold_s = self._th.fast_fall_confirm_hold_s if track.suspicion_fast else self._th.confirm_hold_s
        if t - track.low_since_t < hold_s or speed > self._th.settle_speed_torso_per_s:
            return []
        return [self._confirm(track, t, "chute rapide, posture basse maintenue" if track.suspicion_fast
                              else "descente lente, posture basse maintenue")]

    def _note_suspicion_evidence(self, track, t, features, size) -> None:
        """Mémorise ce que montre la dernière pose visible, utile si elle disparaît ensuite."""
        # Près d'un bord latéral ou du haut de l'image, une pose qui disparaît peut être une personne
        # qui sort du cadre. Le bas est exclu : une chute vers la caméra finit souvent contre ce bord.
        x0, y0, x1, _ = features.bbox_px
        margin_x, margin_y = self._th.frame_edge_margin_ratio * size[0], self._th.frame_edge_margin_ratio * size[1]
        track.last_seen_near_edge = x0 <= margin_x or y0 <= margin_y or x1 >= size[0] - margin_x
        track.last_seen_low = stays_low(features, track, self._th)
        track.last_hip_drop_torso, _ = self._hip_drop(track, t, features)
        # Le basculement rapide peut suivre l'arrivée basse (chute vers la caméra amorcée en se
        # penchant) : il est pris en compte pendant toute la suspicion.
        if not track.suspicion_fast and track.peak_descent_speed_torso(
                t - self._th.fast_fall_recent_s, t, tipping_only=True) >= self._th.falling_descent_speed_torso_per_s:
            track.suspicion_fast = True

    def _when_confirmed(self, track, t, features, posture, upright, size):
        if not upright or not self._hips_back_up(track, features, posture):
            if not upright and stays_low(features, track, self._th):
                track.floor_hip_y_px.append(features.hip_center_px[1])
            track.recovery_since_t = None
            return []
        if track.recovery_since_t is None:
            track.recovery_since_t = t
        if t - track.recovery_since_t < self._th.recovery_hold_s:
            return []
        track.fall_state = FallState.ARMED  # la posture de reprise sert de posture de départ
        track.upright_since_t = t
        track.recovery_since_t = None
        return [DetectionEvent(
            EventKind.PERSON_RECOVERED, t, (track.track_id,), posture=posture,
            detail="reprise de posture fiable et soutenue",
        )]

    # -- utilitaires ----------------------------------------------------------------------------
    def _confirm(self, track: PersonTrack, t: float, reason: str) -> DetectionEvent:
        track.fall_state = FallState.CONFIRMED
        track.recovery_since_t = None
        track.pose_lost_since_t = None
        return DetectionEvent(
            EventKind.FALL_CONFIRMED, t, (track.track_id,), posture=Posture.LYING,
            detail=reason, contact_timestamp_s=track.contact_t,
        )

    def _hips_back_up(self, track: PersonTrack, features: BodyFeatures, posture: Posture) -> bool:
        """Reprise : les hanches sont remontées nettement au-dessus du sol, pas seulement le torse.

        À genoux ou assis au sol torse droit, les hanches restent près du sol : ce n'est pas une
        reprise. Jambes visibles, la personne doit être debout ou en marche ; jambes masquées, la
        remontée exigée est plus forte.
        """
        if not track.floor_hip_y_px:
            return posture in (Posture.STANDING, Posture.WALKING)
        floor_y = sorted(track.floor_hip_y_px)[len(track.floor_hip_y_px) // 2]
        rise_torso = (floor_y - features.hip_center_px[1]) / track.scale_px()
        if features.legs_visible:
            return posture in (Posture.STANDING, Posture.WALKING) and rise_torso >= self._th.recovery_hip_rise_torso
        return rise_torso >= self._th.recovery_hip_rise_hidden_legs_torso

    def _hip_drop(self, track: PersonTrack, t: float, features: BodyFeatures) -> tuple[float, float]:
        """Descente maximale des hanches dans la fenêtre, en longueurs de torse, et début de la descente.

        La descente se mesure par positions, éventuellement à travers une courte perte de pose
        (`descent_max_gap_s`) : une pose qui disparaît pendant la chute ne l'efface pas.
        """
        window: list = []
        newer_t = None
        for sample in reversed(track.samples):
            if t - sample.t > self._th.descent_window_s:
                break
            if newer_t is not None and newer_t - sample.t > self._th.descent_max_gap_s:
                break
            newer_t = sample.t
            if sample.features is not None:
                window.append(sample)
        if not window:
            return 0.0, t
        highest = min(window, key=lambda s: s.features.hip_center_px[1])
        drop_px = features.hip_center_px[1] - highest.features.hip_center_px[1]
        drop_torso = drop_px / track.scale_px()
        tolerance_px = 0.15 * max(drop_px, 0.0)
        top = highest.features.hip_center_px[1] + tolerance_px
        start_t = max(
            (s.t for s in window if s.features.hip_center_px[1] <= top and s.t <= t), default=highest.t
        )
        return drop_torso, start_t

    def _clear_suspicion(self, track, t, reason, next_state) -> DetectionEvent:
        track.fall_state = next_state
        track.low_since_t = None
        track.pose_lost_since_t = None
        track.suspicion_fast = False
        track.suspicion_upright_since_t = None
        track.upright_since_t = t if next_state is FallState.ARMED else None
        return DetectionEvent(EventKind.SUSPICION_CLEARED, t, (track.track_id,), detail=reason)


@dataclass
class _PairSample:
    t: float
    distance_torso: float | None  # None : au moins une des deux poses est inexploitable


@dataclass
class _ContactCandidate:
    contact_t: float
    pre_motion: dict[int, tuple[float, float, float]]  # id -> (vx, vy, vitesse) en torse/s
    post_samples: dict[int, list[tuple[float, float, float]]]  # id -> [(t, x, y)] en torse


@dataclass
class _PendingCollision:
    event: DetectionEvent
    deadline_t: float


class CollisionDetector:
    """Distingue un contact entre deux personnes d'un simple croisement.

    Un contact net combine une convergence rapide, une distance de contact et un changement
    de mouvement (arrêt, rebond ou changement de direction) chez au moins une personne dans la
    seconde qui suit. Une proximité ou un chevauchement seul ne suffit pas.
    """

    def __init__(self, thresholds: DetectionThresholds) -> None:
        self._th = thresholds
        self._pair_log: deque[_PairSample] = deque()
        self._candidate: _ContactCandidate | None = None
        self._pending: list[_PendingCollision] = []
        self._collisions: list[tuple[float, tuple[int, int]]] = []
        self._uncertain_windows: list[tuple[float, float, tuple[int, int]]] = []
        self._track_positions: dict[int, deque[tuple[float, float, float]]] = {}

    def update(self, t: float, present: list[PersonTrack]) -> list[DetectionEvent]:
        events: list[DetectionEvent] = []
        usable = [tr for tr in present if tr.samples and tr.samples[-1].features is not None]
        for track in present:
            self._remember_position(track, t)
        if len(present) == 2:
            first, second = sorted(present, key=lambda tr: tr.track_id)
            distance = self._distance_torso(first, second) if len(usable) == 2 else None
            self._pair_log.append(_PairSample(t, distance))
            if distance is not None:
                events += self._advance_contact(t, first, second, distance)
            if distance is not None and distance <= self._th.overlap_distance_torso:
                self._note_overlap(t, (first.track_id, second.track_id))
        while self._pair_log and t - self._pair_log[0].t > _PAIR_LOG_S:
            self._pair_log.popleft()
        events += self._resolve_pending(t)
        return events

    def note_fall(self, track_ids: tuple[int, ...]) -> None:
        """Une chute rattachée à une collision la sort de la liste « collision sans chute »."""
        self._pending = [p for p in self._pending if not set(p.event.track_ids) & set(track_ids)]

    def collision_involving(self, track_id: int, start_t: float, end_t: float) -> bool:
        return any(
            start_t <= contact_t <= end_t and track_id in ids for contact_t, ids in self._collisions
        )

    def contact_uncertain(self, track_id: int, start_t: float, end_t: float) -> bool:
        """Silhouettes confondues ou témoin masqué à proximité pendant la fenêtre."""
        return any(
            window_end >= start_t and window_start <= end_t and track_id in ids
            for window_start, window_end, ids in self._uncertain_windows
        )

    # -- interne ----------------------------------------------------------------------------------
    def _remember_position(self, track: PersonTrack, t: float) -> None:
        history = self._track_positions.setdefault(track.track_id, deque())
        if track.samples and track.samples[-1].features is not None:
            scale = track.scale_px()
            history.append((t, track.last_center_px[0] / scale, track.last_center_px[1] / scale))
        while history and t - history[0][0] > 3.0:
            history.popleft()

    def _distance_torso(self, first: PersonTrack, second: PersonTrack) -> float:
        scale = (first.scale_px() + second.scale_px()) / 2.0
        return math.dist(first.last_center_px, second.last_center_px) / scale

    def _motion_between(self, track_id: int, start_t: float, end_t: float) -> tuple[float, float, float] | None:
        points = [p for p in self._track_positions.get(track_id, ()) if start_t <= p[0] <= end_t]
        if len(points) < 2 or points[-1][0] - points[0][0] < 1e-3:
            return None
        span = points[-1][0] - points[0][0]
        vx, vy = (points[-1][1] - points[0][1]) / span, (points[-1][2] - points[0][2]) / span
        return vx, vy, math.hypot(vx, vy)

    def _advance_contact(self, t, first, second, distance) -> list[DetectionEvent]:
        ids = (first.track_id, second.track_id)
        if self._candidate is None:
            if distance <= self._th.contact_distance_torso and self._closing_speed(t, distance) >= (
                self._th.closing_speed_torso_per_s
            ):
                pre = {}
                for track_id in ids:
                    motion = self._motion_between(
                        track_id, t - self._th.approach_window_s, t - 0.1
                    )
                    pre[track_id] = motion if motion is not None else (0.0, 0.0, 0.0)
                self._candidate = _ContactCandidate(t, pre, {i: [] for i in ids})
            return []
        candidate = self._candidate
        if set(candidate.post_samples) != set(ids):
            # Une trace a été remplacée pendant la fenêtre d'observation : le contact candidat ne
            # concerne plus les mêmes personnes et ne peut pas être conclu.
            self._candidate = None
            return []
        for track in (first, second):
            scale = track.scale_px()
            candidate.post_samples[track.track_id].append(
                (t, track.last_center_px[0] / scale, track.last_center_px[1] / scale)
            )
        if t - candidate.contact_t < self._th.post_contact_window_s:
            return []
        self._candidate = None
        if self._motion_changed(candidate, ids):
            self._collisions.append((candidate.contact_t, ids))
            event = DetectionEvent(
                EventKind.COLLISION_DETECTED, t, ids, contact_timestamp_s=candidate.contact_t,
                detail="contact entre deux personnes suivi d'un changement de mouvement",
            )
            self._pending.append(_PendingCollision(event, t + self._th.collision_resolve_s))
            return [event]
        return [DetectionEvent(
            EventKind.CROSSING_IGNORED, t, ids, contact_timestamp_s=candidate.contact_t,
            detail="croisement sans changement de mouvement, aucune collision",
        )]

    def _closing_speed(self, t: float, distance: float) -> float:
        reference = next(
            (s for s in self._pair_log if s.distance_torso is not None
             and t - s.t <= self._th.approach_window_s), None,
        )
        if reference is None or reference.distance_torso is None or t - reference.t < 1e-3:
            return 0.0
        return (reference.distance_torso - distance) / (t - reference.t)

    def _motion_changed(self, candidate: _ContactCandidate, ids: tuple[int, int]) -> bool:
        samples_needed = self._th.min_contact_samples
        for track_id in ids:
            pre_vx, pre_vy, pre_speed = candidate.pre_motion[track_id]
            post = candidate.post_samples[track_id]
            if len(post) < samples_needed + 1 or pre_speed < 0.2:
                continue
            span = post[-1][0] - post[0][0]
            if span < 1e-3:
                continue
            post_vx, post_vy = (post[-1][1] - post[0][1]) / span, (post[-1][2] - post[0][2]) / span
            post_speed = math.hypot(post_vx, post_vy)
            if post_speed <= (1.0 - self._th.speed_change_ratio) * pre_speed:
                return True
            if post_speed > 0.2:
                cosine = (pre_vx * post_vx + pre_vy * post_vy) / (pre_speed * post_speed)
                if math.degrees(math.acos(max(-1.0, min(1.0, cosine)))) >= self._th.direction_change_deg:
                    return True
        return False

    def _note_overlap(self, t: float, ids: tuple[int, int]) -> None:
        if self._uncertain_windows and self._uncertain_windows[-1][2] == ids and (
            t - self._uncertain_windows[-1][1] <= 1.0
        ):
            start, _, _ = self._uncertain_windows[-1]
            self._uncertain_windows[-1] = (start, t, ids)
        else:
            self._uncertain_windows.append((t, t, ids))
        self._uncertain_windows = self._uncertain_windows[-20:]

    def note_hidden_witness(self, t: float, present_id: int, hidden_id: int) -> None:
        """Un témoin proche disparaît pendant l'action : le contact reste incertain."""
        self._note_overlap(t, tuple(sorted((present_id, hidden_id))))  # type: ignore[arg-type]

    def _resolve_pending(self, t: float) -> list[DetectionEvent]:
        due = [p for p in self._pending if t >= p.deadline_t]
        self._pending = [p for p in self._pending if t < p.deadline_t]
        return [
            DetectionEvent(
                EventKind.COLLISION_WITHOUT_FALL, t, p.event.track_ids,
                contact_timestamp_s=p.event.contact_timestamp_s,
                detail="collision probable sans chute détectée, journalisée sans SMS de chute",
            )
            for p in due
        ]


class DetectionPipeline:
    """Enchaîne extraction d'indices, suivi, posture, chute et collision pour chaque image."""

    def __init__(
        self,
        thresholds: DetectionThresholds | None = None,
        monitored_zone: Rectangle | None = None,
        lying_zones: list[Rectangle] | None = None,
        diagnostics: Callable[[dict], None] | None = None,
    ) -> None:
        self.thresholds = thresholds or DetectionThresholds()
        # Diagnostic local, désactivé par défaut : un enregistrement par personne et par image.
        self.diagnostics = diagnostics
        self.tracker = PersonTracker(self.thresholds)
        self.smoother = PostureSmoother(self.thresholds)
        self.falls = FallDetector(self.thresholds, lying_zones)
        self.collisions = CollisionDetector(self.thresholds)
        self.monitored_zone = monitored_zone

    def configure_zones(self, monitored_zone: Rectangle | None, lying_zones: list[Rectangle]) -> None:
        self.monitored_zone = monitored_zone
        self.falls.lying_zones = lying_zones

    def label_for(self, track_id: int) -> str:
        """« Personne » seule, sinon « Personne 1 » ou « Personne 2 » selon le numéro attribué à la
        confirmation de la trace : il ne change pas quand l'autre trace est oubliée ou remplacée."""
        tracks = {track.track_id: track for track in self.tracker.tracks if track.confirmed}
        if len(tracks) < 2 or track_id not in tracks:
            return "Personne"
        return f"Personne {tracks[track_id].slot}"

    def process(self, observation: FrameObservation) -> FrameAnalysis:
        t, size = observation.timestamp_s, observation.frame_size_px
        analysis = FrameAnalysis(timestamp_s=t)
        detections = self._detections(observation)
        pairs = self.tracker.associate(t, detections)
        matched_ids = {track.track_id for track, _ in pairs}

        tentative_events: list[DetectionEvent] = []
        for track, detection in pairs:
            events = self._update_matched_track(track, detection, t, size)
            (analysis.events if track.confirmed else tentative_events).extend(events)
        self.tracker.confirm_tracks()
        for track, detection in pairs:
            if track.confirmed:
                analysis.persons.append(PersonView(
                    track.track_id, "", track.last_posture or Posture.INDETERMINATE, detection.pose.landmarks,
                ))
        # Une trace provisoire n'émet que ses événements de chute : son journal de posture attend
        # la confirmation, une pose parasite isolée ne laisse ainsi aucune trace.
        analysis.events += [e for e in tentative_events if e.kind is not EventKind.POSTURE_CHANGED]
        for track in self.tracker.tracks:
            if track.track_id not in matched_ids:
                analysis.events += self._update_absent_track(track, t)
                view = self._masked_view(track, t) if track.confirmed else None
                if view is not None:
                    analysis.persons.append(view)

        present = [track for track in self.tracker.tracks if track.present and track.confirmed]
        analysis.events += self.collisions.update(t, present)
        analysis.events = self._attach_causes(analysis.events, t)
        for forgotten in self.tracker.expire(t):
            analysis.events.append(DetectionEvent(
                EventKind.TRACK_LOST, t, (forgotten.track_id,), detail="trace oubliée après une longue absence",
            ))
        analysis.persons = [
            PersonView(view.track_id, self.label_for(view.track_id), view.posture, view.landmarks)
            for view in analysis.persons
        ]
        analysis.events = [self._label_event(event) for event in analysis.events]
        if self.diagnostics is not None:
            self._emit_diagnostics(t, len(observation.poses), len(detections), pairs, analysis)
        return analysis

    def _emit_diagnostics(self, t, pose_count, kept_count, pairs, analysis) -> None:
        """Points exploitables, indices calculés, pistes et motif des décisions (aucune image)."""
        matched = {track.track_id: detection for track, detection in pairs}
        for track in self.tracker.tracks:
            detection = matched.get(track.track_id)
            features = detection.features if detection is not None else None
            record = {
                "t": round(t, 3), "poses": pose_count, "poses_retenues": kept_count,
                "piste": track.track_id, "libelle": self.label_for(track.track_id), "presente": detection is not None,
                "etat_chute": track.fall_state.value, "posture": (track.last_posture or Posture.INDETERMINATE).value,
                "nouvelle_piste": abs(track.created_t - t) < 1e-9,
                "evenements": [e.kind.value for e in analysis.events if track.track_id in e.track_ids],
            }
            if detection is not None:
                visible = detection.pose.landmarks[:, 2] >= self.thresholds.min_landmark_visibility
                record["points_fiables"] = int(visible.sum())
            if features is not None:
                ratio = track.height_ratio(features)
                hip_above = track.hip_above_ankles_torso(features)
                record.update({
                    "visibilite_tronc": round(features.core_visibility, 2),
                    "angle_torse_deg": round(features.torso_angle_deg, 1),
                    "torse_px": round(features.torso_length_px, 1),
                    "torse_reference_px": round(track.reference_torso_px, 1),
                    "ratio_hauteur": None if ratio is None else round(ratio, 2),
                    "hanches_sur_chevilles_torse": None if hip_above is None else round(hip_above, 2),
                    "jambes_visibles": features.legs_visible, "hauteur_fiable": features.height_reliable,
                    "arrivee_basse": is_low_arrival(features, track, self.thresholds),
                    "redresse": is_upright(features, track, self.thresholds),
                })
            self.diagnostics(record)

    # -- interne ----------------------------------------------------------------------------------
    def _detections(self, observation: FrameObservation) -> list[Detection]:
        size = observation.frame_size_px
        detections = []
        for pose in observation.poses:
            detection = make_detection(
                pose, compute_features(pose, size, self.thresholds), size, self.thresholds.min_landmark_visibility,
            )
            if self.monitored_zone is None or _point_in_zone(detection.center_px, self.monitored_zone, size):
                detections.append(detection)
        return detections

    def _update_matched_track(self, track, detection, t, size) -> list[DetectionEvent]:
        events: list[DetectionEvent] = []
        if track.lost_reported:
            track.lost_reported = False
            events.append(DetectionEvent(EventKind.TRACK_RESUMED, t, (track.track_id,), detail="suivi repris"))
        features = detection.features
        track.seed_reference(features)
        # L'observation entre dans l'historique avant toute mesure : vitesse et durée portent sur
        # les mêmes échantillons, image courante comprise.
        sample = track.record(t, features, detection.center_px)
        instant = classify_posture(features, track, t, self.thresholds)
        sample.posture = instant
        if features is not None:
            track.update_references(t, features, instant)
        events += self.falls.update_present(track, t, features, instant, size)
        if track.fall_state is FallState.SUSPECTED:
            instant = Posture.FALLING  # la suspicion posée pendant cette image s'affiche tout de suite
        track.last_posture = self.smoother.update(track, instant, t)
        events += self._posture_event(track, track.last_posture, t)
        return events

    def _masked_view(self, track: PersonTrack, t: float) -> PersonView | None:
        """Personne suivie dont la pose manque sur cette image.

        Une absence brève (une ou deux images perdues par MediaPipe) garde la posture affichée, pour
        ne pas faire clignoter « Aucune personne détectée ». Au-delà, une personne en suspicion de
        chute ou tombée reste à l'écran en « Posture indéterminée » : sa pose est masquée, ce qui
        n'est ni une reprise ni une absence. Le squelette n'est pas redessiné (aucun point courant).
        """
        absence_s = t - track.last_seen_t
        if absence_s < self.thresholds.track_lost_after_s and track.last_posture is not None:
            return PersonView(track.track_id, "", track.last_posture, None)
        if track.fall_state in (FallState.SUSPECTED, FallState.CONFIRMED):
            return PersonView(track.track_id, "", Posture.INDETERMINATE, None)
        return None

    def _update_absent_track(self, track: PersonTrack, t: float) -> list[DetectionEvent]:
        events: list[DetectionEvent] = []
        track.present = False
        if (track.confirmed and t - track.last_seen_t >= self.thresholds.track_lost_after_s
                and not track.lost_reported):
            track.lost_reported = True
            events.append(DetectionEvent(EventKind.TRACK_LOST, t, (track.track_id,), detail="perte de suivi"))
            for other in self.tracker.tracks:
                if other.present and other.track_id != track.track_id:
                    self.collisions.note_hidden_witness(t, other.track_id, track.track_id)
        events += self.falls.update_absent(track, t)
        return events

    def _posture_event(self, track: PersonTrack, posture: Posture, t: float) -> list[DetectionEvent]:
        if posture is track.reported_posture:
            track.pending_posture = None
            return []
        if posture is not track.pending_posture:
            track.pending_posture = posture
            track.pending_posture_since_t = t
        if t - track.pending_posture_since_t < self.thresholds.posture_log_hold_s:
            return []
        track.reported_posture, track.pending_posture = posture, None
        return [DetectionEvent(
            EventKind.POSTURE_CHANGED, t, (track.track_id,), posture=posture, detail=posture.value,
        )]

    def _attach_causes(self, events: list[DetectionEvent], t: float) -> list[DetectionEvent]:
        completed = []
        for event in events:
            if event.kind is not EventKind.FALL_CONFIRMED:
                completed.append(event)
                continue
            track = next(tr for tr in self.tracker.tracks if tr.track_id == event.track_ids[0])
            start = (track.descent_start_t or t) - self.thresholds.collision_pre_fall_s
            if self.collisions.collision_involving(track.track_id, start, t):
                cause = FallCause.COLLISION
            elif self.collisions.contact_uncertain(track.track_id, start, t):
                cause = FallCause.UNDETERMINED
            else:
                cause = FallCause.INDIVIDUAL
            self.collisions.note_fall(event.track_ids)
            completed.append(DetectionEvent(
                event.kind, event.timestamp_s, event.track_ids, cause=cause, posture=event.posture,
                detail=event.detail, contact_timestamp_s=event.contact_timestamp_s,
            ))
        return completed

    def _label_event(self, event: DetectionEvent) -> DetectionEvent:
        if not event.track_ids:
            return event
        who = " et ".join(self.label_for(track_id) for track_id in event.track_ids)
        return DetectionEvent(
            event.kind, event.timestamp_s, event.track_ids, event.cause, event.posture,
            f"{who} : {event.detail}" if event.detail else who, event.contact_timestamp_s,
        )
