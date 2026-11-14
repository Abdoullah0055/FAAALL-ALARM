# FAAALL-ALARM — plan de réalisation

Le projet se divise en deux lots. **Adam, avec Claude, réalise le backend et l’interface de surveillance Windows. Abdoullah réalise la page Réglages de l’application, le système SMS avec OpenAI pour interpréter les réponses, la page de présentation et la préparation des données.** Abdoullah prend aussi en charge les livrables de [PRODUCT.md](PRODUCT.md) qui ne relèvent pas du lot d’Adam.

[PRODUCT.md](PRODUCT.md) définit le comportement attendu. [AGENTS.md](AGENTS.md) guide les agents qui modifient le dépôt. Le plan attribue les tâches et fixe l’ordre d’intégration.

Avant toute modification, vérifiez votre branche avec `git branch --show-current`. Pour démarrer depuis `origin/main` après un `git fetch origin`, Adam crée sa branche avec `git switch -c plan/adam/demarrage origin/main` et Abdoullah avec `git switch -c plan/abdoullah0055/demarrage origin/main`. Si la branche de la tâche existe déjà, rejoignez-la. Les commits se font sur les branches de travail et les PR les intègrent à `main`.

## Méthode de travail commune

Écrivez du code lisible et maintenable, avec des responsabilités claires et des vérifications ciblées. Appliquez les règles de [qualité du code](AGENTS.md#qualité-du-code) et utilisez les [skills du projet](AGENTS.md#skills-du-projet) au moment indiqué.

- **Adam et Abdoullah.** Utilisez `systematic-debugging` dès qu’un résultat diverge du comportement attendu, notamment pour les chutes, les collisions, le décompte et les SMS. Utilisez `verification-before-completion` avant chaque livraison et pendant la recette commune. Consignez la différence entre test simulé, vérification par MP4 et essai sur le poste cible.
- **Abdoullah.** Utilise `frontend-design` au moment de concevoir ou de revoir la page de présentation. La page Réglages et l’écran de surveillance suivent `PRODUCT.md` et le contrat entre les deux lots.

## Lot 1 — Adam avec Claude

- [x] **Application Windows et vision.** Créer le point d’entrée Python et documenter l’installation sous Windows avec PowerShell. Brancher OpenCV pour la webcam et les MP4, puis MediaPipe Pose. Calculer les indices de posture et de mouvement sur des images horodatées. Gérer l’absence de personne, les occultations, les pertes de caméra et la fin d’un fichier.
  *Fait et vérifié (2026-11-14) :* `python -m faaall_alarm`, installation PowerShell, webcam (22 images/s avec MediaPipe sur le poste de développement), MP4 avec les temps du fichier, fin de fichier distincte d’une perte de caméra, absence de personne, occultation. *Limite :* les seuils sont calibrés sur des poses simulées, pas sur la webcam cible.
- [x] **Détection et incidents.** Classer la posture actuelle et détecter les chutes, y compris les affaissements lents. Suivre jusqu’à deux personnes sur plusieurs images pour distinguer une chute individuelle d’une collision entre deux personnes, avec ou sans chute. Exporter cette distinction vers l’interface et le journal. Gérer l’identifiant d’incident, le compte à rebours de 10 secondes par défaut, l’annulation, la clôture et le réarmement. Le moteur décide si un SMS peut partir.
  *Fait et vérifié par des tests sur séquences simulées :* chutes debout, assise et lente, collision avec ou sans chute, croisement, contact incertain (« Cause indéterminée »), un seul incident même si les deux tombent, annulation, clôture, réarmement, perte de suivi et de caméra. *À mesurer :* chutes manquées, fausses alertes par heure et délai contact-voix sur le corpus d’Abdoullah et la webcam cible.
- [ ] **Voix locale.** Intégrer eSpeak NG comme moteur de synthèse vocale française gratuit, hors ligne et non neuronal. Prononcer le message une fois à l’ouverture de l’alerte. Fournir le test sonore et signaler tout échec sans interrompre le décompte. Vérifier l’installation et l’intelligibilité sur le poste cible.
  *Fait :* eSpeak NG 1.52.0 intégré, voix prononcée une fois à l’ouverture, « Voix indisponible » sans interrompre le décompte, synthèse hors ligne vérifiée. *Reste sur le poste cible :* intelligibilité et volume audibles depuis la zone surveillée.
- [x] **Interface de surveillance.** Afficher la caméra en haut à gauche, les états caméra et SMS en texte simple au-dessus de la vidéo et les commandes en dessous. Dans la colonne droite, afficher la posture en haut, l’alerte et les détails SMS au milieu pendant un incident, puis les logs en bas. Au repos, les logs récupèrent l’espace du panneau d’alerte. **Tout copier** copie le journal horodaté de la session, y compris les lignes hors écran.
  *Fait et vérifié sur captures de la fenêtre en démo :* schéma de PRODUCT.md, panneau d’alerte replié au repos, logs récupérant l’espace, **Tout copier** de toute la session (testé au-delà de l’écran), personnes nommées **Personne**, **Personne 1** et **Personne 2**.
- [x] **Configuration et historique.** Fournir les fonctions de lecture, de validation et d’application des réglages dont la page d’Abdoullah a besoin. Brancher le bouton **Réglages** de l’écran de surveillance sur sa page. Enregistrer les événements utiles et permettre d’effacer l’historique. Sur l’écran de surveillance, utiliser **Personne** ou **Personne 1** et **Personne 2**, selon le nombre de personnes suivies.
  *Fait et vérifié :* `read_config`, `validate_config`, `apply_config`, historique local avec effacement, bouton **Réglages** relié à la page d’Abdoullah par `AppSettingsBackend` (voir [docs/RACCORDEMENT.md](docs/RACCORDEMENT.md)).
- [x] **Tests et intégration.** Tester le moteur avec une horloge contrôlable et des observations simulées. Brancher l’adaptateur SMS simulé fourni par Abdoullah, puis l’adaptateur Twilio. Vérifier que la réception des réponses et les appels réseau n’interrompent ni la capture ni l’interface.
  *Fait :* moteur testé avec horloge contrôlable, `SmsService` et `OpenAI` du lot 2 branchés (simulé, puis chemin réel avec transports factices), frontière d’envoi réel testée. Les appels réseau, la lecture des réponses et l’interprétation tournent sur des fils de travail dédiés, sans chevauchement de sondages : vérifié avec une passerelle volontairement bloquée (`tests/test_non_blocking.py`), l’analyse des images et le décompte continuent. *Reste :* essai réel Twilio et OpenAI sur le poste cible (voir ci-dessous).

**Livrable du lot.** Une application Windows lançable selon le README, capable de lire la webcam ou un MP4, de détecter un scénario de chute, de diffuser la voix et de montrer le parcours SMS simulé jusqu’à la réponse du proche.

**État du lot (V1).** Le livrable est atteint et les deux lots sont raccordés ([docs/RACCORDEMENT.md](docs/RACCORDEMENT.md), 169 tests) : `.\scripts\demo.ps1` rejoue un MP4, déclenche la voix, affiche le décompte puis simule le SMS et la réponse « Je suis en route, j’arrive dans environ 20 minutes ». Tout ce qui se réalise et se vérifie dans le dépôt est terminé. Il reste quatre vérifications qui exigent le poste cible, un corpus indépendant ou l’accord du proche : elles gardent **Voix locale** ouverte.

- [ ] Voix audible et intelligible depuis la zone surveillée (poste cible).
- [ ] Cadrage de la webcam et calibration des seuils de `DetectionThresholds` (poste cible).
- [ ] Mesure sur des données indépendantes : chutes manquées, fausses alertes par heure, délai contact-voix (dépend du corpus du lot 2).
- [ ] Essai réel Twilio et OpenAI avec le téléphone convenu : envoi, livraison, réponse affichée (accord explicite du destinataire).

## Lot 2 — Abdoullah

- [x] **SMS bidirectionnels.** Adaptateurs simulé et Twilio, format de l’alerte, suivi, lecture des réponses et reprise sans renvoi. Vérifié avec transport factice. La recette téléphone reste ouverte.
- [x] **Interprétation OpenAI.** Modèle configurable, données documentées dans `.env.example`, sortie validée, fallback de panne et cas de recette testés avec transport factice. L’appel réel reste à vérifier.
- [x] **Page Réglages autonome.** Contrôles, validation, persistance, aperçu fictif, test sonore et actions SMS. Recette Tk Windows effectuée en simulation.
- [x] **Branchement des Réglages et des SMS à la surveillance d’Adam.** Appliqué selon le [contrat d’intégration](docs/integration-lot-2.md) : voir [docs/RACCORDEMENT.md](docs/RACCORDEMENT.md). L’essai réel sur téléphone reste ouvert.
- [ ] **Données d’entraînement et de validation.** Trouver et documenter des jeux de données autorisés qui couvrent les chutes debout, assises et lentes, les gestes ordinaires, les collisions à deux ainsi que les croisements ou rapprochements sans collision. Annoter les personnes impliquées et les instants visibles de contact entre personnes, de début de chute et de contact au sol. Tenir un inventaire avec les sources et licences. Séparer les jeux par personne lorsque les identifiants le permettent, sinon par séquence d’origine. Remettre à Adam les données ou les modalités d’accès et les annotations. Garder les médias privés hors de Git.
  - [x] [Inventaire et accès](docs/corpus.md), avec trois annotations exploratoires sur séquences UR Fall.
  - [ ] Séquences indépendantes annotées pour les affaissements lents, collisions et croisements à deux.
- [x] **Page de présentation.** Page française et exemple interactif avec données fictives. Simulation vérifiée dans Chromium au clavier et aux largeurs de 320 à 1 280 px.
- [x] **Livrables de démo.** Scénario Windows et terminal reproductible, [résultats de recette produit](PRODUCT.md#résultats-de-recette-du-lot-2) et passage de relais.

**Livrable du lot.** Un module SMS simulable, un adaptateur Twilio prêt à vérifier sur téléphone, un interpréteur OpenAI testé, la page Réglages, une page de présentation, un corpus documenté et les éléments de démonstration.

## Contrat entre les deux lots

| Échange | Responsabilité |
| --- | --- |
| Demande d’alerte | Adam fournit l’identifiant d’incident, l’heure, le destinataire configuré et les données nécessaires au message. Son moteur arbitre l’annulation, puis soumet une seule demande autorisée à l’échéance. |
| Envoi et suivi | Abdoullah fournit les adaptateurs simulé et réel ainsi que les états `Simulé`, `En attente`, `Livré`, `Échec` et `Livraison inconnue`. Son module enregistre durablement l’intention avant l’appel et conserve la référence Twilio lorsqu’elle est connue. |
| Réponse du proche | Abdoullah récupère et déduplique les messages entrants. Adam vérifie le rattachement à un incident ouvert, par expéditeur, destinataire et horodatage. En cas d’ambiguïté, le moteur affiche une réponse à vérifier. Pour une réponse rattachée, Adam appelle l’interpréteur d’Abdoullah, puis affiche le résultat dans l’interface. |
| Page Réglages | Adam documente les fonctions et les erreurs de validation de la configuration. Abdoullah réalise la page et son branchement à ces fonctions. Adam relie le bouton **Réglages** à la page. |
| Données de vision | Abdoullah fournit le corpus et les annotations. Adam s’en sert pour calibrer le détecteur et entraîner un classificateur si l’approche retenue l’exige. Le jeu de test indépendant sert à la mesure finale. |

Au démarrage, fixez les signatures du module SMS et de la configuration, ainsi que les formats de données. Adam possède le point d’entrée Windows et l’état d’incident. Abdoullah possède le module SMS, la page Réglages, la page web et la préparation du corpus. Chacun peut avancer avec une simulation de l’autre lot. Validez ensemble les changements au contrat avant de les intégrer.

## Jalons et recette commune

1. **Travail en parallèle.** Adam branche le flux vidéo, les premiers états de détection et la synthèse vocale locale. Abdoullah livre la simulation SMS et commence la page Réglages, le corpus et la page de présentation.
2. **Intégration locale.** Rejouer un MP4 de chute et suivre le parcours complet. La voix démarre une fois, l’annulation empêche toute soumission, une alerte non annulée affiche un SMS simulé et la réponse « Je suis en route, environ 20 minutes » met à jour l’écran. Vérifier que le panneau d’alerte se replie à la clôture et que **Tout copier** inclut les logs hors écran.
3. **Validation du détecteur.** Sur des données indépendantes, comparer les chutes individuelles, les collisions avec ou sans chute, les chutes avec un témoin à proximité et les croisements sans collision. Relever les confusions entre ces cas, les chutes manquées, les fausses alertes par heure et le délai entre le contact au sol et la voix. Tester aussi les pertes de suivi et l’alerte en cours lors d’une panne caméra.
4. **Recette Windows et SMS.** Vérifier le cadrage et l’audibilité sur le poste cible. Pour un essai réel explicitement demandé, utiliser le destinataire convenu et vérifier la livraison, la réponse reçue et son affichage sur le PC. Garder l’envoi simulé par défaut pendant le développement et les tests.
5. **Démonstration.** Abdoullah rassemble les résultats de recette, la page de présentation et le scénario de démo. Adam valide que l’installation et le parcours Windows fonctionnent selon le README. Les deux vérifient les critères de `PRODUCT.md` et consignent les points encore ouverts.
