# FAAALL-ALARM

Application Windows de surveillance par webcam fixe. Elle détecte une chute probable (Python, OpenCV, MediaPipe Pose), fait parler le PC en français avec eSpeak NG hors ligne, affiche un compte à rebours de 10 secondes (réglable), puis prévient un proche par SMS si personne n'annule. Le proche peut répondre par SMS et l'écran affiche sa réponse. C'est un dispositif d'appoint : il exige un poste allumé et une caméra opérationnelle.

Le comportement attendu est défini dans [PRODUCT.md](PRODUCT.md), la répartition du travail dans [PLAN.md](PLAN.md), le raccordement des deux lots dans [docs/RACCORDEMENT.md](docs/RACCORDEMENT.md) et le contrat du lot 2 dans [docs/integration-lot-2.md](docs/integration-lot-2.md).

**État de la V1.** Les lots 1 et 2 sont raccordés : capture, détection, incidents, voix et écran de surveillance (lot 1) pilotent le service SMS Twilio, l'interpréteur OpenAI et la page Réglages (lot 2). Le SMS est **simulé par défaut**. Des transports factices exercent le parcours Twilio et OpenAI. L’essai sur téléphone reste à faire.

## Versions vérifiées

| Composant | Version |
| --- | --- |
| Windows | 11 (10.0.26200) |
| Python | 3.14.7 (environnement de développement) et 3.14.2 (installation neuve par `install.ps1`, Tk 8.6), 64 bits |
| OpenCV | `opencv-contrib-python` 5.0.0.93 |
| MediaPipe | 1.2.3 (tâche *Pose Landmarker*) |
| Tkinter | Tk 9.0 et Tk 8.6 (fournis avec Python) |
| eSpeak NG | 1.52.0 (`winget install eSpeak-NG.eSpeak-NG`) |
| Modèle de pose | `pose_landmarker_lite.task` (float16, version 1), source officielle Google [MediaPipe Pose Landmarker](https://ai.google.dev/edge/mediapipe/solutions/vision/pose_landmarker), empreinte SHA-256 vérifiée par `install.ps1` |

Les autres versions de Python n'ont pas été essayées. `pyproject.toml` demande Python 3.11 ou plus (lot 2). Sur l'installation neuve, `install.ps1`, la vérification et les 136 tests du lot 1 ont réussi ; la suite complète compte maintenant 211 tests.

Le modèle `lite` est conservé après comparaison sur les mêmes MP4 : `full` et `heavy` détectent moins souvent la personne au sol (64 % et 73 % contre 83 %) pour 1,3 à 3,2 fois le temps d'inférence. MediaPipe tourne avec un seuil de confiance de 0,3 (`pose_min_confidence`). Détails : [docs/vision-evaluation.md](docs/vision-evaluation.md).

## Installer

Depuis la racine du dépôt, dans PowerShell. Entoure de guillemets tout chemin qui contient des espaces.

```powershell
.\scripts\install.ps1 -InstallEspeak
```

Le script crée `.venv`, installe les dépendances déclarées dans [pyproject.toml](pyproject.toml), télécharge le modèle de pose dans `models\` (ignoré par Git), installe eSpeak NG si `-InstallEspeak` est fourni, puis lance la vérification (`python -m faaall_alarm --check`). Sans `-InstallEspeak`, la voix est signalée « Voix indisponible » et le reste fonctionne.

Si l'exécution de scripts est bloquée : `powershell -ExecutionPolicy Bypass -File .\scripts\install.ps1 -InstallEspeak`.

## Lancer

```powershell
.\scripts\run.ps1                                  # webcam 0
.\scripts\run.ps1 -Camera 1 -Countdown 15          # autre caméra, décompte de 15 s
.\scripts\run.ps1 -Video "C:\Mes vidéos\essai.mp4" # rejoue un MP4 avec MediaPipe (mode test)
.\scripts\run.ps1 -Video "C:\Mes vidéos\essai.mp4" -Diagnostic "artifacts\diagnostic.jsonl"  # diagnostic local de la vision
```

Le diagnostic, désactivé par défaut, écrit pour chaque personne et chaque image les points fiables, les indices calculés, l'état du détecteur, les changements de piste et les événements. Il n'écrit ni image ni coordonnée brute ; `artifacts\` est ignoré par Git.

Le SMS est **simulé par défaut**. La lecture d'un MP4, la démo et les tests ne peuvent jamais envoyer de SMS réel, même avec des identifiants Twilio valides. Le mode réel exige `sms_mode = live`, l'activation explicite dans les réglages et une vraie webcam.

## Réglages, SMS réel et OpenAI

Le bouton **Réglages** ouvre la page du lot 2 : caméra et zones, voix, décompte, SMS (mode **Simulé** ou **Réel**) et simulation d'incident. Les réglages sont enregistrés dans `config.local.json` et s'appliquent au moteur en cours : le décompte, les numéros et les services valent pour les nouveaux incidents.

Pour le mode réel, copie `.env.example` en `.env` et renseigne sur le poste `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `OPENAI_API_KEY` (et `OPENAI_MODEL` si besoin). Choisis ensuite **Réel** dans l'onglet **SMS** et utilise **Vérifier le canal**. L'envoi d'un SMS réel exige en plus une vraie webcam : la lecture d'un MP4 et la démo restent simulées. Procédure d'essai réel avec le téléphone convenu : [docs/integration-lot-2.md](docs/integration-lot-2.md). La recette SMS du lot 2 seule : `python -m faaall_alarm --sms-recipe`.

Crée la clé sur la plateforme OpenAI. Le modèle par défaut est `gpt-6-luna`. L’API Responses reçoit le texte utile du SMS associé avec les coordonnées connues masquées et `store: false`. [Créer une clé API](https://platform.openai.com/api-keys) · [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna)

Commandes de l'écran : **Squelette** (affiche le squelette), **Historique** (événements locaux, avec effacement), **Réglages**. **Tout copier** copie tout le journal horodaté de la session, données sensibles masquées.

## Démo reproductible (sans clé API)

```powershell
.\scripts\demo.ps1                       # fenêtre : MP4 de mannequins, voix, décompte, SMS simulé, réponse
.\scripts\demo.ps1 -Cancel 4             # le décompte est annulé après 4 s : aucun SMS
.\scripts\demo.ps1 -Scenario collision_avec_chute
.\scripts\demo.ps1 -List                 # tous les scénarios
.\scripts\demo.ps1 -Headless             # chronologie en texte, horloge accélérée, sans fenêtre ni voix réelle
```

La démo génère `artifacts\demo\<scénario>.mp4` (ignoré par Git), le rejoue avec les temps du fichier, fait parler eSpeak NG, affiche le décompte puis simule le SMS. Environ 6 s après, la réponse « Je suis en route, j'arrive dans environ 20 minutes » est injectée dans la passerelle simulée et l'écran affiche « Sophie a répondu. En route. Arrivée estimée dans 20 min. ». Les poses de la démo sont scriptées (mannequins) : MediaPipe n'est pas utilisé dans la démo, il l'est avec `-Video` et la webcam.

## Tester

```powershell
.\scripts\test.ps1
```

Les tests n'utilisent ni webcam, ni Internet, ni SMS réel, ni délai réel de dix secondes : ils rejouent des scénarios de poses simulées avec une horloge contrôlable.

Pour mesurer la vision sur des MP4 (postures, chutes manquées, fausses alertes, délai contact-voix, suivi, coût par étape) et comparer deux versions du code, utiliser `scripts\evaluate_vision.py` : voir [docs/vision-evaluation.md](docs/vision-evaluation.md#reproduire).

## Organisation du code

| Fichier | Rôle |
| --- | --- |
| `faaall_alarm/capture.py`, `pose.py` | Webcam ou MP4 (perte de caméra distincte de la fin de fichier), MediaPipe Pose |
| `faaall_alarm/features.py`, `tracking.py`, `posture.py` | Indices de posture et de mouvement à l'échelle du corps, suivi de deux personnes, posture actuelle |
| `faaall_alarm/detection.py` | Détection de chute (maintien, affaissement lent, réarmement) et de collision (croisement ≠ collision) |
| `faaall_alarm/incident.py` | Moteur d'incident : décompte, annulation, clôture, une seule soumission, rattachement des réponses |
| `faaall_alarm/sms_contract.py`, `sms_simulated.py`, `integration.py` | Contrat avec le module SMS, substituts simulés, frontière d'envoi réel |
| `faaall_alarm/voice.py` | eSpeak NG (français, hors ligne) |
| `faaall_alarm/config.py`, `settings_backend.py` | Seuils nommés, réglages, lecture/validation/application, backend de la page Réglages |
| `faaall_alarm/sms.py`, `replies.py`, `settings.py`, `settings_page.py`, `http.py` | Lot 2 : Twilio, OpenAI, page Réglages (voir [docs/integration-lot-2.md](docs/integration-lot-2.md)) |
| `faaall_alarm/adapters.py` | Adaptateurs entre les types du lot 2 et les protocoles du moteur d'incident |
| `faaall_alarm/core.py`, `app.py`, `ui/` | Assemblage, fil d'analyse, écran Tkinter |
| `faaall_alarm/simulation/` | Scénarios de mannequins, rendu MP4, démo |

Les seuils de détection (valeurs par défaut et unités) sont dans `DetectionThresholds`, [faaall_alarm/config.py](faaall_alarm/config.py). Ils sont calibrés sur des poses simulées et sur le corpus UR Fall (20 chutes, 27 activités), puis vérifiés sur un jeu réservé ([résultats](docs/vision-evaluation.md)). Ils restent à vérifier sur la webcam cible.

## Données locales

Les images et la pose restent en mémoire. L'historique (événements, transitions, issue, statut du SMS) est écrit dans `data\history.jsonl` (ignoré par Git), numéros masqués et sans texte brut des réponses. Les réglages sont dans `config.local.json` (ignoré par Git) et ne contiennent jamais de secret. Les bases du lot 2 (SMS, incidents) sont dans `%LOCALAPPDATA%\FAAALL-ALARM`. Voir [.env.example](.env.example).
