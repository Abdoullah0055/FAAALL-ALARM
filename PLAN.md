# FAAALL-ALARM — plan de réalisation

Le projet se divise en deux lots. **Adam, avec Claude, réalise le backend et l’interface de surveillance Windows. Abdoullah réalise la page Réglages de l’application, le système SMS avec OpenRouter pour interpréter les réponses, la page de présentation et la préparation des données.** Abdoullah prend aussi en charge les livrables de [PRODUCT.md](PRODUCT.md) qui ne relèvent pas du lot d’Adam.

[PRODUCT.md](PRODUCT.md) définit le comportement attendu. [AGENTS.md](AGENTS.md) guide les agents qui modifient le dépôt. Le plan attribue les tâches et fixe l’ordre d’intégration.

Avant toute modification, vérifiez votre branche avec `git branch --show-current`. Pour démarrer depuis `origin/main` après un `git fetch origin`, Adam crée sa branche avec `git switch -c plan/adam/demarrage origin/main` et Abdoullah avec `git switch -c plan/abdoullah0055/demarrage origin/main`. Si la branche de la tâche existe déjà, rejoignez-la. Les commits se font sur les branches de travail et les PR les intègrent à `main`.

## Lot 1 — Adam avec Claude

- [ ] **Application Windows et vision.** Créer le point d’entrée Python et documenter l’installation sous Windows avec PowerShell. Brancher OpenCV pour la webcam et les MP4, puis MediaPipe Pose. Calculer les indices de posture et de mouvement sur des images horodatées. Gérer l’absence de personne, les occultations, les pertes de caméra et la fin d’un fichier.
- [ ] **Détection et incidents.** Classer la posture actuelle et détecter les chutes, y compris les affaissements lents. Suivre jusqu’à deux personnes sur plusieurs images pour distinguer une chute individuelle d’une collision entre deux personnes, avec ou sans chute. Exporter cette distinction vers l’interface et le journal. Gérer l’identifiant d’incident, le compte à rebours de 10 secondes par défaut, l’annulation, la clôture et le réarmement. Le moteur décide si un SMS peut partir.
- [ ] **Voix locale.** Intégrer eSpeak NG comme moteur de synthèse vocale française gratuit, hors ligne et non neuronal. Prononcer le message une fois à l’ouverture de l’alerte. Fournir le test sonore et signaler tout échec sans interrompre le décompte. Vérifier l’installation et l’intelligibilité sur le poste cible.
- [ ] **Interface de surveillance.** Afficher la caméra en haut à gauche, les états caméra et SMS en texte simple au-dessus de la vidéo et les commandes en dessous. Dans la colonne droite, afficher la posture en haut, l’alerte et les détails SMS au milieu pendant un incident, puis les logs en bas. Au repos, les logs récupèrent l’espace du panneau d’alerte. **Tout copier** copie le journal horodaté de la session, y compris les lignes hors écran.
- [ ] **Configuration et historique.** Fournir les fonctions de lecture, de validation et d’application des réglages dont la page d’Abdoullah a besoin. Brancher le bouton **Réglages** de l’écran de surveillance sur sa page. Enregistrer les événements utiles et permettre d’effacer l’historique. Sur l’écran de surveillance, utiliser **Personne** ou **Personne 1** et **Personne 2**, selon le nombre de personnes suivies.
- [ ] **Tests et intégration.** Tester le moteur avec une horloge contrôlable et des observations simulées. Brancher l’adaptateur SMS simulé fourni par Abdoullah, puis l’adaptateur Twilio. Vérifier que la réception des réponses et les appels réseau n’interrompent ni la capture ni l’interface.

**Livrable du lot.** Une application Windows lançable selon le README, capable de lire la webcam ou un MP4, de détecter un scénario de chute, de diffuser la voix et de montrer le parcours SMS simulé jusqu’à la réponse du proche.

## Lot 2 — Abdoullah

- [ ] **SMS bidirectionnels.** Fournir un adaptateur simulé, puis intégrer Twilio pour l’envoi, le suivi de livraison et la lecture des réponses en arrière-plan. Produire le message prévu dans `PRODUCT.md` avec le prénom, l’heure locale et l’adresse. Gérer la disponibilité du service, les doublons, les résultats réseau incertains et la déduplication des réponses. Documenter la configuration avec des valeurs factices dans `.env.example`.
- [ ] **Interprétation de la réponse via OpenRouter.** Choisir un modèle compatible avec la sortie structurée. Lui envoyer le texte utile de la réponse et fournir à Adam un état validé et un délai annoncé s’il est explicite. Tester les réponses ambiguës, négatives et correctives, ainsi que l’échec du service. Documenter les données transmises et la configuration dans `.env.example`.
- [ ] **Page Réglages.** Créer la page Windows et ses contrôles pour la caméra, la zone surveillée, la voix, le décompte et la configuration SMS. Relier la page aux fonctions exposées par Adam pour charger, valider et appliquer la configuration. Afficher les erreurs de saisie et brancher les actions de test.
- [ ] **Données d’entraînement et de validation.** Trouver et documenter des jeux de données autorisés qui couvrent les chutes debout, assises et lentes, les gestes ordinaires, les collisions à deux ainsi que les croisements ou rapprochements sans collision. Annoter les personnes impliquées et les instants visibles de contact entre personnes, de début de chute et de contact au sol. Tenir un inventaire avec les sources et licences. Séparer les jeux par personne lorsque les identifiants le permettent, sinon par séquence d’origine. Remettre à Adam les données ou les modalités d’accès et les annotations. Garder les médias privés hors de Git.
- [ ] **Page de présentation.** Créer une page web en français qui présente le public visé, la détection locale, la voix et le parcours de réponse SMS. Utiliser des exemples fictifs. Décrire le produit comme un dispositif d’appoint et distinguer les fonctions démontrées des fonctions encore en développement.
- [ ] **Livrables produit restants.** Préparer les exemples fictifs de SMS et le scénario de démonstration. Tenir à jour les critères de `PRODUCT.md` avec les résultats de recette et signaler à Adam les écarts qui touchent son lot.

**Livrable du lot.** Un module SMS simulable, un adaptateur Twilio prêt à vérifier sur téléphone, un interpréteur OpenRouter testé, la page Réglages, une page de présentation, un corpus documenté et les éléments de démonstration.

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
