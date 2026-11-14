# Raccordement des lots 1 et 2

Le [contrat du lot 2](integration-lot-2.md) est appliqué : le moteur d'incident (lot 1) pilote `SmsService`, `OpenAI` et la page Réglages d'Abdoullah. Ce document dit où chaque point est branché, ce qui a été vérifié et ce qui reste à faire.

## Où est branché chaque élément

| Contrat du lot 2 | Branchement dans le lot 1 |
| --- | --- |
| Ouvrir `open_settings(parent, backend, services, state_dir)` | `MainWindow.open_settings` ([ui/main_window.py](../faaall_alarm/ui/main_window.py)). `backend` est `AppSettingsBackend`, `services` un `LocalSettings`, `state_dir` vaut `STATE_DIR`. Une seule fenêtre à la fois. |
| `SettingsPort.load / validate / apply / preview / test_sound` | [settings_backend.py](../faaall_alarm/settings_backend.py). `load` traduit `AppConfig` en `Settings`. `validate` applique les règles de la page puis celles de l'application. `apply` écrit `config.local.json` et met à jour le moteur. `preview` retourne un PNG de 450 × 253 de la capture en cours (ou `None`, aperçu simulé, hors webcam). `test_sound` appelle celui de `LocalSettings` et ne déclenche aucun incident. |
| `SmsService(state_dir / "sms.sqlite3", services.sms(settings), source)` | [integration.py](../faaall_alarm/integration.py), `build_sms_gateway`. La source passée est la vraie : webcam → `Source.CAMERA`, MP4 → `Source.MP4`, démo → `Source.DEMO`. Le service est reconstruit après **Réglages** seulement quand aucun incident n'est ouvert. |
| `submit(alert)` à l'échéance sérialisée avec l'annulation | `IncidentEngine._submit` ([incident.py](../faaall_alarm/incident.py)) enregistre l'intention durablement, puis le port appelle `SmsServiceGateway.submit_alert` ([adapters.py](../faaall_alarm/adapters.py)) sur un fil de travail. L'heure d'incident reçoit le fuseau local, `collision=True` pour une collision, `name_reliable=True` seulement si une seule personne a été vue et que la cause est individuelle. |
| `refresh(incident_id)` toutes les 5 s, `check()` | `MonitoringCore.poll_background` ([core.py](../faaall_alarm/core.py)), sur le pool de fils de travail. L'écran démarre à **SMS non vérifié**, puis affiche le résultat de `check()`. |
| `poll_replies(since)` puis `acknowledge_reply(sid)` | `SmsServiceGateway.fetch_replies` ; `core` acquitte après la décision de rattachement. |
| `associate`, fenêtres de 30 min et de 24 h | Le moteur applique les mêmes règles (`handle_incoming_sms`) : expéditeur et destinataire, heure strictement postérieure à la tentative dans les 30 min, autre soumission dans les 24 h précédant la réponse → **Réponse reçue, à vérifier**, doublon par identifiant, réponse plus ancienne ignorée. Les numéros et le décompte sont figés à l'ouverture de l'incident. |
| `interpret_simulated(body)` / `OpenAI(key, model).interpret(body, redact=...)` | `build_interpreter`. OpenAI n'est utilisé que si le SMS réel est autorisé ; la démo, les MP4 et les tests utilisent les règles hors ligne. Le texte seul est transmis, avec prénom, nom du proche, adresse et numéros masqués. Une panne ou un refus donne **Réponse reçue, à vérifier**. |
| `inject_reply(body, sid, t)` | `SmsServiceGateway.inject_reply` (canal simulé), utilisé par la démo. |

## Frontière d'envoi réel

Le SMS réel exige `sms_mode = live`, l'activation explicite (choisir **Réel** dans la page) et une vraie webcam. Elle est vérifiée deux fois : `real_sms_allowed` côté application, puis `Source` côté `SmsService` (qui reste simulé pour MP4, démo et tests). Un test passe une configuration `live` valide en MP4 et en simulation avec un transport qui échoue au moindre appel : aucun appel n'a lieu.

## Vérifié

- 169 tests, dont `tests/test_integration_lot2.py` : parcours complet avec le vrai `SmsService` simulé, base SQLite et acquittement ; parcours « réel » avec transports Twilio et OpenAI factices (envoi, statut **Livré**, réponse, délai de 20 min, aucune coordonnée envoyée à OpenAI) ; panne OpenAI ; identifiants d'incident uniques ; page Réglages (charger, valider, appliquer, rouvrir, erreurs par champ, aperçu).
- Fenêtre réelle en démo : **Réglages** ouvre la page du lot 2, et l'écran affiche « Sophie a répondu. En route. Arrivée estimée dans 20 min. » après le SMS simulé.
- `.\scripts\demo.ps1` et `python -m faaall_alarm --sms-recipe` (recette SMS du lot 2 dans le terminal).

## Reste à faire sur le poste cible

- Essai réel Twilio avec le téléphone convenu : les appels réseau ont été exercés seulement avec des transports factices. Le bouton **Envoyer un SMS de test réel** de la page reste l'action distincte prévue pour cet essai.
- Appel réel d'OpenAI avec la clé locale et le modèle choisi.
- Page Réglages : la page ne gère qu'une zone de repos, alors que l'application en accepte plusieurs ; enregistrer depuis la page remplace donc les zones de repos par celle de la page.
- Reprise après arrêt : un SMS lu mais pas encore interprété n'est pas rejoué (il est acquitté après son rattachement). À décider avec Abdoullah si ce risque est acceptable.

## Contrat interne du moteur (pour les tests)

`SmsGateway` et `ReplyInterpreter` ([sms_contract.py](../faaall_alarm/sms_contract.py)) sont les protocoles que consomme le moteur. `tests/contracts.py` fournit `assert_gateway_contract` et `assert_interpreter_contract`, utilisés avec les adaptateurs du lot 2. Le moteur n'a aucune dépendance directe au lot 2 : seuls `adapters.py`, `integration.py` et `settings_backend.py` l'importent.
## Raccordement Telegram

Abdoullah fournit `TelegramGateway` dans `faaall_alarm/telegram.py`. `build_sms_gateway` le sélectionne pour une webcam avec `sms_mode="telegram"` et activation explicite. `submit_alert(request: AlertRequest) -> SmsSubmission` conserve l’intention en SQLite avant `sendMessage`. Une alerte fictive peut renvoyer `TELEGRAM_ACCEPTED` avec `provider_ref="telegram:42"`. Un refus renvoie `FAILED` et un délai dépassé `TELEGRAM_UNKNOWN`.

`fetch_replies(since)` renvoie des `IncomingSms` dont `incident_ref` est renseigné uniquement après vérification du chat, du message cité et de l’horodatage. Adam vérifie côté surveillance que cette référence correspond à l’incident actif et que la réponse déclenche l’interprétation sans clore l’incident. La réponse au test suit le même lecteur, dans une file distincte.

Configurer `TELEGRAM_BOT_TOKEN` selon le README. Recette simulée avec `.\scripts\test.ps1 tests/test_telegram.py`, puis recette sur l’iPhone convenu pour la notification et la réponse. Aucun serveur webhook ni migration manuelle. L’installation crée `telegram.sqlite3` dans le dossier de données local.
