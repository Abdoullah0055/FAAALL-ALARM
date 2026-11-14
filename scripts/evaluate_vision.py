"""Banc d'évaluation de la vision : postures, chutes, suivi et performances, sur MP4 ou poses simulées.

Le banc sépare l'inférence (coûteuse, indépendante du code de détection) du rejeu (rapide) :

1. `poses` passe chaque MP4 dans MediaPipe Pose une seule fois et met les poses en cache
   (`artifacts/eval/poses/`, ignoré par Git). La vue RGB des prévisualisations UR Fall, qui
   juxtaposent profondeur à gauche et RGB à droite, est isolée avec `--crop right`.
2. `sim` exporte les scénarios simulés du dépôt dans le même format, avec la posture attendue
   image par image.
3. `replay` rejoue les poses en cache dans le détecteur et le moteur d'incident d'un dépôt donné
   (`--code-root`, par défaut ce dépôt) : la même entrée sert à comparer deux versions du code.
4. `perf` mesure le coût de chaque étape (lecture, inférence, suivi et analyse, affichage) en
   conditions de lecture réelles.
5. `report` compare plusieurs résultats de `replay` dans un tableau.

Aucune image n'est écrite : seules les poses (33 points normalisés) et les mesures le sont.
Les annotations viennent des CSV UR Fall (étiquette par image : -1 hors sol, 0 transition, 1 au sol)
et, si présent, de `datasets/urfall/annotations-postures.csv` (segments de posture relevés à la main).
"""

from __future__ import annotations

import argparse
import csv
import importlib
import json
import math
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_DIR = REPO_ROOT / "artifacts" / "eval"
POSE_DIR = EVAL_DIR / "poses"
RESULT_DIR = EVAL_DIR / "results"
DATASET_DIR = REPO_ROOT / "datasets" / "urfall"
MODEL_PATH = REPO_ROOT / "models" / "pose_landmarker_lite.task"

# Jeu final réservé avant tout réglage (2026-11-14), par séquence d'origine : UR Fall ne donne pas
# l'identité des personnes, l'indépendance entre personnes reste donc inconnue. Un clip sur trois,
# réparti sur toute la numérotation pour couvrir chutes debout, depuis une chaise et activités.
HOLDOUT = {f"fall-{i:02d}" for i in range(3, 31, 3)} | {f"adl-{i:02d}" for i in range(3, 41, 3)}

# Zones de repos de chaque pièce (vue RGB normalisée), relevées sur les images avant tout réglage.
_ROOM_A_BED = (0.60, 0.40, 1.00, 0.85)
_ROOM_B_SOFA = (0.30, 0.48, 0.76, 0.77)
_ROOM_D_BED = (0.50, 0.45, 1.00, 0.85)
SCENE_ZONES = (
    {f"adl-{i:02d}": [_ROOM_A_BED] for i in range(1, 12)}
    | {f"adl-{i:02d}": [_ROOM_B_SOFA] for i in range(12, 24)}
    | {f"adl-{i:02d}": [_ROOM_D_BED] for i in (30, 36, 37, 38, 39, 40)}
)
# Activités : gestes ordinaires, coucher dans une zone de repos, mise au sol volontaire (à genoux,
# à quatre pattes ou allongé par terre). La dernière catégorie ressemble à un affaissement lent :
# ses alertes sont comptées à part.
ADL_FLOOR = {f"adl-{i:02d}" for i in (30, 31, 32, 33, 34, 35, 38, 39, 40)}
ADL_ZONE = {f"adl-{i:02d}" for i in (10, 11, 21, 22, 23, 36, 37)}

UPRIGHT = {"STANDING", "WALKING", "SITTING"}
LOW = {"LYING", "FALLING"}
INDETERMINATE = "Posture indéterminée"
NO_PERSON = "Aucune personne détectée"


# -- chargement d'une version du code ------------------------------------------------------------------
def load_package(code_root: Path):
    """Importe `faaall_alarm` depuis `code_root` (ex. un worktree de la version d'origine)."""
    sys.path.insert(0, str(code_root.resolve()))
    for name in [n for n in sys.modules if n == "faaall_alarm" or n.startswith("faaall_alarm.")]:
        del sys.modules[name]
    package = importlib.import_module("faaall_alarm")
    loaded_from = Path(package.__file__).resolve().parent.parent
    if loaded_from != code_root.resolve():
        raise RuntimeError(f"faaall_alarm chargé depuis {loaded_from}, attendu {code_root}")
    return package


# -- cache de poses -------------------------------------------------------------------------------------
def save_poses(path: Path, times: list[float], frames: list[list[np.ndarray]], frame_size: tuple[int, int],
               meta: dict, truth: list[str] | None = None) -> None:
    max_people = max((len(f) for f in frames), default=0) or 1
    landmarks = np.zeros((len(frames), max_people, 33, 3), dtype=np.float32)
    counts = np.zeros(len(frames), dtype=np.int8)
    for index, poses in enumerate(frames):
        counts[index] = len(poses)
        for slot, pose in enumerate(poses):
            landmarks[index, slot] = pose
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(
        path, times=np.asarray(times, dtype=np.float64), landmarks=landmarks, counts=counts,
        frame_size=np.asarray(frame_size), meta=json.dumps(meta),
        truth=np.asarray(truth if truth is not None else [], dtype=object),
    )


@dataclass
class PoseSequence:
    name: str
    times: np.ndarray
    landmarks: np.ndarray
    counts: np.ndarray
    frame_size: tuple[int, int]
    meta: dict
    truth: list[str]

    @classmethod
    def load(cls, path: Path) -> "PoseSequence":
        data = np.load(path, allow_pickle=True)
        return cls(path.stem, data["times"], data["landmarks"], data["counts"],
                   tuple(int(v) for v in data["frame_size"]), json.loads(str(data["meta"])),
                   [str(v) for v in data["truth"]])

    def poses_at(self, index: int) -> list[np.ndarray]:
        return [self.landmarks[index, slot].astype(float) for slot in range(int(self.counts[index]))]


def make_estimator(pose_module, min_confidence: float | None):
    """Estimateur du dépôt ; seuil de confiance passé seulement s'il est demandé (version d'origine)."""
    if min_confidence is None:
        return pose_module.MediaPipePoseEstimator(MODEL_PATH)
    return pose_module.MediaPipePoseEstimator(MODEL_PATH, min_confidence=min_confidence)


def command_poses(args: argparse.Namespace) -> None:
    package = load_package(REPO_ROOT)
    pose_module = importlib.import_module("faaall_alarm.pose")
    import cv2

    videos = sorted(Path(args.dataset).glob(args.pattern))
    for video in videos:
        name = video.stem.replace("-cam0", "")
        out = Path(args.out_dir) / f"{name}.npz"
        if out.exists() and not args.force:
            continue
        estimator = make_estimator(pose_module, args.min_confidence)
        capture = cv2.VideoCapture(str(video))
        fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
        times, frames, read_ms, infer_ms = [], [], [], []
        frame_size = (0, 0)
        index = 0
        while True:
            started = time.perf_counter()
            ok, image = capture.read()
            if not ok:
                break
            if args.crop == "right":
                image = image[:, image.shape[1] // 2:]
            read_ms.append((time.perf_counter() - started) * 1000)
            frame_size = (image.shape[1], image.shape[0])
            t = index / fps  # temps du fichier, image par image (CSV UR Fall indexés à partir de 1)
            started = time.perf_counter()
            poses = estimator.estimate(image, t)
            infer_ms.append((time.perf_counter() - started) * 1000)
            times.append(t)
            frames.append([p.landmarks for p in poses])
            index += 1
        capture.release()
        estimator.close()
        meta = {"source": str(video.name), "fps": fps, "crop": args.crop, "model": MODEL_PATH.name,
                "min_confidence": args.min_confidence,
                "read_ms_median": statistics.median(read_ms), "infer_ms_median": statistics.median(infer_ms)}
        save_poses(out, times, frames, frame_size, meta)
        print(f"{name}: {len(frames)} images, inférence médiane {meta['infer_ms_median']:.1f} ms")
    del package


# -- export des scénarios simulés ---------------------------------------------------------------------------
def hip_center(landmarks: np.ndarray) -> tuple[float, float]:
    return float(landmarks[[23, 24], 0].mean()), float(landmarks[[23, 24], 1].mean())


def expected_posture(state, pose) -> str:
    """Posture attendue d'un état de mannequin ; « transition » quand aucune étiquette n'est sûre.

    Jambes masquées ou hors cadre : debout et assis ne se distinguent plus, « UPRIGHT? » accepte
    une posture redressée ou « Posture indéterminée », jamais une posture au sol.
    """
    if state is None:
        return "hidden"
    if state.hip_height_t <= 0.4 or (state.torso_deg >= 60 and state.hip_height_t <= 0.6):
        return "LYING"  # hanches au sol : allongé, même torse raccourci vers la caméra
    legs_visible = float(np.mean(pose.landmarks[[25, 26], 2])) >= 0.5
    upright = state.torso_deg < 30 and (state.hip_height_t >= 1.6 or state.thigh_deg >= 60)
    if not legs_visible:
        return "UPRIGHT?" if upright else "transition"
    if state.thigh_deg >= 60 and state.torso_deg < 45:
        return "SITTING"
    if state.hip_height_t >= 1.6 and state.torso_deg < 30 and state.thigh_deg < 25:
        return "WALKING" if state.gait_deg >= 10 else "STANDING"
    return "transition"


def command_sim(args: argparse.Namespace) -> None:
    load_package(REPO_ROOT)
    scenarios = importlib.import_module("faaall_alarm.simulation.scenarios")
    names = args.names or list(scenarios.SCENARIOS)
    for name in names:
        scenario = scenarios.get_scenario(name)
        times, frames, truth, centers = [], [], [], []
        for t in scenario.frame_times():
            t = float(t)
            if not scenario.camera_available(t):
                continue
            times.append(t)
            poses = scenario.poses_at(t)
            frames.append([p.landmarks for p in poses])
            states = [person.state_at(t) for person in scenario.persons]
            visible = iter(poses)
            labels, frame_centers = [], []
            for state in states:
                pose = next(visible) if state is not None else None
                labels.append(expected_posture(state, pose))
                frame_centers.append(hip_center(pose.landmarks) if pose is not None else (math.nan, math.nan))
            truth.append("|".join(labels))
            centers.append(frame_centers)
        meta = {"source": "simulation", "fps": scenario.fps, "lying_zones": scenario.lying_zones,
                "expected": scenario.expected, "fall_contact_s": getattr(scenario, "fall_contact_s", None),
                "expected_falls": getattr(scenario, "expected_falls", None),
                "expected_recoveries": getattr(scenario, "expected_recoveries", None)}
        meta["truth_centers"] = centers  # centre des hanches de chaque personne, pour compter les inversions
        save_poses(POSE_DIR / f"sim-{name}.npz", times, frames, scenario.frame_size_px, meta, truth)
    print(f"{len(names)} scénarios exportés")


# -- rejeu -----------------------------------------------------------------------------------------------------
@dataclass
class RecordingPorts:
    spoken_t: list[float] = field(default_factory=list)
    submitted: int = 0
    clock: object = None

    def speak(self, incident_id: str, text: str) -> None:
        self.spoken_t.append(self.clock.monotonic())

    def submit_sms(self, request) -> None:
        self.submitted += 1

    def interpret_reply(self, incident_id: str, message_id: str, text: str) -> None:
        return None

    def log(self, message: str) -> None:
        return None

    def record(self, event: str, **fields: object) -> None:
        return None


def load_labels() -> dict[str, dict[int, int]]:
    labels: dict[str, dict[int, int]] = defaultdict(dict)
    for csv_name in ("urfall-cam0-falls.csv", "urfall-cam0-adls.csv"):
        path = DATASET_DIR / csv_name
        if path.exists():
            for row in csv.reader(path.open(encoding="utf-8")):
                labels[row[0]][int(row[1])] = int(row[2])
    return labels


def load_posture_segments() -> dict[str, list[tuple[float, float, str]]]:
    """Segments relevés à la main : clip,début_s,fin_s,posture (STANDING, WALKING, SITTING, LYING)."""
    segments: dict[str, list[tuple[float, float, str]]] = defaultdict(list)
    path = DATASET_DIR / "annotations-postures.csv"
    if path.exists():
        for row in csv.reader(path.open(encoding="utf-8")):
            if row and not row[0].startswith("#") and row[0] != "clip":
                segments[row[0]].append((float(row[1]), float(row[2]), row[3].strip()))
    return segments


def replay_sequence(package_modules, sequence: PoseSequence, pad_s: float, thresholds_overrides: dict,
                    preroll_s: float = 0.0) -> dict:
    detection, model, clock_module, incident, runner, config = package_modules
    thresholds = config.DetectionThresholds(**thresholds_overrides)
    zones = [tuple(z) for z in sequence.meta.get("lying_zones") or SCENE_ZONES.get(sequence.name, [])]
    pipeline = detection.DetectionPipeline(thresholds, lying_zones=zones)
    clock = clock_module.ManualClock()
    ports = RecordingPorts(clock=clock)
    settings = incident.incident_settings_from(runner.demo_config())
    engine = incident.IncidentEngine(clock, ports, lambda: settings, merge_window_s=thresholds.merge_window_s)

    fps = float(sequence.meta.get("fps") or 30.0)
    indices = list(range(len(sequence.times)))
    times = [float(t) for t in sequence.times]
    # Les clips sont des extraits : la personne était déjà là avant la première image et reste au sol
    # après la dernière. Le pré-roulement répète la première pose, le prolongement la dernière ; ces
    # images sont exclues des mesures de posture.
    preroll = int(round(preroll_s * fps)) if indices else 0
    indices = [0] * preroll + indices
    times = [times[0] - (preroll - k) / fps for k in range(preroll)] + times if indices else times
    padded_from = len(indices)
    if pad_s > 0 and indices:
        last_t = times[-1]
        for k in range(1, int(round(pad_s * fps)) + 1):
            indices.append(indices[padded_from - 1])
            times.append(last_t + k / fps)

    frames, events, track_ids_seen = [], [], set()
    process_ms = []
    truth_centers = sequence.meta.get("truth_centers")
    identity: dict[int, int] = {}
    identity_switches = 0
    errors: dict[str, int] = {}
    for frame_number, (index, t) in enumerate(zip(indices, times)):
        poses = tuple(model.PersonPose(p) for p in sequence.poses_at(index))
        clock.advance_to(t)
        started = time.perf_counter()
        try:
            analysis = pipeline.process(model.FrameObservation(t, sequence.frame_size, poses))
        except Exception as error:  # noqa: BLE001 - comme l'application : l'image est perdue, l'analyse continue
            errors[type(error).__name__] = errors.get(type(error).__name__, 0) + 1
            engine.tick()
            frames.append({"t": t, "padded": frame_number >= padded_from or frame_number < preroll,
                           "poses": len(poses), "persons": [],
                           "display": "Erreur d'analyse"})
            continue
        engine.note_person_count(len(analysis.persons))
        for event in analysis.events:
            engine.handle_event(event)
        engine.tick()
        process_ms.append((time.perf_counter() - started) * 1000)
        persons = sorted(analysis.persons, key=lambda p: p.track_id)
        track_ids_seen |= {p.track_id for p in persons}
        centers_now = truth_centers[index] if truth_centers else []
        # Au point exact du croisement, les deux vérités se confondent : aucune identité n'y est jugée.
        separable = (len(centers_now) >= 2 and not any(math.isnan(c[0]) for c in centers_now)
                     and math.dist(centers_now[0], centers_now[1]) >= 0.05) or (
            len(centers_now) >= 2 and any(math.isnan(c[0]) for c in centers_now))
        if truth_centers and preroll <= frame_number < padded_from and separable:
            for person in persons:
                if person.landmarks is None:
                    continue
                center = hip_center(person.landmarks)
                distances = [math.dist(center, c) if not math.isnan(c[0]) else math.inf for c in truth_centers[index]]
                who = int(np.argmin(distances))
                if person.track_id in identity and identity[person.track_id] != who:
                    identity_switches += 1
                identity[person.track_id] = who
        frames.append({
            "t": t, "padded": frame_number >= padded_from or frame_number < preroll, "poses": len(poses),
            "persons": [(p.track_id, p.label, p.posture.value) for p in persons],
            "display": display_posture(model, analysis.persons),
        })
        for event in analysis.events:
            events.append({"t": t, "kind": event.kind.value, "tracks": list(event.track_ids),
                           "cause": event.cause.value if event.cause else None, "detail": event.detail})
    return {
        "name": sequence.name, "frames": frames, "events": events, "voice_t": ports.spoken_t,
        "incidents": len(engine.incidents()), "track_ids": sorted(track_ids_seen),
        "identity_switches": identity_switches, "analysis_errors": errors,
        "clip_end_t": times[padded_from - 1] if padded_from else 0.0,
        "process_ms_median": statistics.median(process_ms) if process_ms else 0.0,
        "process_ms_p95": float(np.percentile(process_ms, 95)) if process_ms else 0.0,
        "meta": sequence.meta, "truth": sequence.truth,
    }


def display_posture(model, persons) -> str:
    if not persons:
        return model.Posture.NO_PERSON.value
    present = {p.posture for p in persons}
    return next(p for p in model.POSTURE_DISPLAY_PRIORITY if p in present).value


def command_replay(args: argparse.Namespace) -> None:
    load_package(Path(args.code_root))
    modules = tuple(importlib.import_module(f"faaall_alarm.{m}") for m in (
        "detection", "model", "clock", "incident", "simulation.runner", "config"))
    overrides = json.loads(args.thresholds) if args.thresholds else {}
    results = []
    for path in sorted(Path(args.pose_dir).glob(args.pattern)):
        sequence = PoseSequence.load(path)
        if not sequence_in_split(sequence.name, args.split):
            continue
        simulated = sequence.name.startswith("sim-")
        results.append(replay_sequence(modules, sequence, 0.0 if simulated else args.pad_s, overrides,
                                       0.0 if simulated else args.preroll_s))
    labels, segments = load_labels(), load_posture_segments()
    summary = summarise(results, labels, segments)
    RESULT_DIR.mkdir(parents=True, exist_ok=True)
    out = RESULT_DIR / f"{args.label}-{args.split}.json"
    out.write_text(json.dumps({"label": args.label, "split": args.split, "pad_s": args.pad_s,
                               "preroll_s": args.preroll_s,
                               "code_root": str(args.code_root), "summary": summary,
                               "sequences": [compact(r) for r in results]}, ensure_ascii=False, indent=1),
                   encoding="utf-8")
    if args.split == "holdout" and not args.show_holdout:
        print(f"Résultat du jeu réservé écrit dans {out} (non affiché).")
        return
    print_summary(summary, args.verbose)


def sequence_in_split(name: str, split: str) -> bool:
    if name.startswith("sim-"):
        return split in ("sim", "all")
    if split == "calib":
        return name not in HOLDOUT
    if split == "holdout":
        return name in HOLDOUT
    return split == "all"


def compact(result: dict) -> dict:
    """Garde la chronologie des postures affichées sous forme de segments, pas image par image."""
    segments = []
    for frame in result["frames"]:
        if segments and segments[-1][2] == frame["display"]:
            segments[-1][1] = frame["t"]
        else:
            segments.append([frame["t"], frame["t"], frame["display"]])
    return {k: v for k, v in result.items() if k not in ("frames", "truth")} | {"display_segments": segments}


# -- métriques ---------------------------------------------------------------------------------------------
def summarise(results: list[dict], labels: dict, segments: dict) -> dict:
    falls, adls, sims = [], [], []
    posture = Counter()
    confusion: dict[str, Counter] = defaultdict(Counter)
    changes = 0
    observed_s = 0.0
    indeterminate_s = 0.0
    no_person_with_pose_s = 0.0
    track_churn = 0
    ghost_frames = 0
    identity_switches = 0
    analysis_errors = Counter()
    errors_by_sequence: dict[str, Counter] = defaultdict(Counter)
    for result in results:
        name = result["name"]
        frames = [f for f in result["frames"] if not f["padded"]]
        if len(frames) < 2:
            continue
        dt = (frames[-1]["t"] - frames[0]["t"]) / (len(frames) - 1)
        observed_s += dt * len(frames)
        previous = None
        for index, frame in enumerate(frames):
            shown = frame["display"]
            if previous is not None and shown != previous:
                changes += 1
            previous = shown
            if shown == INDETERMINATE:
                indeterminate_s += dt
            if shown == NO_PERSON and frame["poses"] > 0:
                no_person_with_pose_s += dt
            truth = truth_for(result, index, frame, labels, segments)
            if truth is None:
                continue
            confusion[truth][shown] += 1
            verdict = "indéterminé" if shown == INDETERMINATE and truth != "UPRIGHT?" else (
                "ok" if posture_ok(truth, shown) else "erreur")
            posture[(truth, verdict)] += 1
            if verdict == "erreur":
                errors_by_sequence[name][f"{truth}→{shown}"] += 1
        expected_people = 2 if name.startswith("sim-") and "|" in (result["truth"][0] if result["truth"] else "") else 1
        track_churn += max(0, len(result["track_ids"]) - expected_people)
        identity_switches += result.get("identity_switches", 0)
        analysis_errors.update(result.get("analysis_errors", {}))
        ghost_frames += sum(1 for f in frames if len(f["persons"]) > expected_people)

        confirmations = [e for e in result["events"] if e["kind"] == "fall_confirmed"]
        first_confirm = confirmations[0]["t"] if confirmations else None
        first_voice = result["voice_t"][0] if result["voice_t"] else None
        recoveries = sum(1 for e in result["events"] if e["kind"] == "person_recovered")
        entry = {"name": name, "confirmations": len(confirmations), "incidents": result["incidents"],
                 "recoveries": recoveries,
                 "confirm_t": first_confirm, "voice_t": first_voice, "clip_end_t": result["clip_end_t"]}
        if name.startswith("fall-"):
            ground = ground_contact_s(labels.get(name, {}))
            entry["ground_t"] = ground
            entry["in_clip"] = first_voice is not None and first_voice <= result["clip_end_t"] + 1e-6
            entry["delay_s"] = (first_voice - ground) if (first_voice is not None and ground is not None) else None
            falls.append(entry)
        elif name.startswith("adl-"):
            adls.append(entry)
        else:
            entry["expected_falls"] = result["meta"].get("expected_falls")
            entry["expected_recoveries"] = result["meta"].get("expected_recoveries")
            contact = result["meta"].get("fall_contact_s")
            entry["delay_s"] = (first_voice - contact) if (first_voice is not None and contact is not None) else None
            sims.append(entry)

    fall_detected = [f for f in falls if f["incidents"] >= 1]
    monitored = [a for a in adls if a["name"] not in ADL_FLOOR]
    floor = [a for a in adls if a["name"] in ADL_FLOOR]
    adl_duration_h = sum(a["clip_end_t"] for a in monitored) / 3600.0
    false_alerts = sum(a["incidents"] for a in monitored)
    delays = [f["delay_s"] for f in falls if f["delay_s"] is not None]
    sim_errors = [s for s in sims if (s["expected_falls"] is not None and s["incidents"] != s["expected_falls"])
                  or (s.get("expected_recoveries") is not None and s["recoveries"] != s["expected_recoveries"])]
    total_truth = sum(posture.values()) or 1
    return {
        "sequences": len(results), "observed_s": round(observed_s, 1),
        "posture_frames": total_truth,
        "posture_accuracy": round(sum(v for (t, verdict), v in posture.items() if verdict == "ok") / total_truth, 3),
        "posture_error_rate": round(sum(v for (t, verdict), v in posture.items() if verdict == "erreur") / total_truth, 3),
        "posture_by_truth": {t: {"ok": posture[(t, "ok")], "erreur": posture[(t, "erreur")],
                                 "indéterminé": posture[(t, "indéterminé")]}
                             for t in sorted({t for t, _ in posture})},
        "confusion": {t: dict(c) for t, c in confusion.items()},
        "posture_errors_by_sequence": {n: dict(c) for n, c in sorted(errors_by_sequence.items())},
        "posture_changes_per_min": round(changes / max(observed_s / 60.0, 1e-9), 1),
        "indeterminate_s": round(indeterminate_s, 1),
        "no_person_with_pose_s": round(no_person_with_pose_s, 1),
        "track_churn": track_churn, "ghost_frames": ghost_frames, "identity_switches": identity_switches,
        "analysis_errors": dict(analysis_errors), "analysis_error_clips": [r["name"] for r in results if r.get("analysis_errors")],
        "falls": {"total": len(falls), "detected": len(fall_detected),
                  "detected_in_clip": sum(1 for f in falls if f.get("in_clip")),
                  "missed": [f["name"] for f in falls if f["incidents"] == 0],
                  "duplicates": sum(max(0, f["incidents"] - 1) for f in falls),
                  "delay_s_median": round(statistics.median(delays), 2) if delays else None,
                  "delay_s_max": round(max(delays), 2) if delays else None,
                  "per_clip": falls},
        "adl": {"total": len(monitored), "observed_min": round(adl_duration_h * 60, 1),
                "false_alerts": false_alerts,
                "false_alerts_per_hour": round(false_alerts / adl_duration_h, 1) if adl_duration_h else None,
                "false_alert_clips": [a["name"] for a in monitored if a["incidents"]],
                "zone_clips": len([a for a in monitored if a["name"] in ADL_ZONE]),
                "zone_alerts": sum(a["incidents"] for a in monitored if a["name"] in ADL_ZONE),
                "floor_clips": len(floor), "floor_alerts": sum(a["incidents"] for a in floor),
                "floor_alert_clips": [a["name"] for a in floor if a["incidents"]]},
        "sim": {"total": len(sims),
                "errors": [(s["name"], f"incidents {s['incidents']}/{s['expected_falls']}",
                            f"reprises {s['recoveries']}/{s.get('expected_recoveries')}") for s in sim_errors],
                "delays": {s["name"]: round(s["delay_s"], 2) for s in sims if s["delay_s"] is not None}},
    }


def ground_contact_s(frame_labels: dict[int, int]) -> float | None:
    """Premier appui au sol annoté : première image étiquetée 1 (« au sol ») dans le CSV UR Fall."""
    lying = sorted(f for f, label in frame_labels.items() if label == 1)
    return (lying[0] - 1) / 30.0 if lying else None


def truth_for(result: dict, index: int, frame: dict, labels: dict, segments: dict) -> str | None:
    name = result["name"]
    if name.startswith("sim-"):
        truth = result["truth"][index] if index < len(result["truth"]) else None
        if not truth or "|" in truth:  # scènes à deux : la posture affichée agrège les deux personnes
            return None
        return None if truth in ("transition", "hidden") else truth
    for start, end, label in segments.get(name, ()):
        if start <= frame["t"] <= end:
            return label
    label = labels.get(name, {}).get(index + 1)
    if label == 1:
        return "LYING"
    if label == -1 and name.startswith("fall-"):
        return "UPRIGHT"  # avant la chute : debout, marche ou assis, jamais au sol
    return None


def posture_ok(truth: str, shown: str) -> bool:
    if truth == "UPRIGHT":
        return shown in UPRIGHT
    if truth == "UPRIGHT?":
        return shown in UPRIGHT or shown == INDETERMINATE
    if truth == "LYING":
        return shown in LOW
    return shown == truth


def print_summary(summary: dict, verbose: bool = False) -> None:
    s = summary
    print(f"Séquences {s['sequences']} · durée observée {s['observed_s']} s")
    print(f"Postures : exactitude {s['posture_accuracy']:.1%}, erreurs franches {s['posture_error_rate']:.1%} "
          f"sur {s['posture_frames']} images annotées · "
          f"{s['posture_changes_per_min']} changements/min · indéterminée {s['indeterminate_s']} s · "
          f"aucune personne malgré une pose {s['no_person_with_pose_s']} s")
    for truth, counts in s["posture_by_truth"].items():
        print(f"  {truth:9s} ok {counts['ok']:5d} erreur {counts['erreur']:5d} indét. {counts['indéterminé']:4d}  "
              f"{s['confusion'].get(truth)}")
    f = s["falls"]
    print(f"Chutes : {f['detected']}/{f['total']} détectées ({f['detected_in_clip']} dans le clip) · "
          f"doublons {f['duplicates']} · délai médian {f['delay_s_median']} s, max {f['delay_s_max']} s · "
          f"manquées {f['missed']}")
    a = s["adl"]
    print(f"Activités : {a['false_alerts']} fausses alertes sur {a['total']} clips, {a['observed_min']} min "
          f"({a['false_alerts_per_hour']}/h) {a['false_alert_clips']} · dont coucher en zone "
          f"{a['zone_alerts']}/{a['zone_clips']} · mise au sol volontaire (à part) "
          f"{a['floor_alerts']} alertes/{a['floor_clips']} clips {a['floor_alert_clips']}")
    print(f"Suivi : identifiants en trop {s['track_churn']} · inversions d'identité {s.get('identity_switches', 0)} · "
          f"images avec personne fantôme {s['ghost_frames']} · images perdues sur erreur {s.get('analysis_errors')} "
          f"{s.get('analysis_error_clips')}")
    if s["sim"]["total"]:
        print(f"Simulation : {s['sim']['total']} scénarios · écarts {s['sim']['errors']} · délais {s['sim']['delays']}")
    if verbose:
        for clip in f["per_clip"]:
            print("   ", clip)
        for name, counts in s["posture_errors_by_sequence"].items():
            print("    erreurs de posture", name, counts)


# -- performances ---------------------------------------------------------------------------------------------
def command_perf(args: argparse.Namespace) -> None:
    load_package(Path(args.code_root))
    import cv2

    capture_module = importlib.import_module("faaall_alarm.capture")
    pose_module = importlib.import_module("faaall_alarm.pose")
    detection = importlib.import_module("faaall_alarm.detection")
    model = importlib.import_module("faaall_alarm.model")
    clock_module = importlib.import_module("faaall_alarm.clock")
    overlay = importlib.import_module("faaall_alarm.ui.overlay")

    rows = []
    for video in args.videos:
        clock = clock_module.SystemClock()
        config = importlib.import_module("faaall_alarm.config")
        confidence = getattr(config.DetectionThresholds(), "pose_min_confidence", None)  # comme l'application
        estimator = make_estimator(pose_module, confidence)
        pipeline = detection.DetectionPipeline()
        stages = defaultdict(list)
        latencies = []
        analysed = 0
        # La source est créée juste avant la boucle : en lecture réelle, l'image d'horodatage ts est
        # disponible à `source_start + ts` ; la latence est le retard du résultat sur cet instant.
        source_start = clock.monotonic()
        source = capture_module.VideoFileSource(Path(video), clock, realtime=args.realtime)
        wall_start = time.perf_counter()
        while True:
            started = time.perf_counter()
            frame = source.read()
            if frame.image_bgr is None:
                break
            image = frame.image_bgr
            if args.crop == "right":
                image = image[:, image.shape[1] // 2:]
            read_done = time.perf_counter()
            if not args.realtime:  # en lecture réelle, ce temps inclut l'attente de l'image suivante
                stages["lecture"].append((read_done - started) * 1000)
            poses = estimator.estimate(image, frame.timestamp_s)
            if args.extra_ms:  # poste plus lent simulé : inférence allongée d'autant
                time.sleep(args.extra_ms / 1000.0)
            inferred = time.perf_counter()
            stages["inférence"].append((inferred - read_done) * 1000)
            analysis = pipeline.process(model.FrameObservation(frame.timestamp_s, (image.shape[1], image.shape[0]), poses))
            analysed_at = time.perf_counter()
            stages["suivi et analyse"].append((analysed_at - inferred) * 1000)
            if args.realtime:
                latencies.append((clock.monotonic() - (source_start + frame.timestamp_s)) * 1000)
            drawn = overlay.draw_skeleton(image, analysis.persons)
            scale = 960 / drawn.shape[1]
            resized = cv2.resize(drawn, (960, int(drawn.shape[0] * scale)))
            cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            stages["affichage (squelette, mise à l'échelle)"].append((time.perf_counter() - analysed_at) * 1000)
            analysed += 1
        elapsed = time.perf_counter() - wall_start
        source.close()
        estimator.close()
        row = {"video": Path(video).name, "images": analysed, "cadence_analysee_ips": round(analysed / elapsed, 1),
               "etapes_ms_mediane": {k: round(statistics.median(v), 2) for k, v in stages.items()},
               "etapes_ms_p95": {k: round(float(np.percentile(v, 95)), 2) for k, v in stages.items()}}
        if latencies:
            row["latence_ms_mediane"] = round(statistics.median(latencies), 1)
            row["latence_ms_p95"] = round(float(np.percentile(latencies, 95)), 1)
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False))
    if args.label:
        RESULT_DIR.mkdir(parents=True, exist_ok=True)
        (RESULT_DIR / f"perf-{args.label}.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")


# -- comparaison -------------------------------------------------------------------------------------------
def command_report(args: argparse.Namespace) -> None:
    loaded = [json.loads(Path(p).read_text(encoding="utf-8")) for p in args.results]
    keys = [
        ("Séquences", lambda s: s["sequences"]),
        ("Durée observée (s)", lambda s: s["observed_s"]),
        ("Exactitude des postures", lambda s: f"{s['posture_accuracy']:.1%}"),
        ("Erreurs franches de posture", lambda s: f"{s['posture_error_rate']:.1%}"),
        ("Changements de posture/min", lambda s: s["posture_changes_per_min"]),
        ("Posture indéterminée (s)", lambda s: s["indeterminate_s"]),
        ("Chutes détectées", lambda s: f"{s['falls']['detected']}/{s['falls']['total']}"),
        ("Chutes détectées dans le clip", lambda s: s["falls"]["detected_in_clip"]),
        ("Doublons", lambda s: s["falls"]["duplicates"]),
        ("Délai médian sol→voix (s)", lambda s: s["falls"]["delay_s_median"]),
        ("Fausses alertes (activités)", lambda s: s["adl"]["false_alerts"]),
        ("Fausses alertes/h", lambda s: s["adl"]["false_alerts_per_hour"]),
        ("Alertes mise au sol volontaire", lambda s: f"{s['adl']['floor_alerts']}/{s['adl']['floor_clips']}"),
        ("Identifiants en trop", lambda s: s["track_churn"]),
        ("Inversions d'identité", lambda s: s.get("identity_switches", "—")),
        ("Images perdues sur erreur d'analyse", lambda s: sum(s.get("analysis_errors", {}).values())),
        ("Écarts simulation", lambda s: len(s["sim"]["errors"])),
    ]
    print("| Mesure | " + " | ".join(r["label"] for r in loaded) + " |")
    print("| --- |" + " --- |" * len(loaded))
    for title, getter in keys:
        values = []
        for r in loaded:
            try:
                values.append(str(getter(r["summary"])))
            except (KeyError, TypeError, ZeroDivisionError):
                values.append("—")
        print(f"| {title} | " + " | ".join(values) + " |")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    poses = sub.add_parser("poses", help="MediaPipe sur les MP4, poses mises en cache")
    poses.add_argument("--dataset", default=str(DATASET_DIR))
    poses.add_argument("--pattern", default="*.mp4")
    poses.add_argument("--crop", choices=("none", "right"), default="right")
    poses.add_argument("--force", action="store_true")
    poses.add_argument("--out-dir", default=str(POSE_DIR))
    poses.add_argument("--min-confidence", type=float, default=None,
                       help="seuils de détection, de présence et de suivi MediaPipe (défaut du dépôt sinon)")
    poses.set_defaults(func=command_poses)

    sim = sub.add_parser("sim", help="exporte les scénarios simulés")
    sim.add_argument("names", nargs="*")
    sim.set_defaults(func=command_sim)

    replay = sub.add_parser("replay", help="rejoue les poses dans le détecteur d'un dépôt")
    replay.add_argument("--code-root", default=str(REPO_ROOT))
    replay.add_argument("--label", required=True)
    replay.add_argument("--split", choices=("calib", "holdout", "sim", "all"), default="calib")
    replay.add_argument("--pattern", default="*.npz")
    replay.add_argument("--pose-dir", default=str(POSE_DIR))
    replay.add_argument("--pad-s", type=float, default=4.0,
                        help="prolonge chaque MP4 par sa dernière pose (personne restée au sol)")
    replay.add_argument("--preroll-s", type=float, default=2.0,
                        help="répète la première pose avant le clip (personne déjà présente)")
    replay.add_argument("--thresholds", default="", help='surcharges JSON, ex. {"confirm_hold_s": 1.0}')
    replay.add_argument("--show-holdout", action="store_true")
    replay.add_argument("--verbose", action="store_true")
    replay.set_defaults(func=command_replay)

    perf = sub.add_parser("perf", help="coût par étape et latence")
    perf.add_argument("videos", nargs="+")
    perf.add_argument("--code-root", default=str(REPO_ROOT))
    perf.add_argument("--crop", choices=("none", "right"), default="none")
    perf.add_argument("--realtime", action="store_true")
    perf.add_argument("--label", default="")
    perf.add_argument("--extra-ms", type=float, default=0.0,
                      help="ajoute ce temps à chaque inférence pour simuler un poste plus lent")
    perf.set_defaults(func=command_perf)

    report = sub.add_parser("report", help="tableau comparatif de résultats de replay")
    report.add_argument("results", nargs="+")
    report.set_defaults(func=command_report)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
