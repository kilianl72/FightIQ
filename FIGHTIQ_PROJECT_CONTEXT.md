# FightIQ — Contexte maître et mémoire de continuité

> **Document vivant — dernière mise à jour : 17 août 2026**  
> **Dépôt :** `kilianl72/FightIQ`  
> **Rôle :** conserver les décisions, l’état réel, la vision et la prochaine action afin de reprendre le projet dans n’importe quelle nouvelle conversation sans repartir de zéro.

## 0. Instructions de reprise obligatoires

Au début de toute nouvelle conversation consacrée à FightIQ :

1. lire ce fichier entièrement avant de proposer une modification ;
2. consulter ensuite l’état actuel du dépôt GitHub et les dernières exécutions GitHub Actions ;
3. distinguer systématiquement :
   - ce qui est réellement développé ;
   - ce qui est engagé ou partiellement fonctionnel ;
   - ce qui est validé sur le principe mais pas encore développé ;
   - ce qui reste une piste ;
4. vérifier le schéma SQL et les scripts présents avant toute migration ;
5. préserver les règles d’identité et de provenance décrites ci-dessous ;
6. mettre à jour ce document après toute décision structurante, changement d’architecture, étape achevée ou nouveau point de blocage ;
7. mettre à jour en priorité les sections **Résumé exécutif**, **Prochaine action exacte**, **Décisions encore ouvertes** et **Journal des changements**.

Le dépôt GitHub reste la source de vérité pour le code, les migrations, les scripts et les workflows. La base Supabase reste la source de vérité pour les données effectivement enregistrées. Ce document est la source de vérité pour la vision produit, les règles permanentes et la continuité entre les conversations.

## 1. Résumé exécutif

FightIQ est une future plateforme web d’analyse MMA centrée sur les combattants et les combats.

L’état réel au 17 août 2026 est le suivant :

- le socle de données est largement avancé ;
- environ 4 607 combattants sont présents dans la base ;
- l’import UFCStats, l’enrichissement Cito, les audits et une importante phase de rapprochement d’identités ont été réalisés ;
- le modèle d’identité canonique `fightiq_id` et la table multi-sources `fighter_source_ids` sont validés ;
- la consolidation V5.6 a terminé avec `unresolved = 0` et a produit trois catégories seulement : UFCStats+Cito, UFCStats seul et Cito seul avec preuve MMA ;
- les statistiques globales combattant sont disponibles ; les événements, combats et rounds détaillés ne sont pas encore ingérés dans le modèle cible ;
- le véritable site FightIQ n’est pas encore développé ;
- les anciens fichiers HTML sont des prototypes, pas le futur produit ;
- Wikidata n’apporte actuellement aucune donnée utile et est écarté du pipeline V1 ;
- un paquet de remplacement complet réunit l’import UFCStats, l’enrichissement Cito, la consolidation et la validation dans `scripts/sync_fighters.py` ; il doit encore réussir un dry-run puis un run réel avant suppression des anciens scripts ;
- un nouveau profil ambigu est désormais placé en quarantaine hors de `fighters` sans bloquer les autres mises à jour fiables ;
- les nouveaux alias peuvent être retrouvés par nom complet, surnom ou variante
  de source, puis confirmés par plusieurs combats, adversaires, événements et
  résultats communs entre Cito et UFCStats **avec** une preuve biographique
  indépendante (date ou lieu de naissance précis, ou mensurations) ;
  l'historique ou le palmarès seuls ne suffisent jamais et une contradiction
  renvoie le cas en quarantaine ;
- une migration, une vue Supabase, des actions validées côté synchroniseur et un
  rapport JSON préparent la future file de revue administrateur ; l'écran de
  l'application n'est pas encore développé ;
- le ranking conserve `scripts/enrich_rankings_cito.py`, passe dans un workflow indépendant et ne remet plus les anciens rankings à zéro avant validation ;
- après cette validation et ce nettoyage, la priorité est la création de l’application.

## 2. Identité et ambition du produit

### Nom

**FightIQ**

### Positionnement

FightIQ doit être orienté vers l’analyse, la compréhension et la mémoire du MMA, davantage que vers un simple fil d’actualité.

### Promesse

> **Know every fighter. Understand every fight.**

### Objectif initial, toujours central

L’utilisateur doit pouvoir :

- rechercher rapidement un combattant ;
- obtenir des suggestions en direct à partir du nom, du prénom, du surnom, d’un alias ou d’une variante orthographique ;
- ouvrir une fiche claire et visuelle ;
- consulter le profil, la photo, la division, le statut et le classement ;
- consulter l’historique complet des combats ;
- filtrer l’historique par année ou période ;
- visualiser les séries de victoires et de défaites ;
- consulter les statistiques globales et combat par combat ;
- comprendre l’évolution de la carrière plutôt que regarder uniquement l’état actuel.

### Périmètre

- **V1 produit : UFC uniquement.**
- L’architecture doit néanmoins être extensible dès maintenant au PFL, à ONE, au Bellator historique, aux promotions régionales et à d’autres organisations.
- Un combattant conserve la même identité FightIQ lorsqu’il change d’organisation.

## 3. Direction artistique validée et figée

La direction artistique ne doit pas être redéfinie à chaque reprise :

- symbole principal : **hexagone** ;
- ne jamais reprendre l’octogone UFC comme forme identitaire ;
- gant MMA minimaliste avec `IQ` intégré ;
- noir : `#111111` ;
- rouge : `#D81F26` ;
- blanc cassé : `#F5F5F5` ;
- univers premium, sombre, moderne et minimaliste ;
- pas de surcharge visuelle ;
- interface pensée autant pour le mobile que pour le desktop ;
- recherche et navigation devant fonctionner naturellement sur Safari/iPhone ;
- pages riches mais lisibles, sans donner l’impression d’un tableur brut ;
- animations discrètes pour les évolutions de scores, radars, périodes et cartes.

## 4. Principes fondateurs de la donnée

1. Un combattant réel correspond à une seule identité FightIQ canonique.
2. `fightiq_id` est l’identifiant interne stable et immuable.
3. Les identifiants des sources externes sont stockés séparément.
4. Le nom seul ne constitue jamais une preuve suffisante pour fusionner deux profils.
5. Une contradiction forte bloque l’association automatique.
6. Une identité ambiguë reste non associée jusqu’à vérification.
7. Une donnée inconnue reste `NULL` : ne jamais l’inventer.
8. Une donnée fiable ne doit pas être écrasée par une source moins fiable.
9. La provenance doit être conservée.
10. Les données brutes ne doivent pas être remplacées par les seuls agrégats calculés.
11. Les états historiques importants doivent être conservés sous forme de snapshots datés.
12. Les données factuelles, les calculs FightIQ, les contenus éditoriaux/IA et les données utilisateur doivent rester séparés.
13. Les unités canoniques enregistrées sont métriques (`cm`, `kg`) et les codes/valeurs internes doivent rester universels.
14. Les conversions métrique/impérial et la traduction français/anglais se font uniquement à l’affichage.

## 5. Architecture d’identité

### 5.1 Table `fighters`

Une ligne par combattant réel, identifiée par `fightiq_id`.

Champs importants déjà utilisés ou visés :

- identité et nom d’affichage ;
- surnom et alias ;
- date de naissance ;
- taille, poids, allonge et leg reach ;
- stance ;
- division actuelle ;
- statut actif/inactif ;
- lieu de naissance ;
- camp / `trains_at` ;
- fighting style / background ;
- octagon debut ;
- photo et informations de provenance/droits ;
- ranking actuel ;
- statut champion actuel.

### 5.2 Table `fighter_source_ids`

Un même `fightiq_id` peut avoir plusieurs identifiants externes :

```text
FIQ-000123 | ufcstats    | <id UFCStats>
FIQ-000123 | cito        | <id Cito>
FIQ-000123 | wikidata    | Qxxxxxxx
FIQ-000123 | ufc         | <id UFC>
FIQ-000123 | tapology    | <id Tapology>
FIQ-000123 | sherdog     | <id Sherdog>
FIQ-000123 | fightmatrix | <id FightMatrix>
FIQ-000123 | bellator    | <id Bellator>
```

Principe :

```text
IDs externes de plusieurs sources
              ↓
      fightiq_id canonique
              ↓
        combattant FightIQ
```

L’ajout d’une nouvelle source ne doit jamais remplacer les IDs existants. Une nouvelle ligne est ajoutée autour du même `fightiq_id`.

### 5.3 Signaux de rapprochement autorisés

Le nom sert à trouver des candidats, puis l’identité doit être confirmée avec autant que possible :

- identifiant externe exact ;
- date de naissance exacte ;
- nom complet et alias ;
- surnom ;
- taille cohérente ;
- lieu de naissance ;
- description MMA/UFC ;
- organisation et division compatibles ;
- historique des combats ;
- adversaires, dates et événements ;
- IDs Tapology, Sherdog, FightMatrix ou organisationnels.

Rejets automatiques ou vérification manuelle obligatoire en cas de :

- DOB contradictoire ;
- UFC athlete ID contradictoire ;
- incohérence physique importante ;
- historique de combats incompatible ;
- plusieurs homonymes encore plausibles ;
- score de confiance insuffisant.

Le palmarès global sert seulement de corroboration. Un alias au nom différent
peut être associé automatiquement lorsque plusieurs combats identifiables et
leurs résultats sont compatibles, à condition qu'une date de naissance exacte,
un lieu de naissance précis compatible ou au moins deux mensurations apportent
une preuve indépendante. Un pays seul, un slug seul, l'historique seul ou le
record seul ne provoquent jamais une fusion.

## 6. Sources et politique d’utilisation

### Sources principales déjà exploitées

1. **UFCStats** : socle combattants, combats et statistiques.
2. **Cito** : enrichissement de profils, statistiques et rankings.

### Source testée puis écartée du pipeline V1

3. **Wikidata** : la tentative n’a ajouté aucune donnée à cause des limitations HTTP 429. Les scripts et workflows associés peuvent être supprimés ; la source ne sera reconsidérée que si elle apporte un bénéfice mesurable sans fragiliser le pipeline.

### Sources complémentaires ciblées

4. **UFC-FR / UFC Fans** : complément et vérification ciblés ; aucune ingestion massive n’a été décidée.
5. **UFC.com** : vérification ponctuelle des profils actifs/récents et des cas importants ; pas de scraping massif permanent.

### Source examinée mais non prioritaire

6. **Octagon API** : risque de redondance élevé avec UFCStats/Cito et bénéfice incertain pour les trous restants.

Toute nouvelle source doit être évaluée selon : qualité, stabilité, provenance, conditions d’utilisation, droit de réutilisation, valeur ajoutée réelle et risque de doublon.

## 7. Travail réellement effectué

### 7.1 Base et automatisations

- création du dépôt `kilianl72/FightIQ` ;
- mise en place d’une base Supabase ;
- import de combattants depuis UFCStats ;
- enrichissement via Cito ;
- récupération de rankings Cito ;
- workflows GitHub Actions manuels et planifiés ;
- scripts d’audit de couverture ;
- statistiques globales combattant disponibles pour une première interface.

### 7.2 Nettoyage et consolidation des identités

Une grosse phase de rapprochement des entrées Cito non associées a été menée :

- recherche des variantes orthographiques ;
- gestion des accents et translittérations ;
- prise en compte des alias et surnoms ;
- comparaison des historiques de combats ;
- investigation des profils non associés ;
- association aux fiches existantes lorsque la correspondance était suffisamment solide ;
- création uniquement lorsqu’aucune identité existante ne correspondait réellement ;
- consolidation finale dans `fighter_source_ids`.

Les phases v5.2 à v5.6 ont notamment servi à améliorer le matching, traiter les alias, investiguer les non-appariés et stabiliser les associations. L’exécution V5.6 validée a terminé avec zéro entrée non résolue. Son registre historique doit être conservé dans `data/cito_identity_resolution_v5_6.json`.

### 7.3 État des fichiers techniques

Avant le nettoyage, le dépôt contient notamment :

- `scripts/import_fighters.py` ;
- `scripts/enrich_fighters_cito_v5_1_update_only.py` ;
- `scripts/enrich_rankings_cito.py` ;
- `scripts/complete_fighter_profiles_cito_v2.py` ;
- `scripts/consolidate_fighter_identities_v5_6.py` ;
- `scripts/audit_fighters_v1.py` ;
- `scripts/audit_remaining_fighter_gaps.py` ;
- les workflows GitHub Actions correspondants ;
- `data/cito_identity_resolution_v5_6.json`.

État cible après validation du nouveau pipeline :

```text
scripts/sync_fighters.py
scripts/enrich_rankings_cito.py
.github/workflows/update-fighters.yml
.github/workflows/update-rankings.yml
data/cito_identity_resolution_v5_6.json
data/cito_identity_overrides.json
supabase/migrations/202608170001_fighter_identity_admin_review.sql
requirements.txt
FIGHTIQ_PROJECT_CONTEXT.md
FIGHTIQ_DATA_ARCHITECTURE.md
FIGHTIQ_ADMIN_IDENTITY_REVIEW.md
FIGHTIQ_CHANGELOG.md
```

`sync_fighters.py` traite uniquement l’identité, les informations générales et les statistiques globales. `enrich_rankings_cito.py` reste séparé. Les anciens scripts d’import, d’enrichissement, de consolidation et d’audit ne doivent être supprimés qu’après un dry-run puis un run réel réussis du remplaçant.

Le synchroniseur doit préserver les `fightiq_id` et associations déjà enregistrés et n’écrire que les nouveautés/différences. Une identité ordinaire ne pouvant pas être classée avec certitude passe en statut `quarantined` sans entrer dans `fighters`. Un conflit entre identifiants canoniques reste bloquant. Une collision de nom ne suffit jamais à fusionner deux personnes.

Le registre V5.6 reste immuable. La future application enregistre les nouvelles
décisions dans les champs administrateur de `cito_unmatched_fighters` ; le
synchroniseur les revalide avant de modifier l'identité canonique.
`data/cito_identity_overrides.json`, chargé après V5.6 et prioritaire, reste le
secours versionné.

Le matching automatique à la demande compare maintenant l'historique Cito avec
les snapshots UFCStats : ID de combat, adversaire, événement/date puis résultat.
Il exige aussi une corroboration biographique indépendante : date de naissance
complète exacte, lieu précis compatible ou au moins deux mensurations. Les
valeurs comparées et leurs écarts sont conservés dans le rapport administrateur.
Les homonymes UFCStats impossibles à distinguer dans les CSV ne sont pas
devinés. Le workflow publie `reports/fighter_identity_quarantine.json` comme
artefact administrateur.

### 7.4 Architecture des domaines de données

| Domaine | Contenu | Pipeline |
|---|---|---|
| Combattants | identité, biographie, mensurations, record et statistiques globales | `sync_fighters.py` |
| Rankings | classements, P4P, champions et intérimaires | `enrich_rankings_cito.py` |
| Événements/cartes | cartes UFC passées et à venir, lieux, dates et ordre des combats | futur pipeline indépendant |
| Combats/rounds | année → événement → combat → round, résultats et statistiques détaillées | futur pipeline indépendant |
| Presse/articles | contexte par combattant, combat, événement ou période | futur pipeline indépendant |
| Utilisateurs/pronostics | comptes, matchmaking et choix des vainqueurs de combats/cartes | futur module applicatif indépendant |

Tous les futurs domaines sportifs doivent référencer le `fightiq_id` existant et ne jamais recréer une identité combattant.

### 7.5 Prototype initial

Un prototype HTML autonome et des visuels de fiche combattant ont été créés au début du projet. Ils ont permis de préciser le besoin de recherche, d’autocomplete, de photos, d’historique et de filtres par année.

Ils ne constituent pas le futur site FightIQ et ne doivent pas être confondus avec une interface de production.

## 8. Dernier audit connu de la base

Audit du 16 août 2026 sur **4 607 combattants**.

| Champ | Présent | Manquant |
|---|---:|---:|
| Poids | 98,3 % | 77 |
| Taille | 91,8 % | 376 |
| Date de naissance | 82,8 % | 791 |
| Allonge | 58,0 % | 1 937 |
| Stance | 79,1 % | 963 |
| Lieu de naissance | 64,4 % | 1 639 |
| Camp / `trains_at` | 25,5 % | 3 432 |
| Fighting style | 24,1 % | 3 495 |
| Leg reach | 37,3 % | 2 887 |
| Octagon debut | 67,7 % | 1 487 |
| Photo | 58,3 % | 1 922 |
| Division actuelle | 66,0 % | 1 568 |

Répartition des sources :

- 3 182 combattants avec UFCStats + Cito ;
- 1 406 avec UFCStats uniquement ;
- 19 avec Cito uniquement.

Trous importants chez les actifs avant Wikidata :

- DOB : 17 ;
- allonge : 96 ;
- stance : 58 ;
- lieu de naissance : 106 ;
- octagon debut : 35 ;
- photo : 75.

Les classés et champions étaient quasiment complets sur les données essentielles. Il ne faut donc pas bloquer la V1 dans l’attente d’un remplissage absolu de tous les combattants historiques.

## 9. Historique et décision Wikidata

### Objectif initial

Le workflow **Wikidata identity and profile completion** devait :

- trouver et enregistrer des QID Wikidata fiables ;
- compléter uniquement les champs encore vides ;
- récupérer des IDs UFC, Tapology, Sherdog, FightMatrix et Bellator ;
- écrire ces identifiants dans `fighter_source_ids` ;
- compléter éventuellement `date_of_birth`, `height_cm`, `place_of_birth` et `photo_url`.

### Résultat réel de l’exécution du 16 août 2026

L’exécution GitHub Actions s’est terminée avec le statut technique `success`, mais les logs montrent une limitation massive de Wikidata : `HTTP 429 Too Many Requests`.

Résultat fonctionnel :

```text
confident_wikidata_matches: 0
already_linked_verified: 0
skipped_or_ambiguous: 4607
fighters_updated: 0

date_of_birth: +0
height_cm: +0
place_of_birth: +0
photo_url: +0

wikidata: 0
ufc: 0
sherdog: 0
tapology: 0
fightmatrix: 0
bellator: 0
```

Comparaison avant/après : aucune évolution.

### Conséquence et décision

- aucune mauvaise association n’a été créée ;
- aucune donnée existante n’a été abîmée ;
- la couche Wikidata n’est pas fonctionnellement validée ;
- ne pas relancer le script inchangé sur les 4 607 combattants ;
- retirer le script et son workflow du dépôt lors du nettoyage ;
- ne pas bloquer l’application sur cette source.

### Conditions minimales avant une éventuelle réouverture

La prochaine version doit intégrer une stratégie compatible avec les limites de la source :

- requêtes groupées lorsque possible ;
- cache local des recherches et entités ;
- reprise après interruption ;
- limitation volontaire du débit ;
- délais et backoff exponentiel sur les réponses 429 ;
- priorité aux UFC actifs et récents ;
- traitement par lots ;
- journal des correspondances, rejets et erreurs ;
- audit lecture seule après exécution ;
- contrôle manuel d’un échantillon de cas sensibles.

Wikidata ne doit pas devenir un nouveau chantier bloquant pour la construction du site. En l’absence d’un besoin produit précis non couvert par UFCStats ou Cito, aucune nouvelle tentative n’est prévue.

## 10. Photos et droits

Une image trouvée n’est pas automatiquement exploitable publiquement.

Pour les images provenant de Wikidata/Wikimedia Commons, conserver notamment :

```text
photo_source = wikidata
photo_original_source = wikimedia_commons
photo_rights_status = pending_confirmation
```

Avant affichage public, vérifier la licence propre à chaque image, les obligations d’attribution et la politique de stockage/cache.

La politique définitive des images reste une décision ouverte.

## 11. Rankings, titres et historique

### Statut actuel

Le champ représentant un champion doit signifier **champion actuel** et être actualisé via les rankings.

Il ne doit jamais signifier « a déjà été champion ».

### À construire

Créer une chronologie historique séparée permettant de conserver :

- organisation ;
- division ;
- type de titre ;
- champion ou champion intérimaire ;
- date de gain ;
- défenses ;
- date et motif de fin de règne : défaite, vacance, destitution ou changement de catégorie.

Cette histoire alimentera la timeline, le contexte des combats et l’identification du prime.

Les rankings officiels actuels, l’historique des rankings et un futur ranking propriétaire FightIQ doivent rester distincts.

## 12. Presse, articles et contextualisation historique

Cette partie est un pilier validé de la vision FightIQ. La plateforme ne doit pas seulement afficher des chiffres : elle doit expliquer **ce qu’un combattant ou un combat représentait à ce moment précis**.

### 12.1 Objectif produit

Relier des articles et sources fiables à :

- un combattant ;
- un combat précis ;
- un événement ou une carte ;
- une période de carrière ;
- un changement de division ;
- une période de titre ou de classement ;
- un camp d’entraînement ;
- une blessure ou un retour ;
- une rivalité ;
- un remplacement ou un combat accepté en short notice ;
- une pesée manquée ;
- une évolution technique ou stratégique ;
- un moment charnière de carrière.

Le but est de contextualiser et d’expliquer les enjeux avant et après le combat.

### 12.2 Contexte d’un combattant

La fiche combattant devra permettre de comprendre :

- sa trajectoire à une date donnée ;
- sa série en cours ;
- sa position dans la division ;
- les attentes qui existaient autour de lui à cette époque ;
- les changements de camp, catégorie ou style ;
- les blessures, interruptions et retours documentés ;
- les moments ayant modifié sa carrière ;
- la différence entre la perception de l’époque et l’analyse rétrospective.

### 12.3 Contexte d’un combat

Chaque combat doit pouvoir recevoir deux couches distinctes.

#### Avant le combat

- séries de victoires/défaites des deux combattants ;
- rankings exacts à cette période ;
- enjeu pour le titre ou le Top 15 ;
- contexte du matchmaking ;
- favori/outsider et perception médiatique ;
- changement de catégorie ;
- short notice ou remplacement ;
- retour de blessure ou longue inactivité ;
- rivalité et déclarations pertinentes ;
- records ou accomplissements potentiels ;
- conséquences sportives attendues.

#### Après le combat

- ce que le résultat a changé ;
- mouvement réel dans le ranking ;
- accès ou perte d’une chance au titre ;
- fin ou prolongation d’une série ;
- changement de catégorie ou de trajectoire ;
- conséquences techniques identifiées par des sources fiables ;
- déclarations post-combat importantes ;
- portée historique constatée avec le recul.

Le contexte pré-combat ne doit pas être réécrit avec des informations connues seulement après le résultat.

### 12.4 Contexte d’un événement

Une page événement doit pouvoir présenter :

- les enjeux du main event et du co-main event ;
- les combats ayant une incidence sur les rankings ;
- les changements de carte ;
- les remplacements ;
- les rivalités et histoires communes ;
- les records en jeu ;
- les trajectoires susceptibles de basculer ;
- un bilan post-event indiquant ce que la soirée a réellement changé.

### 12.5 Sources et conservation

Pour chaque article ou source, conserver au minimum :

- titre ;
- média / éditeur ;
- auteur lorsque disponible ;
- URL ;
- date et heure de publication ;
- langue ;
- date de collecte ;
- type de contenu : interview, analyse, actualité, annonce officielle, compte rendu ;
- combattants, combats, événements et périodes associés ;
- nature du lien : pré-fight, post-fight, blessure, camp, ranking, titre, technique, etc. ;
- niveau de fiabilité ou type de source ;
- résumé interne ;
- affirmations factuelles importantes et source correspondante ;
- droits et possibilités d’utilisation d’un éventuel extrait.

Ne pas recopier les articles complets. Stocker principalement les liens, métadonnées, résumés originaux FightIQ et, uniquement lorsque le droit le permet, de courts extraits correctement attribués.

### 12.6 Couche IA

L’IA pourra :

- sélectionner les articles réellement pertinents ;
- regrouper plusieurs sources parlant du même sujet ;
- produire un résumé de contexte ;
- expliquer les enjeux d’un combat ;
- construire un brief pré-event ;
- produire un bilan post-event ;
- expliquer pourquoi une période constitue un tournant ;
- répondre à des recherches en langage naturel.

Garde-fous obligatoires :

- aucune affirmation importante sans source identifiable ;
- séparer clairement faits, calculs FightIQ et interprétation ;
- ne jamais inventer une blessure, une déclaration ou une circonstance ;
- conserver les dates afin d’éviter les anachronismes ;
- versionner les résumés générés ;
- permettre de revenir aux sources originales ;
- signaler les contradictions entre sources ;
- éviter de présenter une rumeur comme un fait ;
- contrôler humainement les sujets sensibles ou à faible confiance.

### 12.7 Affichage envisagé

- onglet ou bloc **Contexte** sur la fiche combattant ;
- articles positionnés sur la timeline de carrière ;
- bloc **Enjeux avant le combat** sur une fiche de combat ou matchup ;
- bloc **Ce que le résultat a changé** après le combat ;
- résumé des histoires principales sur chaque carte UFC ;
- filtres par période, combat et type de contexte ;
- accès direct à la source depuis chaque information importante.

### 12.8 État de développement

La vision presse/articles/contextualisation est validée, mais elle n’est pas encore développée. Le schéma SQL exact, la sélection des médias, la politique d’archivage, les droits et le système de fiabilité restent à formaliser avant ingestion.

## 13. Fiche combattant — cœur de l’interface

La fiche doit accueillir progressivement :

### Identité

- photo ;
- nom et surnom ;
- âge / date de naissance ;
- nationalité et lieu ;
- taille, poids, allonge et stance ;
- camp et style ;
- division ;
- statut actif ;
- début UFC.

### Situation sportive

- record global et UFC ;
- ranking ;
- statut champion ;
- activité récente ;
- séries de victoires/défaites ;
- méthodes de victoire ;
- temps moyen lorsque disponible.

### Statistiques

- statistiques globales ;
- statistiques combat par combat ;
- striking : volume, précision, défense, absorbés, différentiel, cibles et positions ;
- grappling : takedowns, précision, défense, contrôle, soumissions et autres métriques disponibles ;
- filtres par période, organisation, division et sélection de combats.

### Histoire et contexte

- historique des combats ;
- filtre par année ;
- timeline ;
- classements et titres historiques ;
- articles et contexte de période ;
- évolution du FightIQ Score et du radar lorsque ces éléments seront développés.

## 14. Événements et cartes

À terme, chaque événement doit disposer d’une page avec :

- organisation, date et lieu ;
- main event et co-main event ;
- main card et préliminaires ;
- combattants et fiches matchup ;
- résultats ;
- statistiques ;
- rankings et séries avant le combat ;
- contexte presse/IA ;
- comparaison radar et FightIQ Score ;
- conséquences post-event.

La V1 peut commencer par une structure plus simple, puis être enrichie progressivement.

## 15. FightIQ Score, radar et prime

### FightIQ Score — validé, pas encore formalisé

Créer une note propriétaire indépendante du classement UFC, avec :

- score global ;
- sous-scores compréhensibles ;
- méthodologie documentée ;
- normalisation par division et époque ;
- prise en compte du niveau d’opposition ;
- gestion de l’incertitude ;
- algorithme versionné ;
- snapshots datés permettant de reconstruire un score historique.

Axes possibles à formaliser : striking, puissance/finition, lutte, grappling, défense, cardio/rythme, durabilité, activité et niveau d’opposition.

### Radar hexagonal — validé, pas encore développé

- vue carrière ;
- forme récente ;
- prime ;
- comparaison temporelle ;
- comparaison entre deux combattants ;
- superposition de deux périodes.

### Prime — principe validé, calcul à définir

Le prime est une période contextualisée, pas seulement la meilleure année comptable.

Il devra tenir compte de :

- performances ;
- qualité des adversaires ;
- résultats ;
- FightIQ Score ;
- ranking et titres ;
- âge ;
- dynamique ;
- activité ;
- contexte de carrière.

## 16. Comparateur

Le comparateur doit permettre :

- deux combattants côte à côte ;
- caractéristiques physiques ;
- records ;
- statistiques globales et combat par combat ;
- FightIQ Score et sous-scores ;
- radars superposés ;
- styles et tendances ;
- comparaison prime contre prime ;
- choix d’une période pour chaque combattant ;
- comparaison de deux versions temporelles d’un même combattant ;
- accès depuis une carte ou une fiche matchup.

Le comparateur est validé sur le principe mais pas encore développé.

## 17. Prédictions et jeu FightIQ

Fonctionnalités prévues pour une version ultérieure :

- probabilités FightIQ de victoire ;
- analyse du matchup ;
- facteurs favorables, défavorables et incertitudes ;
- prédiction de méthode lorsque les données le permettent ;
- enregistrement des prédictions avant le combat ;
- historique de performance et calibration du modèle ;
- comparaison éventuelle aux cotes avec provenance et timestamp ;
- paris fictifs avec monnaie/points FightIQ ;
- historique personnel et classements.

Le jeu fictif doit rester séparé de toute intégration d’argent réel, qui impliquerait un cadre juridique et réglementaire spécifique.

## 18. Cards FightIQ façon jeu de sport

Fonctionnalité future :

- carte visuelle avec photo, nom, division, score et attributs ;
- variantes rookie, contender, champion, prime, performance spéciale et légende ;
- cartes historiques reposant sur les données réellement disponibles à cette date ;
- collections utilisateur ;
- raretés, séries, challenges et récompenses ;
- aucune nécessité de blockchain ou NFT.

## 19. Profils utilisateurs et communauté

Version ultérieure :

- compte, avatar et pseudo ;
- combattants favoris ;
- événements suivis ;
- watchlist et notifications ;
- historique des pronostics et paris fictifs ;
- collection de cards ;
- badges et classements ;
- profil public optionnel ;
- modération et mécanismes anti-spam avant toute dimension sociale ouverte.

## 20. Roadmap produit

### Phase immédiate — fermer proprement le pipeline combattants

1. installer `scripts/sync_fighters.py` et les deux workflows séparés ;
2. appliquer la migration de revue administrateur dans Supabase ;
3. lancer le workflow combattants en `dry_run = true` ;
4. exiger `unresolved = 0`, aucune collision d’ID, des volumes cohérents avec la référence de 4 607 combattants et contrôler l'artefact des quarantaines ;
5. lancer ensuite le run réel et vérifier la relecture finale de Supabase ;
6. lancer séparément le workflow ranking en dry-run puis en réel ;
7. supprimer les anciens scripts et workflows remplacés, ainsi que les restes Wikidata ;
8. considérer le socle combattants V1 stable sans attendre 100 % de complétude des profils historiques ;
9. commencer l’application et développer les autres domaines dans des pipelines séparés.

### V1 — Fondation UFC utilisable

- squelette du site et navigation ;
- accueil premium ;
- recherche globale ;
- autocomplete nom/prénom/surnom/alias ;
- annuaire des combattants ;
- filtres division et activité ;
- fiche combattant ;
- profil et photo ;
- stats globales ;
- première ingestion séparée des événements, combats et rounds détaillés ;
- historique des combats et filtre par année ;
- séries de victoires/défaites ;
- statistiques par combat essentielles ;
- rankings actuels ;
- événements/cartes de base ;
- interface cohérente mobile/desktop.

### V1.x — Analyse et mémoire FightIQ

- FightIQ Score et Points ;
- radar ;
- comparateur enrichi ;
- timeline ;
- prime ;
- historique des rankings ;
- historique des champions et titres ;
- snapshots datés ;
- contextualisation presse/articles ;
- briefs pré-fight et conséquences post-fight ;
- premières cards FightIQ.

### V2 — Intelligence et prédiction

- modèle matchup/prédictions ;
- historique public des prédictions ;
- calibration et mesure des performances ;
- comparaison aux cotes ;
- paris fictifs / points ;
- profils utilisateurs et favoris ;
- dashboards personnels.

### V3 — Jeu et communauté

- collections de cards ;
- versions historiques/prime/champion ;
- challenges ;
- récompenses ;
- leaderboards ;
- communauté maîtrisée.

### V4 — MMA multi-organisations

- PFL, ONE, Bellator/historique et autres promotions ;
- historique cross-promotion ;
- événements et rankings multi-organisations ;
- comparaisons inter-organisations ;
- base historique MMA indépendante de l’UFC.

## 21. Ce qui n’est pas encore développé

Pour éviter toute confusion lors d’une future reprise :

- aucun véritable front-end de production ;
- aucun écran administrateur de quarantaine, même si son backend et son contrat
  sont maintenant préparés ;
- aucune interface FightIQ complète ;
- aucune ingestion cible complète des événements, combats et rounds détaillés ;
- aucun FightIQ Score formalisé ;
- aucun radar fonctionnel ;
- aucune timeline temporelle ;
- aucun calcul de prime ;
- aucun historique complet des rankings ;
- aucun historique complet des champions/titres ;
- aucun comparateur produit ;
- aucune expérience événement enrichie ;
- aucune ingestion structurée d’articles/presse ;
- aucune contextualisation IA publique ;
- aucun système de pronostics ;
- aucun modèle prédictif validé ;
- aucun profil utilisateur ;
- aucune collection de cards ;
- aucune extension multi-organisations en production.

## 22. Décisions encore ouvertes

- stack front-end exacte ;
- architecture de l’API interne ;
- hébergement du site ;
- définition précise d’un « combattant récent » pour le quality gate ;
- statistiques affichées par défaut sur la fiche ;
- structure exacte des historiques de rankings et de titres ;
- méthode et pondérations du FightIQ Score ;
- calcul du prime ;
- niveau de provenance nécessaire au niveau de chaque champ ;
- politique définitive des photos, licences, cache et attribution ;
- liste des médias et sources éditoriales acceptées ;
- politique de fiabilité, contradiction et modération des articles ;
- politique d’archivage des liens et contenus ;
- schéma SQL des articles et liens de contexte ;
- fréquence des snapshots ;
- modalités futures d’intégration des organisations non-UFC ;
- ergonomie finale de la page `/admin/fighter-identities` et notifications aux
  administrateurs.

## 23. Prochaine action exacte

Au 17 août 2026 :

1. déposer le paquet unifié dans le dépôt : il remplace toute la partie
   combattants généraux par le seul couple `sync_fighters.py` /
   `update-fighters.yml`, sans supprimer immédiatement l’ancien code ;
2. appliquer `supabase/migrations/202608170001_fighter_identity_admin_review.sql` ;
3. exécuter **Sync FightIQ canonical fighter database** avec `dry_run = true` ;
4. vérifier `unresolved = 0`, les volumes et l'artefact administrateur des quarantaines ;
5. si et seulement si le plan est sain, relancer avec `dry_run = false` ;
6. vérifier le rapport final relu depuis Supabase ;
7. exécuter **Sync FightIQ UFC rankings** avec `dry_run = true`, puis avec `dry_run = false` si le plan est cohérent ;
8. supprimer les anciens scripts/workflows combattants remplacés et les restes
   Wikidata ; conserver uniquement le pipeline ranking séparé ;
9. choisir le stack front-end et commencer le squelette de l’application ;
10. développer d’abord accueil/navigation, recherche/autocomplete, listing et fiche combattant avec statistiques globales, puis l'écran admin ;
11. créer ensuite le pipeline séparé événements → combats → rounds, puis les couches presse/contexte et utilisateurs/pronostics ;
12. maintenir ce fichier après chaque étape importante.

## 24. Sécurité et secrets

- ne jamais enregistrer `SUPABASE_SECRET_KEY`, `CITO_API_KEY` ou tout autre secret dans ce fichier ou dans le dépôt ;
- utiliser les secrets GitHub Actions ;
- éviter d’exposer des données personnelles utilisateur ;
- vérifier les droits et conditions d’utilisation de chaque source avant publication.

## 25. Règle d’or

> Une identité douteuse, une donnée inventée ou un contexte sans source coûte davantage qu’un champ temporairement vide.

## 26. Journal des changements

### 17 août 2026

- création du document maître de continuité destiné au dépôt GitHub ;
- consolidation de la vision, de l’architecture, de l’état réel et de la roadmap ;
- ajout du résultat fonctionnel réel de la passe Wikidata V2 : 0 enrichissement à cause des erreurs HTTP 429 ;
- ajout détaillé du pilier presse/articles/contextualisation pour les combattants, combats, événements et périodes ;
- confirmation que la consolidation V5.6 a atteint zéro non-résolu avec 3 182 UFCStats+Cito, 1 406 UFCStats seuls et 19 Cito seuls ;
- décision de retirer Wikidata du pipeline V1 ;
- préparation d’un synchroniseur canonique unique pour les informations générales et statistiques globales des combattants ;
- ajout d'une quarantaine non bloquante conservant les nouveaux profils ambigus hors de `fighters` ;
- maintien du ranking dans son script et son workflow indépendants, avec planification avant écriture et suppression de la remise à zéro globale initiale ;
- ajout de `FIGHTIQ_DATA_ARCHITECTURE.md` et `FIGHTIQ_CHANGELOG.md` afin de sauvegarder les règles techniques et le travail réalisé ;
- ajout de `data/cito_identity_overrides.json` pour les futures décisions sans modifier l'historique V5.6 ;
- ajout du matching d'alias par historique Cito/UFCStats, avec contrôle des
  adversaires, événements, dates et résultats ;
- renforcement de ce matching : noms complets/inversés, surnoms et alias servent
  à découvrir les candidats, mais l'historique n'est décisif qu'avec une date
  de naissance exacte, un lieu de naissance précis compatible ou plusieurs
  mensurations concordantes ;
- conservation dans le rapport administrateur des valeurs biographiques,
  relations de lieu et écarts physiques comparés ;
- ajout de garde-fous interdisant une fusion sur le palmarès seul et refusant
  un historique seul, un pays seul ou un slug seul, ainsi que les historiques
  contradictoires ou les homonymes UFCStats indifférenciables ;
- ajout de la migration, de la vue et du contrat de revue administrateur, ainsi
  que du rapport JSON téléversé par GitHub Actions ;
- ajout des actions administrateur `link`, `create`, `exclude` et `needs_info`,
  toujours revalidées par le synchroniseur ;
- séparation explicite des futurs domaines événements/cartes, combats/rounds, presse/contexte et utilisateurs/pronostics ;
- confirmation que la prochaine grande phase produit, après validation et nettoyage du pipeline, est la construction de l’application FightIQ.
