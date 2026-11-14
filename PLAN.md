# FAAALL-ALARM — plan de réalisation

Le projet se divise en deux lots. **Adam, avec Claude, réalise le backend et l’interface de l’application Windows. Abdoullah réalise le système SMS, la page de présentation et prépare les données d’entraînement et de validation.** Abdoullah prend aussi en charge les livrables de [PRODUCT.md](PRODUCT.md) qui ne relèvent pas du lot d’Adam.

[PRODUCT.md](PRODUCT.md) définit le comportement attendu. [AGENTS.md](AGENTS.md) guide les agents qui modifient le dépôt. Le plan attribue les tâches et fixe l’ordre d’intégration.

## Lot 1 — Adam avec Claude

- [ ] **Application Windows et vision.** Créer le point d’entrée Python et documenter l’installation sous Windows avec PowerShell. Brancher OpenCV pour la webcam et les MP4, puis MediaPipe Pose. Calculer les indices de posture et de mouvement sur des images horodatées. Gérer l’absence de personne, les occultations, les pertes de caméra et la fin d’un fichier.
- [ ] **Détection et incidents.** Classer la posture actuelle et détecter la transition vers une chute probable depuis une position debout ou assise, y compris lors d’un affaissement lent. Gérer l’identifiant d’incident, le compte à rebours réglable de 10 secondes par défaut, l’annulation, la clôture et le réarmement. Le moteur décide si un SMS peut partir.
- [ ] **Voix locale.** Jouer une fois le WAV français fourni par Abdoullah, dès l’ouverture de l’alerte. Ajouter le test sonore et signaler un fichier indisponible sans interrompre le décompte.
- [ ] **Interface de surveillance.** Afficher la caméra en haut à gauche, les états caméra et SMS en texte simple au-dessus de la vidéo et les commandes en dessous. Dans la colonne droite, afficher la posture en haut, l’alerte et les détails SMS au milieu pendant un incident, puis les logs en bas. Au repos, les logs récupèrent l’espace du panneau d’alerte. **Tout copier** copie le journal horodaté de la session, y compris les lignes hors écran.
- [ ] **Réglages et historique.** Ajouter les réglages de la caméra, de la zone surveillée, de la voix et du décompte, ainsi que les champs et actions requis par le module SMS. Enregistrer les événements utiles et permettre d’effacer l’historique. L’interface consomme les états du moteur et du module SMS.
- [ ] **Tests et intégration.** Tester le moteur avec une horloge contrôlable et des observations simulées. Brancher l’adaptateur SMS simulé fourni par Abdoullah, puis l’adaptateur Twilio. Vérifier que la réception des réponses et les appels réseau n’interrompent ni la capture ni l’interface.

**Livrable du lot.** Une application Windows lançable selon le README, capable de lire la webcam ou un MP4, de détecter un scénario de chute, de diffuser la voix et de montrer le parcours SMS simulé jusqu’à la réponse du proche.

## Lot 2 — Abdoullah

- [ ] **SMS bidirectionnels.** Fournir un adaptateur simulé, puis intégrer Twilio pour l’envoi, le suivi de livraison et la lecture des réponses en arrière-plan. Produire le message prévu dans `PRODUCT.md` avec le prénom, l’heure locale et l’adresse. Gérer la disponibilité du service, les doublons, les résultats réseau incertains et la déduplication des réponses. Documenter la configuration avec des valeurs factices dans `.env.example`.
- [ ] **Interprétation de la réponse.** Choisir le modèle de langage et lui transmettre uniquement le texte à interpréter. Fournir à Adam un état validé et un délai annoncé s’il est explicite. Couvrir l’absence de délai, les « OK » ambigus, les refus, les corrections et les échecs d’analyse. Documenter les données transmises si le modèle est distant.
- [ ] **Données d’entraînement et de validation.** Trouver et documenter des jeux de données autorisés qui couvrent les chutes debout, assises et lentes ainsi que les gestes ordinaires. Préparer un inventaire avec source, licence, classes, conditions de prise de vue et intervalles annotés. Pour les chutes, annoter le début du mouvement et le contact au sol lorsque ces instants sont visibles. Séparer l’entraînement, la validation et le test par personne lorsque les identifiants le permettent, sinon par séquence d’origine. Remettre à Adam les données ou les modalités d’accès et les annotations. Garder les médias privés hors de Git.
- [ ] **Page de présentation.** Créer une page web en français qui présente le public visé, la détection locale, la voix et le parcours de réponse SMS. Utiliser des exemples fictifs. Décrire le produit comme un dispositif d’appoint et distinguer les fonctions démontrées des fonctions encore en développement.
- [ ] **Livrables produit restants.** Préparer l’enregistrement WAV français, les exemples fictifs de SMS et le scénario de démonstration. Tenir à jour la liste des critères de `PRODUCT.md` avec les résultats de recette. Prendre en charge les éléments restants hors backend et interface Windows, puis signaler à Adam les écarts qui touchent son lot.

**Livrable du lot.** Un module SMS simulable, un adaptateur Twilio prêt à vérifier sur téléphone, un interpréteur de réponses testé, une page de présentation, un corpus documenté et les éléments de démonstration.

## Contrat entre les deux lots

| Échange | Responsabilité |
| --- | --- |
| Demande d’alerte | Adam fournit l’identifiant d’incident, l’heure, le destinataire configuré et les données nécessaires au message. Son moteur arbitre l’annulation, puis soumet une seule demande autorisée à l’échéance. |
| Envoi et suivi | Abdoullah fournit les adaptateurs simulé et réel ainsi que les états `Simulé`, `En attente`, `Livré`, `Échec` et `Livraison inconnue`. Son module enregistre durablement l’intention avant l’appel et conserve la référence Twilio lorsqu’elle est connue. |
| Réponse du proche | Abdoullah récupère et déduplique les messages entrants. Adam vérifie le rattachement à un incident ouvert, par expéditeur, destinataire et horodatage. En cas d’ambiguïté, le moteur affiche une réponse à vérifier. Pour une réponse rattachée, Adam appelle l’interpréteur d’Abdoullah, puis affiche le résultat dans l’interface. |
| Données de vision | Abdoullah fournit le corpus et les annotations. Adam s’en sert pour calibrer le détecteur et entraîner un classificateur si l’approche retenue l’exige. Le jeu de test indépendant sert à la mesure finale. |

Au démarrage, Adam et Abdoullah fixent ensemble les signatures du module SMS et les formats de données. Adam possède le point d’entrée Windows et l’état d’incident. Abdoullah possède le module SMS, la page web et la préparation du corpus. Chaque personne peut avancer avec une simulation de l’autre lot. Les deux valident les changements au contrat avant de les intégrer.

## Jalons et recette commune

1. **Travail en parallèle.** Adam fait fonctionner le flux vidéo et les premiers états de détection. Abdoullah livre la simulation SMS et commence la préparation du corpus, de la voix et de la page de présentation.
2. **Intégration locale.** Rejouer un MP4 de chute et suivre le parcours complet. La voix démarre une fois, l’annulation empêche toute soumission, une alerte non annulée affiche un SMS simulé et la réponse « Je suis en route, environ 20 minutes » met à jour l’écran. Vérifier que le panneau d’alerte se replie à la clôture et que **Tout copier** inclut les logs hors écran.
3. **Validation du détecteur.** Rejouer les chutes debout, assises et lentes ainsi que les gestes ordinaires sur les données de test indépendantes. Relever les chutes manquées, les fausses alertes par heure et le délai entre le contact au sol et la voix. Tester aussi les pertes de suivi et l’alerte en cours lors d’une panne caméra.
4. **Recette Windows et SMS.** Vérifier le cadrage et l’audibilité sur le poste cible. Pour un essai réel explicitement demandé, utiliser le destinataire convenu et vérifier la livraison, la réponse reçue et son affichage sur le PC. Garder l’envoi simulé par défaut pendant le développement et les tests.
5. **Démonstration.** Abdoullah rassemble les résultats de recette, la page de présentation et le scénario de démo. Adam valide que l’installation et le parcours Windows fonctionnent selon le README. Les deux vérifient les critères de `PRODUCT.md` et consignent les points encore ouverts.
