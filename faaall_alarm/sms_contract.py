"""Contrat entre le moteur d'incident (lot Adam) et le module SMS / interpréteur (lot Abdoullah).

Les signatures et états ci-dessous sont documentés dans docs/RACCORDEMENT.md. Les adaptateurs
réels et simulés d'Abdoullah doivent respecter ces protocoles. `sms_simulated.py` fournit un
substitut utilisable tant que son module n'est pas relié.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Protocol, runtime_checkable

from .model import FallCause


class SmsStatus(str, Enum):
    """Statuts d'un SMS sortant. Seul un retour explicite du fournisseur autorise LIVRE."""

    SIMULATED = "Simulé"
    PENDING = "En attente"
    DELIVERED = "Livré"
    FAILED = "Échec"
    UNKNOWN = "Livraison inconnue"


class SmsAvailability(str, Enum):
    """Disponibilité du canal SMS, affichée au-dessus de la caméra."""

    OPERATIONAL = "SMS opérationnel"  # vérification réussie du service, jamais déduite de la configuration
    UNAVAILABLE = "SMS indisponible"
    SIMULATED = "SMS simulé"
    UNVERIFIED = "SMS non vérifié"


class ReplyState(str, Enum):
    EN_ROUTE = "En route"
    CONFIRMED = "Prise en charge confirmée"
    UNAVAILABLE = "Indisponible"
    TO_CHECK = "À vérifier"


@dataclass(frozen=True)
class AlertRequest:
    """Demande d'alerte émise par le moteur d'incident, au plus une fois par incident et destinataire."""

    incident_id: str
    occurred_at: datetime  # heure civile locale de l'incident (HH:mm dans le message)
    cause: FallCause
    first_name: str | None  # None : attribution incertaine, utiliser la formulation sans prénom
    address: str
    recipient_number: str  # E.164, proche configuré
    sender_number: str  # E.164, numéro Twilio configuré


@dataclass(frozen=True)
class SmsSubmission:
    """Résultat immédiat de la soumission. Une requête acceptée reste EN ATTENTE."""

    status: SmsStatus
    provider_ref: str | None = None  # identifiant fournisseur (SID Twilio), dès qu'il est connu
    error: str | None = None  # message d'erreur, sans numéro ni secret
    submitted_at: datetime | None = None


@dataclass(frozen=True)
class IncomingSms:
    message_id: str  # identifiant unique du message entrant (MessageSid)
    sender: str
    recipient: str
    body: str
    received_at: datetime  # heure réelle du message, pas l'heure de lecture


@dataclass(frozen=True)
class ReplyInterpretation:
    state: ReplyState
    announced_delay_minutes: int | None = None  # uniquement si le texte annonce explicitement un délai


class InterpretationUnavailable(Exception):
    """L'interpréteur ne peut pas répondre (service coupé, sortie invalide). L'écran affiche « à vérifier »."""


@runtime_checkable
class SmsGateway(Protocol):
    """Adaptateur SMS. Les appels sont bloquants et exécutés hors du fil de l'interface."""

    def check_availability(self) -> SmsAvailability:
        ...

    def submit_alert(self, request: AlertRequest) -> SmsSubmission:
        """Enregistre durablement l'intention d'envoi, puis soumet. Ne lève pas pour une erreur réseau :
        retourne `FAILED` (refus) ou `UNKNOWN` (résultat incertain, pas de renvoi automatique)."""

    def get_status(self, provider_ref: str) -> SmsStatus:
        ...

    def fetch_replies(self, since: datetime) -> list[IncomingSms]:
        """Messages entrants du contact configuré depuis `since`, dédupliqués par `message_id`."""


@runtime_checkable
class ReplyInterpreter(Protocol):
    def interpret(self, text: str) -> ReplyInterpretation:
        """Reçoit uniquement le texte du message. Lève `InterpretationUnavailable` en cas d'échec."""
