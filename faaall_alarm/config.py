"""Seuils de détection nommés et réglages de l'application.

Les seuils de détection sont regroupés dans `DetectionThresholds` (valeurs par défaut et unités
indiquées). Les réglages modifiables depuis la page Réglages sont dans `AppConfig`. Les fonctions
`read_config`, `validate_config` et `apply_config` forment le contrat partagé avec la page Réglages
(voir docs/RACCORDEMENT.md).

Priorité de lecture : `config.local.json` > variables d'environnement (.env) > valeurs par défaut.
Les secrets (jeton Twilio, clé OpenAI) restent dans l'environnement : ils ne sont jamais
écrits dans `config.local.json`.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field, fields, replace
from pathlib import Path
from typing import Any, Callable, Mapping

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config.local.json"
DEFAULT_DOTENV_PATH = PROJECT_ROOT / ".env"
DEFAULT_MODEL_PATH = PROJECT_ROOT / "models" / "pose_landmarker_lite.task"
DEFAULT_HISTORY_PATH = PROJECT_ROOT / "data" / "history.jsonl"

SMS_MODE_SIMULATED = "simulated"
SMS_MODE_LIVE = "live"
TELEGRAM_MODE = "telegram"
SMS_MODES = (SMS_MODE_SIMULATED, SMS_MODE_LIVE, TELEGRAM_MODE)

DEFAULT_VOICE_MESSAGE = (
    "Une chute possible a été détectée. Si tout va bien, appuyez sur Annuler l'alerte."
)


@dataclass(frozen=True)
class DetectionThresholds:
    """Seuils du moteur de détection. Les longueurs sont exprimées en « longueurs de torse » de
    référence (distance épaules-hanches de la personne debout) pour rester valables quelle que soit
    la distance à la caméra. À calibrer sur la webcam cible."""

    # Visibilité (0 à 1).
    min_core_visibility: float = 0.5  # moyenne épaules + hanches pour exploiter une pose
    min_landmark_visibility: float = 0.5  # point retenu dans la boîte englobante

    # Classement de la posture.
    lying_torso_angle_deg: float = 60.0  # inclinaison du torse par rapport à la verticale
    lying_bbox_aspect: float = 1.3  # largeur / hauteur de la boîte englobante
    lying_height_ratio: float = 0.55  # hauteur apparente / référence d'un corps couché
    sitting_thigh_vertical_ratio: float = 0.4  # (genou - hanche) verticale / torse
    walking_speed_torso_per_s: float = 0.35  # vitesse horizontale des hanches
    falling_descent_speed_torso_per_s: float = 2.0  # vitesse de descente des hanches
    falling_label_hold_s: float = 1.0  # durée d'affichage de FALLING après la descente

    # Chute.
    reference_window_s: float = 30.0  # fenêtre de la hauteur de référence debout
    descent_window_s: float = 5.0  # fenêtre de mesure de la descente (inclut les affaissements lents)
    min_hip_drop_torso: float = 0.6  # descente minimale des hanches dans la fenêtre
    low_torso_angle_deg: float = 55.0  # inclinaison d'une posture d'arrivée basse
    low_height_ratio: float = 0.45  # hauteur apparente / hauteur de référence
    confirm_hold_s: float = 1.5  # maintien de la posture basse avant confirmation
    settle_speed_torso_per_s: float = 0.5  # mouvement résiduel toléré pendant le maintien
    suspicion_pose_loss_s: float = 1.0  # perte de pose qui annule une suspicion
    suspicion_timeout_s: float = 6.0  # durée maximale d'une suspicion sans confirmation
    posture_log_hold_s: float = 0.4  # stabilité d'une posture avant de l'inscrire au journal
    recovery_torso_angle_deg: float = 35.0  # torse « redressé » pour la reprise
    recovery_height_ratio: float = 0.7  # hauteur apparente de reprise
    recovery_hold_s: float = 2.0  # maintien de la reprise avant d'annuler ou de clore
    rearm_hold_s: float = 1.0  # posture de départ exploitable avant de pouvoir détecter

    # Suivi de deux personnes au plus.
    max_people: int = 2
    track_gate_torso: float = 3.0  # distance maximale d'appariement entre deux images
    track_lost_after_s: float = 0.6  # absence avant « perte de suivi »
    track_memory_s: float = 4.0  # conservation d'une trace perdue
    confirmed_track_memory_s: float = 120.0  # idem pour une personne tombée

    # Collision entre deux personnes.
    contact_distance_torso: float = 0.9  # centres de corps assez proches pour un contact
    approach_window_s: float = 0.8  # fenêtre de mesure de la convergence
    closing_speed_torso_per_s: float = 0.8  # vitesse de rapprochement d'un contact net
    post_contact_window_s: float = 1.0  # fenêtre de recherche d'un changement de mouvement
    speed_change_ratio: float = 0.5  # baisse de vitesse relative évoquant un choc
    direction_change_deg: float = 60.0  # changement de direction évoquant un choc
    min_contact_samples: int = 2  # images concordantes minimales
    overlap_distance_torso: float = 1.2  # silhouettes confondues : contact incertain
    collision_resolve_s: float = 5.0  # attente avant de classer une collision « sans chute »
    collision_pre_fall_s: float = 2.5  # un contact antérieur à la chute compte jusqu'à ce délai
    merge_window_s: float = 6.0  # deux chutes proches appartiennent au même incident


@dataclass(frozen=True)
class AppConfig:
    """Réglages persistants de l'application (non secrets)."""

    camera_index: int = 0
    countdown_seconds: float = 10.0
    show_skeleton: bool = False

    monitored_first_name: str = ""
    alert_address: str = ""
    contact_name: str = ""
    contact_number: str = ""  # E.164, proche
    twilio_number: str = ""  # E.164, numéro Twilio

    sms_mode: str = SMS_MODE_SIMULATED
    sms_live_confirmed: bool = False  # activation explicite du mode réel
    reply_poll_interval_s: float = 5.0

    voice_enabled: bool = True
    voice_message: str = DEFAULT_VOICE_MESSAGE
    voice_speed_wpm: int = 150
    voice_volume: int = 100  # amplitude eSpeak NG, en %
    espeak_path: str = ""  # vide : recherche automatique

    monitored_zone: list[float] = field(default_factory=list)  # [x0, y0, x1, y1] normalisés, vide = tout
    lying_zones: list[list[float]] = field(default_factory=list)  # lit, canapé : [x0, y0, x1, y1]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ConfigIssue:
    """Erreur de validation affichable à côté du champ concerné."""

    field: str
    message: str


@dataclass(frozen=True)
class ApplyResult:
    ok: bool
    issues: tuple[ConfigIssue, ...] = ()
    config: AppConfig | None = None


_E164 = re.compile(r"^\+[1-9]\d{7,14}$")
_FIELD_NAMES = {f.name for f in fields(AppConfig)}


def load_dotenv(path: Path = DEFAULT_DOTENV_PATH) -> None:
    """Charge un fichier .env minimal sans écraser les variables déjà définies."""
    if not path.is_file():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _config_from_environment() -> dict[str, Any]:
    mapping = {
        "FAAALL_SMS_MODE": "sms_mode",
        "FAAALL_MONITORED_FIRST_NAME": "monitored_first_name",
        "FAAALL_ALERT_ADDRESS": "alert_address",
        "FAAALL_CONTACT_NAME": "contact_name",
        "FAAALL_SMS_TO_NUMBER": "contact_number",
        "TWILIO_FROM_NUMBER": "twilio_number",
    }
    return {name: os.environ[env] for env, name in mapping.items() if os.environ.get(env)}


def read_config(path: Path | None = None, *, use_environment: bool = True) -> AppConfig:
    """Lit la configuration courante. Ne lève pas d'exception : une valeur illisible est ignorée."""
    values: dict[str, Any] = {}
    if use_environment:
        load_dotenv()
        values.update(_config_from_environment())
    config_path = path or DEFAULT_CONFIG_PATH
    if config_path.is_file():
        try:
            stored = json.loads(config_path.read_text(encoding="utf-8"))
            if isinstance(stored, dict):
                values.update(stored)
        except (OSError, json.JSONDecodeError):
            pass
    known = {name: value for name, value in values.items() if name in _FIELD_NAMES}
    candidate = replace(AppConfig(), **known)
    return candidate if not validate_config(candidate.to_dict()) else _safe_subset(known)


def _safe_subset(values: Mapping[str, Any]) -> AppConfig:
    """Garde champ par champ les valeurs valides lorsque la configuration complète est invalide."""
    config = AppConfig()
    for name, value in values.items():
        trial = replace(config, **{name: value})
        if not any(issue.field == name for issue in validate_config(trial.to_dict(), strict_live=False)):
            config = trial
    return config


def _is_rectangle(value: Any) -> bool:
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return False
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0.0 <= v <= 1.0 for v in value):
        return False
    return value[0] < value[2] and value[1] < value[3]


def validate_config(values: Mapping[str, Any], *, strict_live: bool = True) -> list[ConfigIssue]:
    """Valide un dictionnaire de réglages. Retourne la liste des erreurs, vide si tout est valide."""
    issues: list[ConfigIssue] = []
    merged = {**AppConfig().to_dict(), **{k: v for k, v in values.items() if k in _FIELD_NAMES}}

    def report(name: str, message: str) -> None:
        issues.append(ConfigIssue(name, message))

    def number(name: str, low: float, high: float, label: str, unit: str) -> None:
        value = merged[name]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not low <= value <= high:
            report(name, f"{label} : entrer une valeur entre {low:g} et {high:g} {unit}.".strip())

    camera_index = merged["camera_index"]
    if isinstance(camera_index, bool) or not isinstance(camera_index, int) or camera_index < 0:
        report("camera_index", "Caméra : entrer un numéro de caméra entier (0 pour la première).")
    number("countdown_seconds", 1, 300, "Décompte", "secondes")
    number("voice_volume", 0, 100, "Volume de la voix", "%")
    number("reply_poll_interval_s", 2, 60, "Lecture des réponses", "secondes")
    number("voice_speed_wpm", 80, 300, "Vitesse de la voix", "mots par minute")

    for name, label, limit in (
        ("monitored_first_name", "Prénom", 40),
        ("contact_name", "Nom du proche", 60),
        ("alert_address", "Adresse d'intervention", 120),
        ("voice_message", "Message vocal", 200),
    ):
        if not isinstance(merged[name], str) or len(merged[name]) > limit:
            report(name, f"{label} : texte de {limit} caractères au plus.")
    if not str(merged["voice_message"]).strip():
        report("voice_message", "Message vocal : le message ne peut pas être vide.")

    for name, label in (("contact_number", "Numéro du proche"), ("twilio_number", "Numéro Twilio")):
        value = merged[name]
        if value and (not isinstance(value, str) or not _E164.match(value)):
            report(name, f"{label} : utiliser le format international, par exemple +15145550123.")

    if merged["sms_mode"] not in SMS_MODES:
        report("sms_mode", "Choisissez Simulé, SMS Twilio ou Telegram.")
    elif strict_live and merged["sms_mode"] != SMS_MODE_SIMULATED:
        if not merged["sms_live_confirmed"]:
            report("sms_live_confirmed", "Le mode réel exige une activation explicite.")
        required = [
            ("monitored_first_name", "Prénom"),
            ("alert_address", "Adresse d'intervention"),
        ]
        if merged["sms_mode"] == SMS_MODE_LIVE:
            required.extend((("contact_number", "Numéro du proche"), ("twilio_number", "Numéro Twilio")))
        for name, label in required:
            if not merged[name]:
                report(name, f"{label} : requis en mode réel.")

    zone = merged["monitored_zone"]
    if zone and not _is_rectangle(zone):
        report("monitored_zone", "Zone surveillée : quatre valeurs entre 0 et 1 (x0, y0, x1, y1).")
    lying_zones = merged["lying_zones"]
    if not isinstance(lying_zones, list) or not all(_is_rectangle(z) for z in lying_zones):
        report("lying_zones", "Zones de repos : chaque zone a quatre valeurs entre 0 et 1.")
    return issues


def apply_config(
    values: Mapping[str, Any],
    path: Path | None = None,
    on_applied: Callable[[AppConfig], None] | None = None,
) -> ApplyResult:
    """Valide, enregistre dans `config.local.json` puis notifie l'application en cours.

    `values` peut ne contenir que les champs modifiés. Rien n'est écrit si la validation échoue.
    """
    current = read_config(path)
    candidate_values = {**current.to_dict(), **{k: v for k, v in values.items() if k in _FIELD_NAMES}}
    issues = validate_config(candidate_values)
    if issues:
        return ApplyResult(ok=False, issues=tuple(issues))
    config = AppConfig(**candidate_values)
    target = path or DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(config.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    if on_applied is not None:
        on_applied(config)
    return ApplyResult(ok=True, config=config)
