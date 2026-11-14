# Raccordement entre le lot 1 (Adam) et le lot 2 (Abdoullah)

Ce document est le contrat versionné entre le moteur d'incident et l'écran de surveillance (lot 1) et le module SMS, l'interpréteur OpenRouter et la page Réglages (lot 2). Chaque élément suit le format de passage de relais d'[AGENTS.md](../AGENTS.md) : point de branchement, données échangées, démarrage, vérification.

**Responsables de la suite.** Abdoullah relie ses trois modules aux points ci-dessous. Adam vérifie ensuite que l'écran affiche la bonne information pour le bon incident et consigne le résultat dans la PR.

**Ordre d'intégration.**

1. Fusionner la PR du lot 1 (elle ajoute ce contrat, les simulateurs et les tests de contrat).
2. Abdoullah met sa branche à jour, ajoute ses modules aux chemins indiqués, puis exécute les tests de contrat de la section 5 contre ses adaptateurs.
3. Recette simulée sur l'écran (section 5), puis, seulement sur demande explicite et avec le destinataire convenu, un essai réel.

## Ce qui est vérifié et ce qui reste à relier

| Élément | État |
| --- | --- |
| Contrat (`sms_contract.py`), moteur d'incident, rattachement des réponses, affichage | Vérifié par les tests et la démo avec les substituts simulés |
| Frontière d'envoi réel (`integration.py`) | Vérifiée : MP4, démo et tests ne passent jamais par l'adaptateur réel |
| Adaptateur SMS simulé et réel (`faaall_alarm/sms/`) | **À relier par Abdoullah.** Substitut simulé local en attendant |
| Interpréteur OpenRouter (`faaall_alarm/replies/`) | **À relier par Abdoullah.** Règles simples simulées en attendant (ne remplacent pas le modèle) |
| Page Réglages (`faaall_alarm/settings_page.py`) | **À relier par Abdoullah.** Page provisoire (6 champs et « Tester le son ») en attendant |
| Essai réel Twilio / téléphone convenu | Non fait, non demandé |

## 1. Module SMS

**Point de branchement.** Créer `faaall_alarm/sms/__init__.py` qui expose :

```python
def create_gateway(config: AppConfig) -> SmsGateway: ...                      # adaptateur réel (Twilio)
def create_simulated_gateway(config: AppConfig, clock: Clock) -> SmsGateway:  # adaptateur simulé, optionnel
    ...  # doit aussi exposer inject_reply(body, sender, recipient, received_at=None, message_id=None)
```

[`integration.build_sms_gateway`](../faaall_alarm/integration.py) cherche ces deux noms par `importlib`. `create_gateway` n'est appelé que si `sms_mode == "live"`, `sms_live_confirmed` est vrai **et** la source est une webcam (`real_sms_allowed`). Dans tous les autres cas (MP4, démo, tests, mode simulé), `create_simulated_gateway` est utilisé s'il existe, sinon le substitut `SimulatedSmsGateway`. L'adaptateur réel est en plus enveloppé par `SendGuard`, qui refuse toute soumission si le mode réel n'est plus autorisé.

Le protocole `SmsGateway` ([sms_contract.py](../faaall_alarm/sms_contract.py)) :

```python
class SmsGateway(Protocol):
    def check_availability(self) -> SmsAvailability: ...
    def submit_alert(self, request: AlertRequest) -> SmsSubmission: ...
    def get_status(self, provider_ref: str) -> SmsStatus: ...
    def fetch_replies(self, since: datetime) -> list[IncomingSms]: ...
```

**Fils d'exécution.** Les appels sont bloquants et faits depuis un `ThreadPoolExecutor` de 4 fils, jamais depuis le fil de l'interface ni de capture. `fetch_replies` et `get_status` sont appelés toutes les `reply_poll_interval_s` secondes (5 par défaut) tant qu'un incident est ouvert avec une alerte soumise. Les appels peuvent se chevaucher : l'adaptateur doit être thread-safe.

**Données échangées.**

Demande émise par le moteur, au plus une fois par couple (incident, destinataire) :

```python
AlertRequest(
    incident_id="INC-001-14h32",                     # stable pendant tout l'incident
    occurred_at=datetime(2026, 1, 15, 14, 32),       # heure civile locale, sert au « HH:mm » du message
    cause=FallCause.INDIVIDUAL,                      # INDIVIDUAL | COLLISION | UNDETERMINED
    first_name="Marie",                              # None si l'attribution à la personne tombée est incertaine
    address="123, rue Exemple, Montréal",
    recipient_number="+15145550123",                 # E.164, proche configuré
    sender_number="+15145550199",                    # E.164, numéro Twilio
)
```

- `first_name is None` : utiliser la formulation sans prénom de PRODUCT.md (témoin visible, collision ou cause indéterminée).
- `cause is FallCause.COLLISION` : « ALERTE : COLLISION ET CHUTE POSSIBLES À 14:32 à … ».
- Abdoullah compose le texte du SMS. Le moteur ne le journalise pas.

Retour de `submit_alert` :

```python
SmsSubmission(status=SmsStatus.PENDING, provider_ref="SM...", error=None, submitted_at=datetime(...))
```

| `SmsStatus` | Sens | Affichage |
| --- | --- | --- |
| `SIMULATED` | Consigné sans envoi réel | « SMS : Simulé » |
| `PENDING` | Requête acceptée, livraison à confirmer | « SMS : En attente · livraison à confirmer » |
| `DELIVERED` | Retour de livraison **explicite** du fournisseur (jamais déduit de `sent`) | « SMS : Livré » |
| `FAILED` | Refus ou échec connu | « SMS : Échec » ; l'alerte locale reste disponible |
| `UNKNOWN` | Résultat réseau incertain après l'enregistrement de l'intention ; **pas de renvoi automatique** | « SMS : Livraison inconnue » |

Règles pour l'adaptateur : `submit_alert` ne lève pas d'exception pour une erreur réseau ou fournisseur (il retourne `FAILED` ou `UNKNOWN`), enregistre durablement l'intention avant l'appel réseau, ne renvoie jamais deux fois pour le même `incident_id` et destinataire, masque numéros et secrets dans `error`. `get_status` ne retourne `DELIVERED` que sur un statut de livraison explicite.

Réponses entrantes :

```python
IncomingSms(message_id="SM_IN_1", sender="+15145550123", recipient="+15145550199",
            body="Je suis en route, j'arrive dans environ 20 minutes",
            received_at=datetime(2026, 1, 15, 14, 33))   # heure réelle du message, pas l'heure de lecture
```

`fetch_replies(since)` retourne les messages entrants **du contact configuré vers le numéro Twilio**, dédupliqués par `message_id`. `since` est l'heure de la demande d'envoi de l'incident ouvert. Le moteur vérifie ensuite l'expéditeur, le destinataire, l'heure et l'incident (voir section 4) : un message étranger, en double ou tardif n'est jamais affecté à un mauvais incident.

**Démarrage.** Variables dans [.env.example](../.env.example) : `FAAALL_SMS_MODE`, `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER`, `FAAALL_SMS_TO_NUMBER`, `FAAALL_MONITORED_FIRST_NAME`, `FAAALL_ALERT_ADDRESS`. Le lot 1 lit `FAAALL_SMS_MODE`, `TWILIO_FROM_NUMBER`, `FAAALL_SMS_TO_NUMBER`, `FAAALL_MONITORED_FIRST_NAME` et `FAAALL_ALERT_ADDRESS` (via `.env`, puis `config.local.json` qui les remplace) ; le jeton et la clé API sont lus par le module d'Abdoullah et ne sont jamais écrits dans `config.local.json`. Aucune variable n'est ajoutée ou renommée par le lot 1.

**Vérification.**

```powershell
.\scripts\test.ps1 tests\test_contracts.py
```

Le test importe `assert_gateway_contract` de [tests/contracts.py](../tests/contracts.py). Côté adaptateur, l'appeler avec un faux client HTTP :

```python
from tests.contracts import assert_gateway_contract
assert_gateway_contract(create_simulated_gateway(config, clock))   # puis l'adaptateur réel avec un faux client HTTP
```

Limite connue : seul le contrat est vérifié. Aucune soumission ni lecture réelle n'a été faite.

## 2. Interpréteur des réponses (OpenRouter)

**Point de branchement.** Créer `faaall_alarm/replies/__init__.py` qui expose :

```python
def create_interpreter(config: AppConfig) -> ReplyInterpreter: ...
```

Il est chargé par `integration.build_interpreter` **uniquement lorsque le SMS réel est autorisé** (mêmes conditions que la section 1). Sinon, `SimulatedReplyInterpreter` (règles simples, sans clé API) est utilisé : la démo et les tests n'envoient donc jamais de texte à OpenRouter. Si le module manque en mode réel, l'interpréteur retourne « Réponse reçue, à vérifier ». *Décision à valider ensemble :* autoriser OpenRouter avec un SMS simulé pour tester le modèle.

```python
class ReplyInterpreter(Protocol):
    def interpret(self, text: str) -> ReplyInterpretation: ...   # lève InterpretationUnavailable en cas d'échec
```

**Données échangées.** Entrée : le texte seul, sans numéro, sans adresse ni identité de la configuration. Sortie validée :

```python
ReplyInterpretation(state=ReplyState.EN_ROUTE, announced_delay_minutes=20)
```

| Texte du proche | `state` | `announced_delay_minutes` |
| --- | --- | --- |
| « Je suis en route, j'arrive dans environ 20 minutes » | `EN_ROUTE` | 20 |
| « Je suis en route » | `EN_ROUTE` | `None` |
| « OK » ; « Tu es à 20 minutes ? » | `TO_CHECK` | `None` |
| « Finalement je ne peux pas venir » | `UNAVAILABLE` | `None` |
| Prise en charge sans déplacement | `CONFIRMED` | `None` |

Le délai n'est renseigné que s'il est explicite. Toute sortie invalide, tout échec réseau ou tout délai d'attente doit lever `InterpretationUnavailable` : le moteur affiche alors « Réponse reçue, à vérifier ». Les appels sont faits depuis un fil de travail, pas depuis l'interface.

**Vérification.** `assert_interpreter_contract(create_interpreter(config))` de [tests/contracts.py](../tests/contracts.py), avec un faux client HTTP. Cas à couvrir par Abdoullah : ambigu, négation, correction, échec du service.

## 3. Page Réglages

**Point de branchement.** Créer `faaall_alarm/settings_page.py` qui expose :

```python
def open_settings_page(parent: tkinter.Misc, services: SettingsServices) -> None: ...
```

Le bouton **Réglages** de l'écran de surveillance ([ui/main_window.py](../faaall_alarm/ui/main_window.py), `MainWindow.open_settings`) appelle cette fonction dès que le module est importable. Sinon, la page provisoire [ui/settings_fallback.py](../faaall_alarm/ui/settings_fallback.py) s'ouvre. *Hypothèse à valider :* la page est écrite en Tkinter comme l'écran. Si un autre outil est retenu, adapter `MainWindow.open_settings` (une seule méthode).

`SettingsServices` ([settings_services.py](../faaall_alarm/settings_services.py)) est la façade fournie par le lot 1 :

```python
services.read() -> AppConfig                                   # config.local.json > environnement > défauts
services.validate(values: Mapping[str, Any]) -> list[ConfigIssue]   # [] si valide
services.apply(values: Mapping[str, Any]) -> ApplyResult       # valide, enregistre, applique au moteur en cours
services.test_voice() -> VoiceResult                           # test sonore seul : aucun incident, aucun SMS
services.test_sms_simulated() -> SmsSubmission                 # message de test simulé, aucun envoi réel
services.sms_availability() -> SmsAvailability                 # vérifie le canal (bloquant : appeler hors du fil de l'interface)
services.clear_history() -> None
```

Le test d'envoi **réel** n'est volontairement pas fourni : c'est une action distincte, à ajouter par le lot 2 avec l'adaptateur réel et une confirmation explicite du destinataire convenu.

**Données échangées.** Champs de `AppConfig` ([config.py](../faaall_alarm/config.py)) :

| Champ | Type, unité | Valeurs valides |
| --- | --- | --- |
| `camera_index` | entier | ≥ 0 |
| `countdown_seconds` | secondes | 3 à 120 (10 par défaut) |
| `monitored_first_name` | texte | ≤ 40 caractères |
| `alert_address` | texte | ≤ 120 caractères |
| `contact_name` | texte | ≤ 60 caractères |
| `contact_number`, `twilio_number` | E.164 | `+15145550123` |
| `sms_mode` | `"simulated"` ou `"live"` | `simulated` par défaut |
| `sms_live_confirmed` | booléen | requis `True` pour `live` |
| `reply_poll_interval_s` | secondes | 2 à 60 |
| `voice_enabled`, `voice_message`, `voice_speed_wpm`, `espeak_path` | booléen, texte ≤ 200, mots/min 80 à 300, chemin | message non vide |
| `monitored_zone` | `[x0, y0, x1, y1]` normalisés, ou `[]` | 0 ≤ x0 < x1 ≤ 1, idem y |
| `lying_zones` | liste de rectangles normalisés (lit, canapé) | idem |

Exemple de validation :

```python
services.validate({"countdown_seconds": 1, "contact_number": "514-555-0123"})
# [ConfigIssue(field="countdown_seconds", message="Décompte : entrer une valeur entre 3 et 120 secondes."),
#  ConfigIssue(field="contact_number", message="Numéro du proche : utiliser le format international, par exemple +15145550123.")]
```

`apply` fusionne les champs fournis avec la configuration actuelle, ne modifie rien en cas d'erreur (`ApplyResult(ok=False, issues=...)`) et écrit `config.local.json` de façon atomique. Le décompte réglé s'applique à l'incident suivant ; les zones et les numéros s'appliquent immédiatement.

**Vérification.**

```powershell
.\scripts\test.ps1 tests\test_config.py
.\scripts\run.ps1        # puis Réglages : page provisoire, saisir un décompte de 1 s → erreur sous le champ
```

## 4. Demande d'alerte et réponse du proche (moteur → écran)

**Point de branchement.** [incident.py](../faaall_alarm/incident.py) : `IncidentEngine.tick()` décide à l'échéance et appelle le port `submit_sms(AlertRequest)` ; [core.py](../faaall_alarm/core.py), `MonitoringCore.poll_background()` lit les réponses et appelle `IncidentEngine.handle_incoming_sms(IncomingSms) -> ReplyRouting`.

**Rattachement d'une réponse (avant tout appel à l'IA).**

| Condition | Résultat | Écran |
| --- | --- | --- |
| `message_id` déjà vu | `DUPLICATE` | rien |
| Expéditeur ≠ contact configuré ou destinataire ≠ numéro Twilio | `IGNORED_SENDER` | ligne de journal sans numéro |
| Aucun incident ouvert avec alerte soumise | `NEEDS_REVIEW` | « Réponse reçue, à vérifier » (journal) |
| `received_at` avant l'envoi de l'alerte, ou alerte précédente restée sans réponse dans les 30 min | `NEEDS_REVIEW` | « Réponse reçue, à vérifier » dans le panneau |
| Plus ancienne que la réponse déjà affichée | `IGNORED_OLDER` | rien (une mise à jour plus récente n'est pas remplacée) |
| Sinon | `ATTACHED` | « Réponse du proche reçue, analyse en cours… » puis résultat |

Après interprétation : « Sophie a répondu. En route. Arrivée estimée dans 20 min. (reçue à 14:33) », « Délai non précisé », « Indisponible » ou « Réponse reçue, à vérifier ». Le texte brut n'est jamais journalisé et un SMS entrant ne ferme jamais l'incident.

**Vérification (recette simulée, sans clé API).**

```powershell
.\scripts\demo.ps1                 # fenêtre : après l'échéance, la réponse simulée met l'écran à jour
.\scripts\demo.ps1 -Headless       # même parcours en texte
.\scripts\test.ps1 tests\test_incident_engine.py tests\test_scenarios_end_to_end.py
```

Résultat attendu de la démo : voix une fois, décompte 10 s, « SMS : Simulé », puis « Sophie a répondu. En route. Arrivée estimée dans 20 min. » dans le panneau d'alerte ; l'incident reste ouvert jusqu'à **Clore l'incident**.

Injecter une réponse de test depuis du code :

```python
gateway.inject_reply("Je suis en route, j'arrive dans environ 20 minutes",
                     sender="+15145550123", recipient="+15145550199")   # numéros factices
```

Limite connue : l'association de plusieurs incidents rapprochés repose sur l'heure et sur l'absence de réponse précédente ; un téléphone réel reste à essayer.
