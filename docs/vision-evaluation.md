# Évaluation et corrections de la vision (lot 1)

Mesures du 14 novembre 2026, poste de développement : Windows 11 (10.0.26200), Intel Core i7-13620H, 16 Go,
Python 3.14.7, OpenCV 5.0.0, MediaPipe 1.2.3, modèle `pose_landmarker_lite.task`. Trois versions comparées
sur les mêmes séquences et dans les mêmes conditions :

- **origine** — `main` au commit `1e89061` ;
- **passe 1** — commit `26b261c` (premières corrections) ;
- **finale** — après le réaudit (commit de cette branche qui ajoute ce document).

Aucun résultat ne vient de la webcam cible : elle n’était pas disponible. Chaque ligne indique sa source.

## Données et méthode

| Source | Contenu | Usage |
| --- | --- | --- |
| UR Fall, caméra 0 (MP4 traités par MediaPipe, vue RGB isolée) | 30 chutes, 40 activités, 30 images/s, 320×240 | Calibration : 20 chutes et 27 activités. Jeu réservé : 10 chutes et 13 activités (un clip sur trois, `fall-03`, `fall-06`… `adl-39`), fixé avant tout réglage et consulté une seule fois, après les derniers réglages. |
| Poses simulées (`faaall_alarm/simulation/`) | 37 scénarios, dont 19 ajoutés pour reproduire les défauts | Développement et réaudit, plus 4 variantes : 10 images/s, cadence irrégulière (±30 ms), personne deux fois plus loin, bruit des points et visibilité dégradée (« sombre »). |

- **Annotations.** Les étiquettes par image des CSV UR Fall (-1 hors sol, 1 au sol) sont une source indépendante. Elles donnent la posture au sol et le **contact au sol** (première image étiquetée 1), légèrement postérieur au premier appui. Les segments debout, marche, assis et accroupi ont été relevés à la main sur des planches d’images (`datasets/urfall/annotations-postures.csv`, hors Git). Ils ne sont pas indépendants de l’auteur des corrections.
- **Découpage des clips.** Les clips UR Fall sont courts : la personne est déjà là avant la première image et reste au sol après la dernière. Le banc répète donc la première pose 2 s avant le clip et la dernière 4 s après, comme une personne immobile. Ces images sont exclues des mesures de posture. La colonne « dans le clip » compte les chutes confirmées avant la fin réelle du MP4.
- **Activités.** Les activités sont réparties en trois groupes : gestes ordinaires, coucher sur le lit ou le canapé (zones de repos tracées sur les images avant les réglages) et **mise au sol volontaire** (à genoux, à quatre pattes, allongé par terre : `adl-30` à `35`, `38` à `40`). Ce dernier groupe ressemble à un affaissement lent, que le produit doit signaler : ses alertes sont comptées à part et non dans les fausses alertes par heure.
- **Durée observée.** Elle reste faible : 4,4 min de calibration et 2,2 min réservées, dont 2,4 et 1,2 min d’activités. Une fausse alerte par heure n’est donc pas mesurable finement. Les valeurs indiquent seulement 0 ou 1 fausse alerte sur ces durées.
- **Une seule personne.** UR Fall ne contient qu’une personne par clip. Les croisements, collisions et témoins ne sont vérifiés qu’en simulation.

## Résultats

### MP4 UR Fall traités par MediaPipe

| Mesure | Calibration : origine | Calibration : passe 1 | Calibration : finale | Réservé : origine | Réservé : passe 1 | Réservé : finale |
| --- | --- | --- | --- | --- | --- | --- |
| Séquences, durée observée | 47, 263 s | 47, 263 s | 47, 263 s | 23, 134 s | 23, 134 s | 23, 134 s |
| Exactitude des postures | 57,8 % | 74,2 % | 74,0 % | 57,0 % | 75,5 % | 76,3 % |
| Erreurs franches de posture | 42,1 % | 21,1 % | 21,8 % | 43,0 % | 20,9 % | 21,5 % |
| Changements de posture affichés / min | 182 | 29 | 29 | 165 | 32 | 31 |
| Temps en « Posture indéterminée » | 0 s | 7,4 s | 6,8 s | 0 s | 3,4 s | 2,4 s |
| Chutes détectées | 7/20 | 20/20 | 20/20 | 3/10 | 9/10 | 9/10 |
| … dont dans le clip | 3 | 6 | 6 | 3 | 1 | 1 |
| Doublons d’incident | 0 | 0 | 0 | 0 | 0 | 0 |
| Délai médian (max) contact au sol → voix | 1,33 s (1,7) | 1,6 s (3,7) | 1,6 s (3,7) | 1,4 s (1,4) | 2,1 s (3,2) | 2,1 s (3,2) |
| Fausses alertes, gestes et coucher en zone | 1 / 21 clips (25/h) | 0 / 21 | 0 / 21 | 0 / 10 | 0 / 10 | 0 / 10 |
| Alertes sur mise au sol volontaire (à part) | 4/6 | 3/6 | 3/6 | 0/3 | 2/3 | 2/3 |
| Identifiants de piste en trop (clips à une personne) | 40 | 12 | 12 | 13 | 4 | 4 |
| Images perdues sur erreur d’analyse | 13 (`KeyError`) | 0 | 0 | 15 (`KeyError`) | 0 | 0 |

Les délais d’origine ne portent que sur les 7 et 3 chutes détectées : ce sont les chutes franches.
Dans la version finale, le délai est de 0,8 à 1,7 s quand la personne reste visible au sol. Il atteint 2,4 à 3,7 s
quand MediaPipe la perd au sol et que la chute est confirmée après 2,5 s de pose masquée.

**Postures par état, jeu réservé** (images correctes / erreurs franches / indéterminées) :

| État annoté | Origine | Finale |
| --- | --- | --- |
| Au sol (CSV) | 314 / 456 / 0 | 683 / 76 / 11 |
| Marche | 264 / 337 / 0 | 334 / 231 / 36 |
| Assis | 66 / 76 / 0 | 64 / 78 / 0 |
| Debout | 52 / 0 / 0 | 45 / 7 / 0 |
| Accroupi, penché (« pas au sol ») | 528 / 55 / 0 | 513 / 70 / 0 |

**Effet séparé du seuil de confiance MediaPipe.** Le code final avec le seuil d’origine (0,5) obtient 17/20 et 6/10 chutes,
pour une exactitude des postures de 67,9 % et 72,3 %. Le seuil de 0,3 apporte donc 3 et 3 chutes, et la logique corrigée le reste.

### Poses simulées

37 scénarios (942 s), mêmes poses pour les trois versions.
« Écarts » : scénarios dont le nombre d’incidents ou de reprises diffère de l’attendu.

| Variante | Origine : postures, écarts | Passe 1 : postures, écarts | Finale : postures, écarts |
| --- | --- | --- | --- |
| Base 15 images/s | 89,2 %, 1 (reprise à genoux) | 89,1 %, 1 (ramasser derrière un meuble) | 94,4 %, 0 |
| 10 images/s | 89,6 %, 1 | 87,8 %, 1 | 93,0 %, 0 |
| Cadence irrégulière | 89,2 %, 1 | 89,2 %, 1 | 94,5 %, 0 |
| Personne deux fois plus loin | 89,2 %, 1 | 89,1 %, 1 | 94,4 %, 0 |
| Bruit et faible visibilité | 85,0 %, 3 (chutes assise et depuis le canapé manquées, reprise à genoux) | 87,7 %, 2 | 93,1 %, 1 (ramasser derrière un meuble) |

Le délai médian entre l’arrivée au sol et la confirmation passe de 1,27 s à 0,80 s.
Aucune inversion d’identité n’a lieu dans les croisements simulés pour les trois versions ; deux persistent dans la variante bruitée.

### Performances en direct (`adl-36`, coûts sans cadence, latence en lecture réelle de la vue RGB)

| Étape (médiane, p95) | Origine | Finale |
| --- | --- | --- |
| Lecture et décodage, 640×240 | 0,25 ms (0,36) | 0,30 ms (0,37) |
| Inférence MediaPipe lite | 17,8 ms (20,0) | 17,9 ms (19,3) |
| Suivi, posture, chute, incident | 0,21 ms (0,28) | 0,29 ms (0,38) |
| Squelette et mise à l’échelle | 0,42 ms (0,76) | 0,48 ms (0,65) |
| Cadence analysée (lecture réelle à 30 images/s) | 30 images/s | 30 images/s |
| Latence image → résultat, poste actuel | 26 ms (29) | 27 ms (29) |
| Latence, poste 3× plus lent simulé (+40 ms par inférence) | **4,3 s (8,6 s)**, dérive continue | **116 ms (132 ms)**, bornée |

L’inférence domine : l’analyse ajoutée coûte moins de 0,1 ms par image. Le gain porte sur la latence quand l’inférence ne suit pas la caméra.
La webcam garde désormais la dernière image et le MP4 saute les images en retard. L’écran ne recalcule plus une image déjà affichée.
Un MP4 de démonstration à deux mannequins (640×480) donne 10,6 ms d’inférence, mais sans personne réelle : aucun coût mesuré avec deux personnes réelles.

**Modèle.** Mesures sur les mêmes MP4 et le même poste, part des images au sol où une personne est détectée et temps par image.
`pose_landmarker_full` : 64 % et 22,7 ms. `pose_landmarker_heavy` : 73 % et 54 ms. `lite` au seuil de 0,5 : 78 % et 16,6 ms ; au seuil de 0,3 : 83 % et 16,9 ms.
Le modèle lite est conservé et seul le seuil de confiance change (`pose_min_confidence` = 0,3).
Les modèles full et heavy, téléchargés depuis la source officielle Google pour cette comparaison, ne sont ni utilisés ni installés par `install.ps1`.

## Causes confirmées et corrections

### Passe 1

| Défaut (preuve) | Couche | Correction |
| --- | --- | --- |
| `KeyError` dans `CollisionDetector` quand une piste change pendant un contact candidat : 13 et 15 images perdues sur UR Fall. L’application journalise « Erreur d’analyse » et perd toutes les images à deux. | détection | Le contact candidat est abandonné si les pistes ne sont plus les mêmes. |
| Suspicion effacée après 1 s de pose perdue : MediaPipe perd la personne au sol (fall-01, -07, -19…), 13 chutes sur 20 manquées. | détection | La suspicion survit à la perte de pose. Elle est confirmée quand la pose revient basse, ou après 2,5 s si le basculement était rapide jusqu’au sol et loin des bords. |
| Vitesse de piste calculée sur un seul écart d’image, poses parasites isolées et doublons : nouvelles pistes DISARMED au sol, « Personne 2 » fantôme (40 identifiants en trop). | suivi | Vitesse sur une fenêtre continue, prédiction bornée, pistes provisoires (confirmées à 3 observations), filtre de doublons, appariement global avec coût de taille, priorité à la personne tombée. |
| Vitesse de posture lue avant l’ajout de l’image courante (`hip_displacement_torso`). | suivi, posture | L’observation entre dans l’historique avant toute mesure. |
| FALLING affiché sur la seule vitesse de descente : assise rapide, accroupissement, ramassage d’objet. | posture | Basculement rapide (torse incliné ou raccourci) et hanches déjà près du sol, uniquement quand le détecteur est armé. |
| Référence de hauteur prise en pixels sur 30 s, donc fausse dès que la personne change de distance. | suivi | Référence par personne : rapport hauteur sur torse × torse de référence lissé, jamais mis à jour pendant une descente. |
| Chutes vers la caméra et depuis une chaise manquées : torse presque vertical, image bruitée qui lève la suspicion. | posture, détection | Raccourci du torse traité comme arrivée basse, maintien au sol avec hystérésis, levée de suspicion après 0,4 s de redressement soutenu. |
| Coucher sur le lit ou le canapé (adl-10, -11, -23) pris pour une chute : s’asseoir en se penchant passait pour une chute rapide. | détection | Seul le basculement rapide de la dernière seconde compte. Dans une zone de repos, il doit dépasser 3 torses/s. |
| Marche non reconnue (vers la caméra, à petite vitesse) ou devinée (balancement), STANDING par défaut avec les jambes masquées. | posture | Déplacement net sur 0,8 s, alternance des chevilles, variation proportionnée de la taille. « Posture indéterminée » si les jambes manquent. |
| Posture affichée image par image : 182 changements par minute. | posture | Libellé lissé : 0,3 s pour entrer dans une posture, 0,6 s pour « indéterminée », FALLING immédiat. |
| Reprise déclarée à genoux (scénario de reprise partielle). | détection | La reprise exige des hanches relevées d’au moins 1,1 torse au-dessus du sol mesuré et une posture debout ou en marche. |
| Latence qui dérive quand l’inférence ne suit pas la caméra. | capture, interface | Dernière image seulement, images en retard sautées en MP4, pas de recalcul d’une image déjà affichée. |

### Réaudit (passe 2)

| Défaut trouvé dans la passe 1 (preuve) | Correction |
| --- | --- |
| Après une chute vers la caméra, SITTING affiché pendant 431 images alors que l’incident était confirmé : les genoux remontés au-dessus des hanches passaient pour une assise. | Une personne tombée qui reste basse est affichée LYING. L’assise exige des genoux pas plus de 0,3 torse au-dessus des hanches. |
| WALKING affiché en se relevant, en s’accroupissant ou en se redressant au sol : le centre du torse avance quand le corps s’incline. | Déplacement mesuré aux hanches. Le déplacement ne compte comme marche que torse droit (< 20°). |
| Se baisser vite derrière un meuble puis disparaître confirmait une chute : le temps masqué comptait dans le maintien au sol. | La confirmation sans voir la personne exige une descente des hanches d’au moins 1,1 torse. Le temps masqué ne compte plus dans le maintien. |
| En conditions sombres, la chute depuis la chaise était manquée : une image inexploitable isolée remettait à zéro le réarmement d’1 s. | Interruption tolérée jusqu’à 0,3 s dans le réarmement et la reprise. |
| Chute confirmée sur pose masquée sans niveau de sol connu : un passage à genoux aurait pu valoir reprise. | Le niveau du sol est relevé dès l’arrivée basse. |
| Le banc comptait des inversions d’identité au point exact du croisement et dans la variante éloignée (vérité dans un autre repère). | Correction du banc : ce n’étaient pas des défauts du suivi. |

## Limites encore observées

- **fall-06** (jeu réservé, chute depuis une chaise) reste manquée. Elle n’a pas été réglée après la consultation du jeu réservé.
- **Ramasser derrière un meuble, variante bruitée** : une fausse alerte persiste en simulation. Le bruit gonfle la descente mesurée, qui prend le point le plus haut de la fenêtre. Correctif proposé : une hauteur de départ robuste (médiane des 3 points les plus hauts). Il reste à vérifier sur un nouveau jeu indépendant.
- **Mise au sol volontaire** : 2 alertes sur 3 clips réservés et 3 sur 6 en calibration. S’allonger lentement par terre hors d’une zone de repos ressemble à un affaissement lent, que le produit doit signaler. Une zone de repos ou l’annulation règle ces cas.
- **Postures** : la marche reste confondue avec la station debout sur ~40 % des images de marche réelle (petites silhouettes, chevilles imprécises en 320×240). L’assise sur le bord du lit ou du canapé est souvent affichée SITTING alors que le CSV dit « au sol ».
- **Délai de confirmation** : de 2,4 à 3,7 s quand MediaPipe perd la personne au sol, à cause de l’attente de 2,5 s de pose masquée.
- **Pistes fantômes** : de 400 à 430 images par jeu de calibration montrent encore une seconde personne. Ce sont surtout des personnes tombées masquées, gardées à l’écran en « Posture indéterminée », pendant qu’une seconde estimation du même corps apparaît.
- **Démarrage** : une nouvelle personne s’affiche après 3 observations, soit environ 0,1 s à 30 images/s.
- Le comportement en direct d’OpenCV avec `CAP_PROP_BUFFERSIZE` et la latence webcam n’ont pas été mesurés sur la webcam cible.

## Données manquantes pour valider le reste

Ces données relèvent du corpus (lot d’Abdoullah, [docs/corpus.md](corpus.md)) et de la recette sur le poste cible.

| Scénario | Prise ou annotation à obtenir |
| --- | --- |
| Deux personnes : croisement, collision avec ou sans chute, témoin, contact incertain | Prises réelles à deux, avec P1/P2, instant du contact et contact au sol de chaque chute. Aucune séquence réelle disponible : vérifié en simulation seulement. |
| Affaissement lent réel | Séquences autorisées avec début de descente et premier appui. UR Fall n’en contient pas : vérifié en simulation seulement. |
| Chute vers la caméra, depuis le canapé, près d’un lit sur une scène réelle avec zone de repos | Prises avec zone de repos tracée et contact au sol annoté. |
| Fausses alertes par heure | Plusieurs heures d’activités ordinaires dans la pièce cible : 3,6 min d’activités ne permettent qu’un ordre de grandeur. |
| Contact au sol indépendant | Instant du premier appui relevé par une autre personne. Les CSV UR Fall marquent le début de la posture au sol, un peu après le contact. |
| Webcam cible | Cadrage réel, cadence et latence en direct, voix audible : `.\scripts\run.ps1 -Diagnostic "artifacts\diagnostic.jsonl"`. |

## Reproduire

Depuis la racine, dans PowerShell, après `.\scripts\install.ps1`. Les MP4 UR Fall vont dans `datasets\urfall\` avec les deux CSV `urfall-cam0-*.csv`, comme indiqué dans [docs/corpus.md](corpus.md).
`datasets\`, `artifacts\` et `models\` sont ignorés par Git.

```powershell
# 1. Poses MediaPipe en cache (vue RGB isolée), au seuil de l'application
.\.venv\Scripts\python.exe scripts\evaluate_vision.py poses --min-confidence 0.3 --out-dir artifacts\eval\poses-conf03
# 2. Scénarios simulés et variantes de conditions
.\.venv\Scripts\python.exe scripts\evaluate_vision.py sim
.\.venv\Scripts\python.exe scripts\evaluate_vision.py sim --out-dir artifacts\eval\sim-sombre --noise 0.003 --dropout 0.15
# 3. Rejeu : calibration, puis jeu réservé une seule fois
.\.venv\Scripts\python.exe scripts\evaluate_vision.py replay --label finale --split calib --pose-dir artifacts\eval\poses-conf03 --verbose
.\.venv\Scripts\python.exe scripts\evaluate_vision.py replay --label finale --split holdout --pose-dir artifacts\eval\poses-conf03 --show-holdout
# 4. Autre version du code (ex. main dans un worktree) sur les mêmes poses
git worktree add ..\faaall-origine 1e89061
.\.venv\Scripts\python.exe scripts\evaluate_vision.py replay --code-root ..\faaall-origine --label origine --split calib --pose-dir artifacts\eval\poses
# 5. Performances par étape et latence (poste lent simulé avec --extra-ms)
.\.venv\Scripts\python.exe scripts\evaluate_vision.py perf datasets\urfall\adl-36-cam0.mp4 --crop right --realtime --extra-ms 40
# 6. Tableau comparatif
.\.venv\Scripts\python.exe scripts\evaluate_vision.py report artifacts\eval\results\origine-calib.json artifacts\eval\results\finale-calib.json
```

Le diagnostic local écrit un enregistrement JSON par personne et par image, sans image ni coordonnée brute. Il contient les points fiables, l’angle et la longueur du torse, le rapport de hauteur, les hanches au-dessus des chevilles, l’état du détecteur, les nouvelles pistes et les événements. Il s’active avec `.\scripts\run.ps1 -Video "C:\chemin\essai.mp4" -Diagnostic "artifacts\diagnostic.jsonl"`.
