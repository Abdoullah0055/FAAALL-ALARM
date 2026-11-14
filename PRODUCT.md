# FAAALL-ALARM — document produit

## 1. Vision

FAAALL-ALARM est une application Windows qui surveille une zone intérieure avec une webcam fixe. Lorsqu’elle détecte une chute probable, elle affiche une alerte et fait parler l’ordinateur. Si personne n’annule dans le délai prévu, elle prévient un proche par SMS.

**L’écran du PC est le cœur du produit.** Il affiche la détection, déclenche la voix de synthèse locale et montre la réponse du proche. Le SMS prévient le proche même si la personne au sol ne peut pas atteindre l’ordinateur. Le proche peut répondre dans la conversation SMS avec ses propres mots, et l’ordinateur affiche la confirmation et le délai annoncé.

Le premier public visé est une personne surveillée dans une pièce avec son accord, un proche qui accepte de recevoir les SMS et la personne qui configure le poste. Ces deux derniers rôles peuvent être remplis par la même personne.

## 2. Ce que les vidéos apportent

| Référence | Comportement observé | Décision pour FAAALL-ALARM |
| --- | --- | --- |
| `ai__computervision__python__opencv__machinelearni.mp4` | Le suivi distingue une personne allongée dans son lit, assise, en marche, puis en chute. | Observer la transition du corps sur plusieurs images et tenir compte de la zone du lit. |
| `computervision__yolov8__opencv__ai__healthtech__m.mp4` | Le cadre change d’état après l’affaissement. Le suivi disparaît brièvement pendant le mouvement. | Utiliser la boîte comme indice d’appoint et tolérer les courtes pertes de pose. |
| `artificialintelligence__computervision__machinele.mp4` | La démo passe par des états de suspicion et de confirmation. Elle montre aussi un accroupissement sans alerte. | Vérifier la persistance des indices et tester les gestes du quotidien. |
| `fall_detection_example_advanced.mp4` | Le compte à rebours est visible. La personne reste au sol alors que le statut repasse au vert, puis une seconde alerte apparaît. | Garder un seul incident actif jusqu’à une reprise de posture observée ou une clôture manuelle. |
| `fall_detection_system_with_ai_and_computer_vision_.mp4` | Le squelette et les journaux montrent des indices de posture et de mouvement. Un identifiant change autour d’une chute. | Conserver l’incident même si le suivi attribue un nouvel identifiant. |

Ces références guident le comportement attendu. La voix locale et le dialogue par SMS avec le proche constituent les ajouts propres à FAAALL-ALARM.

## 3. Périmètre de la première version

| Inclus | Paramètres de départ |
| --- | --- |
| Surveillance sur un ordinateur Windows | Une webcam fixe et une pièce. Suivi de deux personnes au maximum pour distinguer une chute individuelle d’une collision. Les corps et le sol doivent être visibles dans la zone surveillée. |
| Détection locale | Python, OpenCV pour lire la caméra ou un fichier, MediaPipe Pose pour repérer le corps. Le traitement des images reste sur le PC. |
| Interface locale en français | Aperçu de la caméra, état de la surveillance, fenêtre d’alerte, compte à rebours, réponse du proche et historique. |
| Alerte vocale | Texte prononcé en français sur le PC par eSpeak NG, un moteur gratuit de synthèse non neuronale qui fonctionne hors ligne. [Documentation eSpeak NG](https://github.com/espeak-ng/espeak-ng) |
| Alerte à distance | Un proche configuré et un numéro Twilio compatible avec l’envoi et la réception de SMS. Activation volontaire des SMS réels. |
| Interprétation de la réponse | Un modèle de langage via OpenAI traite la réponse libre en français et extrait l’engagement du proche et son délai estimé. |
| Mode test | Relecture de MP4, simulation d’incident et de réponse du proche. Les SMS simulés sont distincts dans l’interface et le journal. |

Les caméras multiples, le suivi de plus de deux personnes, la reconnaissance faciale, les commandes vocales, l’application mobile et l’appel automatique aux services d’urgence restent hors périmètre. La surveillance est prévue comme dispositif d’appoint. Elle exige un poste allumé et éveillé ainsi qu’une caméra opérationnelle.

## 4. Mise en route

1. Choisir la webcam et contrôler dans l’aperçu qu’elle voit le sol et une personne debout en entier. Délimiter la zone surveillée et, si elle en contient, repérer les surfaces normales où l’on s’allonge, comme le lit ou le canapé. Une chute qui se termine au sol à côté d’un meuble doit rester détectable.
2. Configurer la synthèse vocale française et utiliser **Tester le son** pour vérifier l’intelligibilité et le volume depuis la zone surveillée. Exemple de message « Une chute possible a été détectée. Si tout va bien, appuyez sur Annuler l’alerte. »
3. Configurer le prénom de la personne surveillée, l’adresse d’intervention, le nom et le numéro international du proche et le numéro Twilio. Utiliser la simulation pour vérifier le message, le compte à rebours et l’affichage d’une réponse. Une action distincte permet d’envoyer un SMS de test au destinataire convenu.
4. Activer la surveillance. L’interface indique la source, l’état de la caméra, la disponibilité de la voix et le mode SMS **Simulé** ou **Réel**. Une panne d’une sortie d’alerte reste visible sans bloquer l’autre.

## 5. Comment le système reconnaît une chute

MediaPipe fournit des points du corps et des indices de visibilité. FAAALL-ALARM en tire une évolution de la posture. Le modèle de pose repère le corps, puis la logique du produit décide si un incident doit être ouvert.

- **Mouvement récent.** Suivre la descente des épaules et des hanches ainsi que le changement d’angle du torse. Mesurer le mouvement sur des images horodatées, à l’échelle du corps suivi. La cadence de la caméra peut varier.
- **Posture d’arrivée.** Chercher une posture basse qui persiste après le mouvement, avec une inclinaison du torse ou une forte réduction de la hauteur apparente. Tenir compte de l’emplacement et du cadrage. Plusieurs indices peuvent compenser un axe de chute peu visible.
- **Gestes ordinaires.** Une posture allongée déjà présente au démarrage, un passage normal sur un lit ou un accroupissement ne suffisent pas à confirmer une chute. Une chute depuis une chaise doit pouvoir déclencher l’alerte même sans posture debout préalable.
- **Indices fragiles.** Une image isolée, une boîte horizontale ou un point de pose peu fiable ne suffisent pas. Avant confirmation, une perte prolongée de la pose ramène la suspicion à un état indéterminé. Après confirmation, l’incident reste ouvert même si la personne sort du champ ou que le suivi change d’identifiant.

Les seuils de vitesse, d’inclinaison, de visibilité et de persistance doivent être calibrés sur la webcam cible. La vitesse est un indice parmi d’autres. Une descente lente et continue terminée au sol constitue un scénario de validation à part entière.

### Distinguer une chute d’une collision

Suivre les deux trajectoires avec des identifiants temporaires et sans reconnaître les visages. Une chute individuelle peut se produire même si un témoin est présent. Une collision probable exige des indices concordants sur plusieurs images, comme la convergence des trajectoires, un contact apparent suivi d’un changement de mouvement chez au moins une des personnes. Une proximité ou un chevauchement des silhouettes à l’image ne suffit pas. Si les indices ne permettent pas de trancher, indiquer **Cause indéterminée** et conserver une chute probable si les critères de chute sont remplis.

Afficher et journaliser séparément la chute individuelle et la collision probable. Une collision sans chute détectée ne déclenche pas le SMS de chute. Une collision suivie d’une chute suit le même décompte et se traite comme un seul incident, même si les deux personnes tombent. L’écran peut l’indiquer par **Collision probable · chute possible**. Si les deux personnes sont tombées, une reprise de posture automatique n’annule ou ne clôt l’incident que si elle est observée pour les deux.

## 6. Cycle d’un incident

| État | Entrée et comportement | Transition |
| --- | --- | --- |
| **Surveillance** | La caméra est analysée sans incident actif. L’interface distingue une pose exploitable, une personne absente ou masquée et une caméra indisponible. | Des indices concordants ouvrent une suspicion. |
| **Suspicion** | Le système observe les images suivantes pour voir si la posture basse se maintient. | Des indices persistants confirment la chute probable. Un geste terminé sans chute ou des indices insuffisants mettent fin à la suspicion. |
| **Chute probable** | Création d’un identifiant d’incident. L’interface affiche l’alerte, lance la voix une fois et démarre un délai de **10 secondes par défaut**, configurable. | Une annulation manuelle ou une reprise de posture normale, fiable et soutenue avant l’échéance annule le SMS. À l’échéance, le système soumet le SMS en mode réel ou consigne une simulation en mode test. |
| **Alerte traitée** | L’interface indique l’état du SMS et, séparément, la réponse du proche ou l’attente d’une réponse. L’incident reste ouvert. | Une reprise de posture soutenue de chaque personne tombée ou **Clore l’incident** termine l’incident. La réponse du proche met à jour son statut de prise en charge. |
| **Alerte annulée** | Aucun SMS automatique n’est envoyé si l’annulation intervient avant la soumission. L’incident annulé reste consigné. | Le détecteur se réarme après une reprise soutenue de la posture. Une clôture manuelle reste possible. |

La disparition du squelette, la perte de la caméra ou la fermeture de la fenêtre d’alerte ne valent ni annulation ni rétablissement. Si la caméra se coupe pendant une alerte confirmée, le compte à rebours continue et l’écran signale la panne. Une panne caméra hors incident ne déclenche pas de SMS de chute. Le décompte se base sur le temps écoulé, indépendamment de la cadence d’analyse.

Une personne qui reste au sol ne crée pas de nouvel incident à chaque image. Après une clôture manuelle, la même posture au sol ne peut pas relancer l’alerte. Le système attend une nouvelle transition observée depuis une posture de départ exploitable. Dès la soumission au fournisseur SMS, l’action d’annulation est remplacée par la clôture locale. La livraison peut encore être en cours.

## 7. Voix, SMS et erreurs

**Voix.** La synthèse locale démarre avec le compte à rebours et n’interrompt pas la surveillance. Le bouton **Tester le son** fonctionne sans déclencher d’incident. Si le moteur ou la voix française est indisponible, afficher **Voix indisponible**. Le compte à rebours visuel et l’envoi SMS restent utilisables.

**SMS sortant.** Le message utilise le prénom et l’adresse configurés et l’heure locale de l’incident au format `HH:mm`. Exemple avec une adresse fictive.

> ALERTE : MARIE EST TOMBÉE À 14:32 à 123, rue Exemple, Montréal !

Si le prénom configuré ne peut pas être associé avec certitude à la personne qui a chuté, utiliser une formulation sans prénom. Pour une collision suivie d’une chute, le SMS peut indiquer « ALERTE : COLLISION ET CHUTE POSSIBLES À 14:32 à 123, rue Exemple, Montréal ! »

Le PC envoie le texte au fournisseur, sans image ni vidéo. L’interface affiche **Simulé**, **En attente**, **Livré**, **Échec** ou **Livraison inconnue** selon les informations disponibles. Le retour initial du fournisseur indique une soumission, et l’état **Livré** exige un statut de livraison explicite de Twilio. Une livraison confirmée ne renseigne pas sur la prise en charge par le proche. [États des messages Twilio](https://www.twilio.com/docs/messaging/api/message-resource)

L’application mémorise la soumission par incident et par destinataire pour éviter les doublons. Si une réponse réseau est perdue après la tentative, elle signale l’incertitude et évite de renvoyer automatiquement le message. L’absence de connexion ou une configuration invalide est visible à l’écran et dans le journal. La voix et l’affichage restent locaux.

### Réponse du proche et mise à jour de l’écran

**Canal Telegram.** En surveillance webcam, Telegram est le canal par défaut. L’utilisateur associe un proche en conversation privée et configure l’adresse d’intervention. Une configuration incomplète donne un échec explicite. La simulation est réservée aux parcours hors surveillance webcam. Telegram reçoit le texte de l’alerte, dont l’adresse, et héberge les réponses du proche. Un succès API affiche **Accepté par Telegram**. En cas de délai réseau dépassé, afficher **Envoi Telegram incertain** sans renvoi automatique. Une réponse texte doit arriver dans les 30 minutes. Elle peut citer le message exact ou être envoyée directement si une seule tentative correspond à cette période dans le chat. Le test manuel reste distinct des alertes de surveillance. En recette, vérifier la notification sur l’iPhone verrouillé, la réponse et son affichage sur le PC.

1. **Réception.** Le proche répond directement au numéro Twilio. Au Canada, Twilio prend en charge le SMS bidirectionnel. Un webhook entrant peut recevoir le texte (`Body`), l’expéditeur (`From`), le numéro destinataire (`To`) et l’identifiant du message (`MessageSid`). Pour le premier prototype Windows, le PC peut aussi interroger périodiquement l’API Messages depuis l’application. Cette option permet de valider le parcours sans héberger de serveur public. [SMS au Canada](https://www.twilio.com/en-us/guidelines/ca/sms) · [Webhook SMS entrant](https://www.twilio.com/docs/messaging/guides/webhook-request) · [API Messages](https://www.twilio.com/docs/messaging/api/message-resource)
2. **Association.** Le logiciel accepte les SMS du contact configuré à destination du bon numéro Twilio. Il vérifie l’heure du message et le rattache uniquement à un incident ouvert pour lequel une alerte a déjà été soumise. Un SMS ordinaire ne fournit pas l’identifiant de l’alerte d’origine. Si le rattachement est ambigu, par exemple en cas de réponse tardive à une alerte précédente, l’application indique qu’une réponse est à vérifier. Un même message est traité une fois, même après une reconnexion.
3. **Interprétation.** Le modèle de langage analyse le texte et renvoie un état parmi **En route**, **Prise en charge confirmée**, **Indisponible** ou **À vérifier**, ainsi qu’un délai annoncé si le texte en contient un. « Je suis en route, environ 20 minutes » donne **En route** et **environ 20 minutes**. « Je suis en route » donne **En route** avec délai inconnu. « OK » seul ou une question comme « Tu es à 20 minutes ? » donne **À vérifier**. Une correction explicite comme « Finalement je ne peux pas venir » met à jour la prise en charge.
4. **Affichage.** Sur l’écran de surveillance, le PC affiche par exemple **« Sophie a répondu. Elle est en route. Arrivée estimée dans 20 min. »**, avec l’heure de la réponse. Le délai est déclaré par le proche. S’il manque, afficher **Délai non précisé**. Si la réponse est ambiguë ou que l’analyse échoue, afficher **Réponse reçue, à vérifier**. La caméra continue d’être affichée et l’incident garde son propre état.

OpenAI reçoit uniquement le texte utile de la réponse, avec les numéros, noms et adresse connus masqués. La sélection de l’incident, la validation du résultat et la mise à jour de l’écran relèvent du code. Le modèle par défaut est `gpt-6-luna`, configurable avec `OPENAI_MODEL`. En cas d’échec d’OpenAI, l’écran affiche **Réponse reçue, à vérifier**. Pour l’essai de bout en bout avec le texte sortant personnalisé, prévoir un compte Twilio payant. L’essai gratuit limite les envois aux modèles de Twilio et aux destinataires vérifiés. [Modèle OpenAI](https://developers.openai.com/api/docs/models/gpt-6-luna) · [Restrictions de l’essai Twilio](https://www.twilio.com/docs/usage/trials)

## 8. Interface, journal et confidentialité

L’écran de surveillance comporte deux colonnes et aucun en-tête global.

Utiliser **Personne** pour désigner la personne suivie et **Personne 1** et **Personne 2** s’il y en a deux. Le prénom configuré reste utilisable dans le SMS sortant. La page **Réglages** permet de modifier la configuration et de tester la voix et le canal SMS.

| Emplacement | Contenu |
| --- | --- |
| **En haut à gauche** | La caméra en direct occupe l’essentiel de la colonne gauche, qui prend environ 70 % de la largeur. Le squelette peut être affiché pour vérifier le cadrage et les points suivis. |
| **Juste au-dessus de la caméra** | Une ligne de texte simple, alignée à gauche avec la vidéo, sans conteneur visuel, fond, bordure ni badge. Par exemple, **Caméra active · SMS opérationnel**. |
| **Sous la caméra** | Une bande mince regroupe les commandes **Squelette**, **Historique** et **Réglages**. |
| **À droite, en haut** | Afficher en grand la prédiction actuelle du moteur de détection, par exemple `STANDING`, `WALKING`, `SITTING`, `FALLING` ou `LYING`. Afficher **Posture indéterminée** quand les points sont insuffisants et **Aucune personne détectée** quand aucune personne n’est détectée. |
| **À droite, au milieu** | Afficher le panneau uniquement pendant une alerte active. Il présente **Chute possible**, le compte à rebours et **Annuler l’alerte**, puis l’état du SMS, l’attente ou la réponse du proche, son délai annoncé s’il est en route et **Clore l’incident**. La vidéo reste visible. |
| **À droite, en bas** | Le panneau défilant **Logs de détection** occupe environ le tiers inférieur de la colonne pendant une alerte. Au repos, le panneau du milieu se replie complètement et les logs récupèrent l’espace libéré. |

La ligne de statut affiche **Caméra inactive** si la source ne fournit plus d’images. Le statut SMS distingue **SMS opérationnel**, **SMS indisponible**, **SMS simulé** et **SMS non vérifié**. Une configuration seule ne suffit pas à afficher **SMS opérationnel**. Cet état repose sur une vérification réussie de la disponibilité du service et reste distinct de la livraison d’un message précis.

- **Logs de détection.** Le panneau affiche les entrées de la session dans l’ordre chronologique, avec l’heure locale au format `HH:mm:ss`. Il montre notamment les changements de posture, les pertes et reprises de suivi et les décisions du détecteur. En lecture MP4, ajouter le temps dans la vidéo aux événements associés à une image. Le bouton **Tout copier** copie le journal de détection complet de la session en texte, horodatages compris, y compris les lignes hors de la zone visible. Afficher une confirmation de copie. Le texte copié suit les mêmes règles de confidentialité que les entrées affichées.
- **Historique local.** Conserver l’identifiant et l’heure de l’incident, le mode, les transitions, l’issue, l’état du SMS, l’état de la réponse et les erreurs techniques. Masquer les numéros et exclure le texte brut des réponses des journaux. Prévoir une action d’effacement de l’historique local.
- **Données.** Traiter les images en mémoire, sans enregistrement ni téléversement par défaut. Les coordonnées du proche et les identifiants de service restent dans la configuration locale, hors de Git. Le fournisseur SMS reçoit les informations nécessaires à l’envoi.

## 9. Critères d’acceptation

| Scénario | Résultat attendu |
| --- | --- |
| Chute simulée vers le sol depuis la position debout ou assise, avec maintien au sol | L’application ouvre un incident, joue la voix une fois et affiche le compte à rebours. Le SMS est soumis à l’échéance si les alertes réelles sont actives. |
| Affaissement lent suivi d’un maintien au sol | Les séquences de recette annotées déclenchent chacune un incident, même en l’absence d’un pic de vitesse. Le délai de détection est relevé séparément. |
| Une personne tombe seule alors qu’un témoin est visible | L’écran indique une chute individuelle si les observations permettent d’écarter la collision. |
| Deux personnes se heurtent avec ou sans chute | La collision probable est distinguée d’une chute individuelle. Avec chute, un seul incident d’alerte suit le décompte et peut envoyer un SMS. Sans chute, la collision est journalisée sans SMS de chute. |
| Deux personnes se croisent ou l’image masque le contact | Le croisement ordinaire ne produit pas de collision. Si une chute est détectée alors que le contact est incertain, l’alerte s’ouvre avec **Cause indéterminée**. |
| Marche, assise ordinaire, accroupissement, se pencher, se coucher normalement et personne déjà allongée au démarrage | Aucun incident dans ces scénarios de recette. |
| Annulation manuelle ou reprise de posture fiable avant la fin du compte à rebours | Le décompte s’arrête, aucune soumission n’est effectuée et l’issue est journalisée. |
| Occlusion temporaire ou changement d’identifiant après confirmation | Le décompte continue. L’incident conserve son identifiant et la voix ne recommence pas. |
| Personne toujours au sol après l’alerte | La même chute ne provoque pas de deuxième SMS. Une nouvelle chute après reprise d’une posture normale crée un nouvel incident. |
| Caméra débranchée sans chute, puis pendant une alerte confirmée | Hors incident, l’interface signale la panne sans SMS de chute. Pendant l’incident, l’alerte existante continue. |
| Réseau coupé ou réponse SMS incertaine | La voix et le décompte fonctionnent. L’état d’envoi indique l’échec ou l’incertitude et une nouvelle tentative automatique ne crée pas de doublon. |
| Lecture d’un MP4 et simulation complète | L’interface et les journaux indiquent le mode test. La lecture utilise les temps du fichier pour évaluer la chute. Aucun SMS réel n’est envoyé. |
| Réponse « je suis en route, j’arrive dans environ 20 minutes » du proche | La notification indique que le proche a répondu, est en route et estime son arrivée à environ 20 minutes. L’incident reste ouvert. |
| Réponse sans heure, ambiguë ou négative | L’écran distingue délai non précisé, réponse à vérifier et indisponibilité. Il n’invente aucun délai. |
| Réponse dupliquée, étrangère, tardive ou ambiguë à rattacher | Aucune mise à jour automatique du mauvais incident. Une réponse valide rejouée ne produit pas une seconde notification. |
| Appareil ou canal SMS indisponible | Les deux états sont lisibles en texte au-dessus de la caméra. En simulation, la ligne indique **SMS simulé**. Pendant une alerte, la colonne de droite reste visible. |
| Surveillance au repos, puis alerte active | La posture actuelle reste en haut de la colonne. Le panneau d’alerte apparaît au milieu et les logs restent visibles en bas. Après clôture, les logs récupèrent l’espace du panneau. |
| Journal plus long que la zone visible | **Tout copier** place dans le presse-papiers toutes les entrées de détection de la session dans l’ordre, avec leurs horodatages et sans données privées. |
| Essai complet sur le poste cible | La voix est audible depuis la zone surveillée. Le téléphone convenu reçoit le SMS de test, y répond et le PC affiche la prise en charge et le délai annoncé. |

Pour les essais de vision, annoter séparément le début de la chute et le contact avec le sol. Relever le délai entre le contact et la voix, les chutes manquées, les fausses alertes par heure de surveillance et les doublons. Mesurer séparément le temps de soumission du SMS et le résultat de livraison. Les cinq vidéos servent de références de démonstration. La validation utilise aussi des prises indépendantes avec plusieurs angles de chute, des postures usuelles, des occultations et des variations de lumière dans le cadrage cible.

### Résultats de recette du lot 2

| Scénario | Vérification |
| --- | --- |
| Incident fictif, annulation, réponse et délai | Réglages Windows avec aperçu fictif. L’annulation avant échéance ne crée aucune tentative. Après échéance, **Simulé** puis **En route** avec environ 20 min, ou **À vérifier** / **Indisponible** selon le texte. |
| Panne et résultat SMS incertain, reprise, livraison | Tests avec transport Twilio factice. Un délai dépassé ou une interruption après enregistrement ne provoquent aucun renvoi. Un statut `sent` reste en attente. |
| MP4 avec Twilio configuré | Isolation vérifiée à la frontière SMS avec configuration factice, puis avec le moteur de surveillance du lot 1 (test d’intégration : aucun appel au transport en MP4 ni en démo). |
| Réponses étrangères, anciennes, dupliquées, ambiguës et panne du modèle | Tests du filtrage, des horodatages, du SID et du contrat d’association. Sorties OpenAI simulées, y compris l’échec et le refus d’un délai inventé. |
| Poste cible | Recette commune ouverte pour la vision, l’audibilité, Twilio et le téléphone, ainsi que la qualité d’interprétation du modèle hébergé. |

## 10. Ordre de réalisation

1. **Vision locale.** Lire les fichiers de référence et la webcam, afficher la pose et journaliser les indices. Rejouer les chutes et les gestes ordinaires avant de régler les seuils.
2. **Incident et voix.** Relier la confirmation à la fenêtre d’alerte, au compte à rebours et à la voix de synthèse locale. Vérifier l’annulation, le réarmement et les pertes de suivi.
3. **SMS sortants.** Brancher la simulation, puis l’envoi et le suivi de livraison. Valider les pannes réseau et l’absence de doublons.
4. **Réponse du proche.** Simuler des réponses libres, vérifier leur rattachement et l’affichage, puis valider l’aller-retour sur le téléphone convenu.
