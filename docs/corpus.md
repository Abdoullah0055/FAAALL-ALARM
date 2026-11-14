# Corpus de vision pour Adam

| Source et accès | Conditions | Cas exploitables et limites |
| --- | --- | --- |
| [UR Fall Detection, université de Rzeszów](https://fenix.ur.edu.pl/mkepski/ds/uf.html), liens MP4 ou ZIP RGB et CSV temporels | [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/), usage académique non commercial. Citer Bogdan Kwolek et Michał Kepski (2014). | Chutes individuelles debout et assises, activités ordinaires. Trois clips contrôlés ci-dessous. Les MP4 de prévisualisation juxtaposent profondeur à gauche et RGB à droite. |
| [SAFER-Activities, KU Leuven et AIT](https://huggingface.co/datasets/SAFER-Activities/SAFER-Activities), connexion et acceptation des conditions. Prendre un sous-ensemble des vidéos RGB, les CSV associés et les listes `splits/`. | CC BY-NC-SA 4.0. Citer Lamsal et coll. (2026). | Chutes et gestes ordinaires en longues séquences, dont des personnes en fauteuil. Les annotations fournies bornent l’action. Relever le contact au sol séparément et vérifier quelles séquences comportent un affaissement lent. |
| [NTU RGB+D 60, ROSE Lab](https://rose1.ntu.edu.sg/dataset/actionRecognition/), demander un accès de recherche et accepter l’accord de diffusion. | Recherche académique non commerciale sous approbation. La redistribution et la constitution d’un jeu dérivé exigent une permission explicite. | A050 chute individuelle, A053 poussée, A059 rapprochement, A060 éloignement. La poussée est un exemple de contact, la classe seule ne prouve ni la collision avec chute ni son absence. Le rapprochement fournit des candidats à examiner pour les négatifs à deux. |

### Trois clips annotés

Télécharger les MP4 ci-dessous dans `datasets/`, ignoré par Git. Télécharger de préférence le ZIP RGB et le CSV de synchronisation de la même ligne pour l’analyse image par image. Les instants de la table sont des repères visuels approximatifs dans le MP4, relevés à intervalles de 0,1 s sur les chutes. Le contact au sol désigne le premier appui visible hors pieds, ici la main. Un tiret indique que l’événement ne se produit pas.

| Clip | Groupe | Usage | Scénario | Personnes impliquées | Contact entre personnes | Début de chute | Contact au sol |
| --- | --- | --- | --- | --- | --- | --- | --- |
| [fall-01-cam0.mp4](https://fenix.ur.edu.pl/mkepski/ds/data/fall-01-cam0.mp4) | UR-F01 | Calibration | Chute debout vers l’avant | P1 tombe | — | 2,9–3,1 s | 3,3–3,5 s |
| [fall-02-cam0.mp4](https://fenix.ur.edu.pl/mkepski/ds/data/fall-02-cam0.mp4) | UR-F02 | Calibration | Chute amorcée sur une chaise | P1 tombe | — | 1,5–1,7 s | 1,9–2,1 s |
| [adl-01-cam0.mp4](https://fenix.ur.edu.pl/mkepski/ds/data/adl-01-cam0.mp4) | UR-A01 | Contrôle exploratoire | Marche puis accroupissement | P1 | — | — | — |

### Reste à constituer

| Scénario | Séquence à obtenir | Annotation attendue |
| --- | --- | --- |
| Affaissement lent | Séquence autorisée commençant debout ou assise, avec descente graduelle et maintien bas. | P1, début de la descente continue, premier appui hors pieds. |
| Chute individuelle avec témoin | Deux personnes visibles, dont une chute sans contact. | Personne qui tombe, absence de contact, début et contact au sol. |
| Collision avec chute | Séquence autorisée ou animation à deux, une ou deux personnes chutent après le contact. | P1/P2, instant du contact, début et contact au sol pour chaque chute. |
| Collision sans chute | Contacter sans provoquer de perte d’équilibre dans une mise en scène sécurisée, ou employer une animation. | P1/P2, instant du contact, aucune chute. |
| Croisement ou rapprochement sans contact | Filmer deux volontaires consentants marchant à des profondeurs différentes, avec chevauchement apparent à l’image. | P1/P2, absence de contact et de chute, intervalle du chevauchement. |
| Contact indéterminé | Variante avec occultation au moment du rapprochement et chute visible ensuite. | Contact indéterminé, début et contact au sol s’ils sont visibles. |

Pour les prises locales, obtenir l’accord des participants pour la capture, le partage privé avec Adam et l’usage prévu. Employer des prises indépendantes, des mises en scène sécurisées ou de l’animation pour les chutes. Garder les fichiers et annotations identifiantes dans `datasets/` et utiliser des identifiants pseudonymes dans l’inventaire.

Pour chaque prise, noter la source et sa version, le fichier d’origine, les identifiants pseudonymes, les rôles et les temps en secondes depuis la première image. Pour un événement masqué, inscrire « indéterminé » avec l’intervalle de visibilité. Conserver tous les angles et extraits d’une même prise ensemble. Affecter toutes les prises d’une personne au même lot, y compris quand elle figure dans une paire. Si la source ne donne pas les identifiants, grouper par séquence d’origine et indiquer que l’indépendance entre personnes reste inconnue. Réserver un jeu indépendant avant le réglage des seuils. Les trois clips UR ci-dessus servent à la calibration et au contrôle exploratoire.

Pour la recette finale, relever les chutes manquées, les fausses alertes par heure, les confusions de cause, les doublons et le délai entre contact au sol et voix. Les prises locales à deux et leurs annotations restent à produire. L’accès NTU et SAFER n’a pas été demandé sur un compte utilisateur.
