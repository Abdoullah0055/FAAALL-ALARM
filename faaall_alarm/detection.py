"""Détection des chutes et des collisions à partir d'observations de pose horodatées."""

from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

from .config import DetectionThresholds
from .features import BodyFeatures, compute_features
from .model import (
    DetectionEvent, EventKind, FallCause, FrameAnalysis, FrameObservation, PersonView, Posture,
)
from .posture import classify_posture, is_low_arrival, is_upright
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
        if t - track.pose_lost_since_t >= self._th.suspicion_pose_loss_s:
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
        if any(_point_in_zone(features.hip_center_px, zone, size) for zone in self.lying_zones):
            track.fall_state = FallState.DISARMED  # s'allonger sur le lit ou le canapé est normal
            return [DetectionEvent(
                EventKind.RESTING_ZONE, t, (track.track_id,), posture=Posture.LYING,
                detail="Posture allongée dans une zone de repos, aucune alerte",
            )]
        track.fall_state = FallState.SUSPECTED
        track.suspicion_started_t = t
        track.descent_start_t = descent_start_t
        track.contact_t = t
        track.low_since_t = t
        return [DetectionEvent(
            EventKind.FALL_SUSPECTED, t, (track.track_id,), posture=Posture.FALLING,
            detail=f"descente des hanches de {drop_torso:.1f} torse, posture basse",
            contact_timestamp_s=t,
        )]

    def _when_suspected(self, track, t, features, posture, upright, size):
        if upright:
            return [self._clear_suspicion(track, t, "geste terminé sans chute", FallState.ARMED)]
        if t - (track.suspicion_started_t or t) > self._th.suspicion_timeout_s:
            return [self._clear_suspicion(track, t, "posture basse non maintenue", FallState.DISARMED)]
        if not is_low_arrival(features, track, self._th):
            track.low_since_t = None
            return []
        if track.low_since_t is None:
            track.low_since_t = t
        shift_x, shift_y, duration = track.hip_displacement_torso(0.5, t)
        speed = math.hypot(shift_x, shift_y) / duration if duration > 0 else 0.0
        if t - track.low_since_t < self._th.confirm_hold_s or speed > self._th.settle_speed_torso_per_s:
            return []
        track.fall_state = FallState.CONFIRMED
        track.recovery_since_t = None
        return [DetectionEvent(
            EventKind.FALL_CONFIRMED, t, (track.track_id,), posture=Posture.LYING,
            detail="posture basse maintenue", contact_timestamp_s=track.contact_t,
        )]

    def _when_confirmed(self, track, t, features, posture, upright, size):
        if not upright:
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
    def _hip_drop(self, track: PersonTrack, t: float, features: BodyFeatures) -> tuple[float, float]:
        """Descente maximale des hanches dans la fenêtre, en longueurs de torse, et début de la descente."""
        window = [s for s in track.samples if s.features is not None and t - s.t <= self._th.descent_window_s]
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
    ) -> None:
        self.thresholds = thresholds or DetectionThresholds()
        self.tracker = PersonTracker(self.thresholds)
        self.falls = FallDetector(self.thresholds, lying_zones)
        self.collisions = CollisionDetector(self.thresholds)
        self.monitored_zone = monitored_zone

    def configure_zones(self, monitored_zone: Rectangle | None, lying_zones: list[Rectangle]) -> None:
        self.monitored_zone = monitored_zone
        self.falls.lying_zones = lying_zones

    def label_for(self, track_id: int) -> str:
        track_ids = [track.track_id for track in self.tracker.tracks]
        if len(track_ids) < 2 or track_id not in track_ids:
            return "Personne"
        return f"Personne {track_ids.index(track_id) + 1}"

    def process(self, observation: FrameObservation) -> FrameAnalysis:
        t, size = observation.timestamp_s, observation.frame_size_px
        analysis = FrameAnalysis(timestamp_s=t)
        detections = self._detections(observation)
        pairs = self.tracker.associate(t, detections)
        matched_ids = {track.track_id for track, _ in pairs}

        for track, detection in pairs:
            analysis.events += self._update_matched_track(track, detection, t, size)
            analysis.persons.append(PersonView(
                track.track_id, "", track.last_posture or Posture.INDETERMINATE, detection.pose.landmarks,
            ))
        for track in self.tracker.tracks:
            if track.track_id not in matched_ids:
                analysis.events += self._update_absent_track(track, t)

        present = [track for track in self.tracker.tracks if track.present]
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
        return analysis

    # -- interne ----------------------------------------------------------------------------------
    def _detections(self, observation: FrameObservation) -> list[Detection]:
        size = observation.frame_size_px
        detections = []
        for pose in observation.poses:
            detection = make_detection(pose, compute_features(pose, size, self.thresholds), size)
            if self.monitored_zone is None or _point_in_zone(detection.center_px, self.monitored_zone, size):
                detections.append(detection)
        return detections

    def _update_matched_track(self, track, detection, t, size) -> list[DetectionEvent]:
        events: list[DetectionEvent] = []
        if track.lost_reported:
            track.lost_reported = False
            events.append(DetectionEvent(EventKind.TRACK_RESUMED, t, (track.track_id,), detail="suivi repris"))
        track.seed_reference(detection.features)
        posture = classify_posture(detection.features, track, t, self.thresholds)
        track.record(t, detection.features, posture, detection.center_px)
        track.last_posture = posture
        events += self._posture_event(track, posture, t)
        events += self.falls.update_present(track, t, detection.features, posture, size)
        # Un état posture FALLING reflète la suspicion posée pendant cette image.
        if track.fall_state is FallState.SUSPECTED:
            track.last_posture = Posture.FALLING
        return events

    def _update_absent_track(self, track: PersonTrack, t: float) -> list[DetectionEvent]:
        events: list[DetectionEvent] = []
        track.present = False
        if t - track.last_seen_t >= self.thresholds.track_lost_after_s and not track.lost_reported:
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
