# AGENTS.md

Ces consignes s’appliquent à tout le dépôt FAAALL-ALARM. Lis [PRODUCT.md](PRODUCT.md) avant de modifier un comportement. Il définit le périmètre, le cycle d’incident et les critères d’acceptation. Si la demande change une décision produit, mets le code, les tests et la documentation en accord avec cette décision.

## Travailler dans le dépôt

- Vérifie `git status --short` et lis le code, les tests et les éventuels `AGENTS.md` du sous-dossier avant de modifier un fichier. Préserve le travail local déjà présent.
- Traite la demande en cours et reste dans son périmètre. Une modification qui change la détection, le décompte, l’annulation, l’envoi SMS ou les données collectées doit être explicite.
- Garde les textes visibles et la documentation utilisateur en français. Réponds brièvement en français et indique le résultat, les vérifications effectuées et les limites qui comptent.
- Utilise les commandes documentées par le dépôt. Si tu ajoutes le premier point d’entrée, documente dans le README comment installer, lancer et tester l’application sous Windows avec PowerShell. Indique la version de Python réellement vérifiée avec OpenCV et MediaPipe. Dans les commandes PowerShell, entoure de guillemets les chemins qui contiennent des espaces.
- Déclare les dépendances dans le manifeste du projet. Pour un modèle de pose ajouté au projet, documente sa source officielle, sa version et la manière de l’installer. Garde des seuils de détection nommés, avec leurs unités et leurs valeurs par défaut, dans une configuration identifiable.

## Qualité du code

- Écris toujours du code propre, lisible et maintenable. Utilise des noms qui décrivent le rôle des fonctions et des variables, des unités explicites et des interfaces simples. Une fonction doit avoir une responsabilité claire. Explique les décisions qui seraient difficiles à comprendre à la lecture du code.
- Réutilise les composants existants. Sépare la détection, l’état d’incident, l’interface et les appels aux services selon leurs responsabilités. Garde les réglages et les seuils au même endroit et traite explicitement les erreurs qui changent le comportement visible.
- Relis le diff comme si l’autre collaborateur devait le reprendre demain. Vérifie le parcours touché et ajoute un test ciblé quand il permet de couvrir une régression ou un contrat partagé. Mets à jour la documentation si l’usage ou la configuration change.

## Skills du projet

Les skills sont versionnés dans `.agents/skills/` pour Codex et `.claude/skills/` pour Claude Code. Utilise le skill pertinent au moment où il apporte quelque chose à la tâche.

| Skill | Quand l’utiliser |
| --- | --- |
| [`systematic-debugging`](.agents/skills/systematic-debugging/SKILL.md) | Dès qu’un bug apparaît, avant de modifier le code. Reproduis le problème et établis sa cause avec un MP4, des logs ou une simulation, selon le cas. |
| [`verification-before-completion`](.agents/skills/verification-before-completion/SKILL.md) | Avant de déclarer un résultat, de livrer un changement ou d’ouvrir une PR. Appuie tes affirmations sur des vérifications récentes et distingue la simulation d’un essai sur le poste cible. |
| [`frontend-design`](.agents/skills/frontend-design/SKILL.md) | Pour concevoir ou retoucher la page de présentation. Pour l’application Windows, suis le schéma et les états définis dans `PRODUCT.md`. |

Pour mettre à jour un skill, conserve la même version dans les deux dossiers, note la révision source et vérifie sa licence et ses éventuels fichiers de support. Les consignes de ce dépôt s’appliquent aussi aux procédures proposées par les skills.

- Source de `systematic-debugging` et `verification-before-completion` — [obra/superpowers à la révision 22f0b46](https://github.com/obra/superpowers/tree/22f0b463e7a5e579904dd6211a520321035526f7/skills).
- Source de `frontend-design` — [anthropics/skills à la révision 1780c5e](https://github.com/anthropics/skills/tree/1780c5e5a552fd4d1c9d6a7b0cab26b0c2bef064/skills/frontend-design).

## Commits et dépôt public

- Fais des commits régulièrement après chaque unité de travail cohérente et vérifiée. Garde les changements indépendants dans des commits distincts et préserve les modifications déjà présentes dans le dépôt.
- Écris un titre court et précis qui décrit le changement, par exemple `feat(sms): récupérer les réponses du proche`. Ajoute un corps si le motif, un changement de configuration ou une limite mérite une explication. Applique la même règle aux descriptions de pull requests.
- Ajoute à l’index uniquement les fichiers du changement, en nommant leurs chemins. Avant chaque commit, contrôle la liste des fichiers et relis le diff indexé. Vérifie aussi les fichiers binaires et générés, dont le contenu peut échapper au diff. Évite les ajouts globaux par défaut.
- Traite le code, les exemples, les tests, les logs, les captures et les messages de commit comme publics. N’y inclus jamais de jetons, clés, mots de passe, fichiers d’environnement réels, adresses, numéros personnels, SMS privés, vidéos ou voix privées, ni de données sans droit de redistribution. N’affiche pas de valeur secrète lors d’une vérification.
- Mets `.gitignore` à jour dès que le projet crée de nouveaux fichiers locaux, privés ou générés. Vérifie les règles avec `git check-ignore` et contrôle séparément les fichiers déjà suivis. Git continue de suivre un fichier déjà indexé même après son ajout à `.gitignore`. N’utilise pas `git add -f` pour y faire entrer un fichier sensible.
- Si tu repères un secret dans l’index, retire-le de l’index en préservant le fichier local. S’il a déjà été commité, préviens le responsable sans recopier la valeur. Fais révoquer ou renouveler un identifiant exposé et coordonne le nettoyage de l’historique si nécessaire.

## Collaboration et passage de relais

- Lis [PLAN.md](PLAN.md) avant de commencer et identifie le lot concerné. Adam, avec Claude, porte le backend et l’écran de surveillance Windows. Abdoullah porte la page Réglages, le module SMS et OpenRouter, la page de présentation, la préparation du corpus et les autres livrables prévus par le plan. Si une tâche touche les deux lots, répartissez les changements et vérifiez qui possède chaque partie avant de modifier les fichiers communs.
- Avant de modifier un fichier ou de faire un commit, vérifie la branche avec `git branch --show-current`. Si le résultat est `main` ou si aucune branche n’est active, crée ou rejoins une branche de travail. Ne développe et ne commite jamais directement sur `main`. Intègre les changements sur `main` par une PR, sauf exception explicitement demandée pour la publication d’un changement déjà préparé sur une branche.
- Nomme ta branche `plan/<collaborateur>/<tache>`. Exemples pour commencer un lot : `plan/adam/demarrage` et `plan/abdoullah0055/demarrage`. Utilise le nom d’utilisateur GitHub lorsqu’il est connu. Choisis ensuite un nom qui décrit la tâche, comme `plan/abdoullah0055/sms-twilio`.
- Ouvre une PR ciblée. Indique le lot, les changements, les dépendances et les vérifications. Fais relire par l’autre personne les changements qui touchent son lot ou le contrat commun. Après mise à jour avec la branche principale, relance les vérifications de l’intégration concernée.
- Coordonnez les changements de contrat avant leur intégration. Une signature, un format de données, un état ou une variable de configuration partagée doit avoir le même sens des deux côtés. Quand un changement est incompatible, documente la migration et l’ordre de fusion. Fournis un simulateur ou des exemples factices quand ils permettent à l’autre personne d’avancer en parallèle.
- Pour chaque élément que l’autre personne doit relier, laisse un **passage de relais** dans la PR et un lien vers la documentation versionnée du contrat. Indique la personne responsable de la suite et distingue ce qui a été vérifié de ce qui reste à brancher. Le passage de relais doit permettre de reproduire le branchement sans avoir accès à la conversation d’origine.

Pour un passage de relais, donne les informations suivantes lorsqu’elles s’appliquent.

| Information | Détail attendu |
| --- | --- |
| Point de branchement | Chemin du fichier, nom et signature de la fonction ou de l’événement, et endroit exact où l’appeler. |
| Données échangées | Exemple factice d’entrée et de sortie, champs obligatoires, unités, états possibles et erreurs à traiter. |
| Démarrage | Dépendances, noms des variables dans `.env.example`, commandes à exécuter et ordre d’intégration si une autre PR est requise. |
| Vérification | Commande ou étapes de recette, résultat attendu et limite connue. Pour les SMS, décris d’abord la recette en mode simulé. |

Par exemple, pour le module SMS, Abdoullah documente comment le moteur d’incident d’Adam appelle l’adaptateur simulé avec un identifiant fictif, quels états l’adaptateur renvoie et comment injecter une réponse de test. Adam vérifie ensuite que l’écran affiche la réponse pour le bon incident et note le résultat dans la PR.

## Périmètre et responsabilités du code

Le premier parcours utilise Python, OpenCV et MediaPipe Pose sous Windows, avec une webcam fixe et jusqu’à deux personnes suivies. Une chute probable déclenche une synthèse vocale française locale sur le PC. Le système prévient un proche par SMS Twilio si l’alerte reste active à la fin du délai.

| Partie | Responsabilité |
| --- | --- |
| Capture | Lire la webcam ou un MP4, horodater les images et distinguer une perte de caméra d’une fin de fichier. |
| Pose et détection | Évaluer la visibilité des points, calculer les indices de mouvement et de posture et produire des observations au fil du temps. |
| Gestion d’incident | Décider des transitions, gérer le décompte, l’annulation, le réarmement et la décision d’envoyer. |
| Voix et SMS | Exécuter les actions décidées, suivre la livraison et récupérer les réponses. Le fournisseur SMS reçoit uniquement les données utiles à la conversation. |
| Interprétation des réponses | Extraire la prise en charge et le délai annoncé à partir du texte du proche. |
| Interface et historique | Afficher la posture actuelle, l’alerte active, la réponse du proche et le journal de détection. Transmettre les actions de l’utilisateur et consigner les événements utiles localement. |

Garde ces responsabilités distinctes sans multiplier les couches. Le moteur de détection et le cycle d’incident doivent pouvoir être exercés avec des observations, une horloge et des sorties de test. L’inférence, la synthèse vocale et les appels réseau ne doivent pas bloquer l’interface ni le décompte.

## Détection et états d’incident

- Évalue une transition sur plusieurs observations. Combine la descente des épaules ou des hanches, le basculement du torse et la persistance d’une posture basse. Prends en compte la visibilité des points et le cadrage. Une seule image, un cadre horizontal ou une posture allongée ne suffisent pas à ouvrir un incident.
- Calcule le mouvement selon le temps écoulé et l’échelle du corps. Utilise les horodatages du fichier pour analyser un MP4 et une horloge monotone en direct. Gère les images irrégulières et les interruptions sans estimer une vitesse à travers une longue perte de pose. Réserve l’heure civile à l’affichage et à l’historique.
- Préserve les cas de chute depuis une chaise et d’affaissement lent. Les zones où l’on s’allonge normalement aident à interpréter une posture. Vérifie qu’une chute vers le sol à côté d’un lit ou d’un canapé reste détectable.
- Suis séparément jusqu’à deux trajectoires pour distinguer la chute individuelle, la collision probable et la cause indéterminée. La présence d’un témoin ou le chevauchement à l’image ne suffit pas pour classer une collision. Conserve l’alerte si la chute est détectée et que la cause reste indéterminée. Une collision ouvre au plus un incident de chute, même si les deux personnes tombent. Dans ce cas, exige une reprise fiable pour chacune avant d’annuler ou de clore automatiquement.
- Attribue un identifiant d’incident au moment où la chute probable est confirmée. Il reste stable si un identifiant de suivi change. Une pose masquée ou une caméra indisponible est un état distinct d’une reprise de posture.
- Démarre une fois le décompte de **10 secondes par défaut**, réglable. Il continue si la caméra se coupe après confirmation. Hors incident, une panne caméra produit un statut de surveillance indisponible. Fermer la fenêtre d’alerte ne vaut pas annulation.
- Sérialise la décision d’envoyer et celle d’annuler dans le moteur d’incident. Si une annulation manuelle ou une reprise de posture fiable et soutenue est enregistrée avant l’échéance et avant la soumission, bloque l’envoi. Lorsque la soumission a commencé, expose l’état réel du SMS et permets la clôture locale.
- Garde le même incident tant que la personne reste au sol. Une fois l’incident traité, une reprise de posture fiable ou une clôture manuelle le termine. Après une clôture ou une annulation, le réarmement exige une posture de départ exploitable suivie d’une nouvelle transition pour déclencher une autre alerte.

## Voix et SMS

- Prononce l’alerte une seule fois en français avec eSpeak NG, localement et sans service distant. Le test sonore agit uniquement sur l’audio. Si le moteur, la voix ou le son est indisponible, affiche **Voix indisponible** et poursuis le décompte. Si le SMS échoue, l’alerte locale reste disponible.
- Fais de **Simulé** le mode par défaut. La lecture MP4, la simulation d’incident et les tests automatisés restent sans envoi réel, même si des identifiants Twilio existent. Applique cette règle à la frontière d’envoi et vérifie-la dans les tests. Le mode réel exige une activation explicite et une configuration valide.
- N’envoie jamais de SMS réel depuis un outil de développement ou une recette d’agent sans demande explicite pour cet envoi et destinataire convenu. Dans l’application, le bouton de test SMS réel est distinct de la simulation et du test sonore.
- Soumets une seule fois par incident et par destinataire. Enregistre durablement l’intention d’envoi avant l’appel réseau pour qu’un autre traitement ne puisse pas le répéter. En cas de délai réseau dépassé ou de redémarrage laissant un doute après cette étape, conserve l’état incertain et évite un renvoi automatique. Enregistre l’identifiant fournisseur dès qu’il est disponible et utilise-le pour consulter le statut.
- Distingue **Simulé**, **En attente**, **Livré**, **Échec** et **Livraison inconnue**. Une requête acceptée ou un statut `sent` laisse la livraison à confirmer. Seul un retour explicite de livraison autorise **Livré**. Pour le SMS sortant, suis le modèle de `PRODUCT.md` avec le prénom, l’heure locale de l’incident en `HH:mm` et l’adresse configurée.

## Réponses SMS et interprétation par l’IA

- Pour le prototype Windows, privilégie la lecture périodique de l’API Messages Twilio en arrière-plan. Filtre les messages entrants par contact, numéro Twilio et date, puis déduplique par identifiant de message. Si un webhook est ajouté, valide `X-Twilio-Signature` avec le SDK avant de traiter le contenu. Garde la réception distincte des statuts de livraison.
- Associe la réponse dans le moteur d’incident avant d’appeler l’IA. Vérifie l’expéditeur configuré, le destinataire, l’heure réelle du message et l’existence d’une alerte soumise. Les réponses en retard ou ambiguës ne doivent pas être affectées automatiquement à un autre incident. Une réponse plus ancienne reçue hors ordre ne remplace pas une mise à jour plus récente.
- Utilise le modèle configuré via OpenRouter pour interpréter les réponses. Documente les données transmises et les options dans `.env.example` et garde la clé dans la configuration locale hors de Git. Si OpenRouter est indisponible, conserve **Réponse reçue, à vérifier**.
- Traite le SMS entrant comme du texte externe. Donne au modèle uniquement le texte à interpréter, sans numéros ni adresse de configuration. Valide une sortie structurée composée d’un état autorisé et d’un délai optionnel explicitement annoncé. Les questions, les négations, les messages contradictoires et les échecs d’analyse doivent être traités. Si un modèle distant est retenu, documente les données envoyées à ce fournisseur.
- Affiche sur l’écran de détection l’état du proche, le délai annoncé s’il est connu et l’heure de réception. En cas de lecture ou d’interprétation indisponible, indique-le à l’écran. Un SMS entrant ne ferme pas l’incident et ne modifie pas la détection ni la voix locale.

## Écran de surveillance et logs

- Suis la disposition de `PRODUCT.md`. Garde la prédiction de posture en haut de la colonne droite et les logs en bas. Le panneau d’alerte au milieu apparaît uniquement pendant un incident avec une alerte active. Quand il se replie, les logs récupèrent sa place.
- Affiche la posture fournie par le moteur de détection. Les libellés de posture comme `STANDING`, `WALKING` et `FALLING` reprennent les vidéos de référence. Affiche un état explicite quand la posture est indéterminée ou qu’aucune personne n’est détectée. La prédiction de posture et l’état de l’incident restent indépendants.
- Désigne les personnes suivies par **Personne**, ou **Personne 1** et **Personne 2**, sur l’écran de surveillance. Réserve le prénom configuré au champ de configuration et au SMS quand son attribution est fiable.
- Horodate les entrées du journal de détection et garde leur ordre. En mode MP4, indique aussi le temps dans le fichier pour les événements associés aux images. Branche **Tout copier** sur le journal filtré des données sensibles de la session entière, indépendamment des lignes visibles. Affiche une confirmation après écriture dans le presse-papiers.

## Données locales

- Traite les images et la pose en mémoire. Par défaut, l’historique conserve les événements, les transitions, les erreurs utiles et le statut de livraison. Il doit pouvoir être effacé localement.
- Garde les vidéos privées, les enregistrements personnels, les numéros complets et les secrets hors du dépôt. Utilise des valeurs factices dans les tests et exemples. Masque les numéros et les secrets dans les logs, y compris les messages d’erreur du fournisseur.
- Ne lis, n’affiche et ne partage jamais les valeurs des fichiers `.env` ou les secrets du poste. Si une variable d’environnement est ajoutée ou renommée, documente son nom, son rôle et son caractère obligatoire dans `.env.example`, avec une valeur vide ou factice. Vérifie que les fichiers locaux contenant des secrets sont ignorés par Git.
- N’ajoute pas de télémétrie, de reconnaissance faciale, de commande par microphone ni de transfert d’images sans demande produit explicite. Les cinq vidéos de référence guident la conception. Utilise des fichiers locaux autorisés pour les essais et garde leurs chemins propres au poste dans la configuration.

## Vérification avant de livrer

Avant de modifier un comportement, identifie le scénario de `PRODUCT.md` correspondant. Pour une modification de la logique d’alerte, couvre les transitions concernées avec une horloge contrôlable et un adaptateur SMS simulé. Les tests ne doivent dépendre ni d’un périphérique, ni d’Internet, ni d’un délai réel de dix secondes.

| Partie touchée | Vérification attendue |
| --- | --- |
| Détection ou seuils | Tester les chutes debout, assises et lentes, les gestes ordinaires et la perte de pose. Vérifier aussi la chute avec témoin, les collisions avec ou sans chute, le croisement sans contact et le contact incertain sur des séquences indépendantes. |
| Décompte, annulation ou réarmement | Tester l’échéance, l’annulation juste avant et après la décision d’envoi, la perte caméra après confirmation, le changement de suivi, le maintien au sol et la chute suivante. Vérifier qu’aucun doublon n’est créé. |
| Voix, SMS ou configuration | Vérifier la voix locale hors ligne, le moteur de synthèse indisponible, un SMS refusé, un résultat réseau inconnu et une confirmation de livraison. Vérifier que le mode simulation reste isolé du fournisseur, avec une configuration SMS valide factice. |
| Réponse SMS et IA | Simuler un trajet avec et sans délai, un « OK » ambigu, une négation, une correction, un échec du modèle, un expéditeur inattendu, un doublon et une réponse tardive. Vérifier l’association à l’incident et la notification. Le test sur téléphone couvre aussi le SMS retour. |
| Interface ou périphériques | Vérifier la prédiction affichée, le repli du panneau d’alerte au repos et la copie des logs horodatés hors écran. Vérifier que les actions restent visibles pendant le décompte et les pannes. La recette sur le poste cible confirme le cadrage, la voix audible et, si l’envoi a été autorisé, la réception sur le téléphone convenu. |

Sur la vision, relève les chutes manquées, les fausses alertes par heure et le délai entre le contact au sol et le déclenchement de la voix. Suis séparément la soumission et la livraison des SMS. Utilise des vidéos ou des mises en scène sécurisées pour les chutes de recette. Un test simulé valide le comportement exercé par le test. La reconnaissance dans la pièce, l’audibilité et la réception sur téléphone exigent des vérifications distinctes.

Avant de conclure, relis le diff, lance les vérifications applicables et indique celles qui ont réellement été exécutées. Si un test exige un périphérique, un fichier ou un service indisponible, précise ce qui reste à vérifier. Liste les noms des variables d’environnement ajoutées ou renommées quand la modification en introduit.
