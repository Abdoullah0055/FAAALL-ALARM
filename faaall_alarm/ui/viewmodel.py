"""Textes affichés par l'écran de surveillance, calculés sans dépendance à Tkinter pour rester testables."""

from __future__ import annotations

import math

from ..incident import IncidentView, ReplyRecord
from ..model import POSTURE_DISPLAY_PRIORITY, PersonView, Posture
from ..sms_contract import ReplyState, SmsStatus

WAITING_FOR_REPLY = "En attente de la réponse du proche"
REVIEW_REPLY = "Réponse reçue, à vérifier"
ANALYSING_REPLY = "Réponse reçue, analyse en cours…"
UNKNOWN_DELAY = "Délai non précisé"


def format_countdown(remaining_s: float) -> str:
    seconds = max(0, math.ceil(remaining_s - 1e-9))
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def main_posture(persons: list[PersonView]) -> Posture:
    if not persons:
        return Posture.NO_PERSON
    present = {person.posture for person in persons}
    return next(posture for posture in POSTURE_DISPLAY_PRIORITY if posture in present)


def posture_caption(persons: list[PersonView]) -> str:
    """Détail par personne, affiché sous la posture quand deux personnes sont suivies."""
    if len(persons) < 2:
        return ""
    return "   ".join(f"{person.label} · {person.posture.value}" for person in sorted(persons, key=lambda p: p.label))


def sms_status_text(view: IncidentView) -> str:
    if view.sms_status is None:
        return "SMS : envoi à l'échéance du décompte"
    if view.sms_status in (SmsStatus.TELEGRAM_ACCEPTED, SmsStatus.TELEGRAM_UNKNOWN):
        return view.sms_status.value
    suffix = " · livraison à confirmer" if view.sms_status is SmsStatus.PENDING else ""
    return f"SMS : {view.sms_status.value}{suffix}"


def reply_text(view: IncidentView) -> str:
    reply = view.reply
    if reply.analysing and reply.state is None:
        return ANALYSING_REPLY
    if reply.needs_review:
        return REVIEW_REPLY
    if reply.state is None:
        return WAITING_FOR_REPLY
    return reply_sentence(reply, view.contact_name)


def reply_sentence(reply: ReplyRecord, contact_name: str) -> str:
    who = contact_name or "Le proche"
    received = f" (reçue à {reply.received_at:%H:%M})" if reply.received_at else ""
    status = {
        ReplyState.EN_ROUTE: "En route",
        ReplyState.CONFIRMED: "Prise en charge confirmée",
        ReplyState.UNAVAILABLE: "Indisponible",
    }.get(reply.state, REVIEW_REPLY)
    if reply.state is ReplyState.UNAVAILABLE or reply.delay_minutes is None:
        delay = "" if reply.state is ReplyState.UNAVAILABLE else f" {UNKNOWN_DELAY}."
        return f"{who} a répondu. {status}.{delay}{received}"
    return f"{who} a répondu. {status}. Arrivée estimée dans {reply.delay_minutes} min.{received}"


def alert_cause_line(view: IncidentView) -> str:
    return view.cause.value


def voice_line(view: IncidentView) -> str:
    return "Voix indisponible" if view.voice_ok is False else ""
