# FightIQ — installation de la sauvegarde consolidée

Ce paquet ne reconstruit pas la base. Il reprend les `fightiq_id`, les liens
`fighter_source_ids` et les décisions V5.6 déjà enregistrées dans Supabase.

## Contenu à déposer à la racine du dépôt

```text
scripts/sync_fighters.py
scripts/enrich_rankings_cito.py
.github/workflows/update-fighters.yml
.github/workflows/update-rankings.yml
data/cito_identity_resolution_v5_6.json
data/cito_identity_overrides.json
supabase/migrations/202608170001_fighter_identity_admin_review.sql
reports/README.md
tests/test_sync_fighters.py
tests/test_enrich_rankings_cito.py
requirements.txt
.gitignore
README.md
FIGHTIQ_PROJECT_CONTEXT.md
FIGHTIQ_DATA_ARCHITECTURE.md
FIGHTIQ_ADMIN_IDENTITY_REVIEW.md
FIGHTIQ_CHANGELOG.md
START_HERE.txt
```

Le registre `data/cito_identity_resolution_v5_6.json` est indispensable et doit
rester immuable. Il contient 133 liens validés, 20 décisions de création
représentant 21 profils Cito et 26 exclusions.

Les nouvelles décisions prises depuis la future interface administrateur sont
enregistrées dans `cito_unmatched_fighters`, puis appliquées par le
synchroniseur. `data/cito_identity_overrides.json` reste le mécanisme de secours
versionné : il est lu après V5.6 et a donc priorité. Ne jamais modifier le
registre V5.6.

## Responsabilités des deux pipelines

### Combattants

`scripts/sync_fighters.py` gère uniquement :

- les identités et informations générales UFCStats ;
- les profils et statistiques globales Cito ;
- les associations canoniques UFCStats/Cito ;
- la recherche des noms complets/inversés, surnoms et alias de source ;
- la comparaison des historiques Cito/UFCStats pour confirmer les alias avec
  une preuve biographique indépendante ;
- les nouveaux profils et les profils ayant évolué ;
- les créations Cito-only avec preuve MMA suffisante ;
- les exclusions Power Slap/test/non-MMA ;
- la quarantaine des identités ambiguës ;
- l'application des décisions approuvées par un administrateur ;
- la génération du rapport JSON administrateur ;
- la validation des trois catégories canoniques.

### Ranking

`scripts/enrich_rankings_cito.py` gère uniquement :

- les rankings par division ;
- les rankings P4P ;
- les champions et champions intérimaires ;
- la division courante lorsqu'elle provient du ranking.

Il calcule tout son plan avant d'écrire et ne remet plus tous les rankings à
zéro au début du run.

## Déploiement sûr

1. Extraire le ZIP à la racine du dépôt FightIQ.
2. Ne supprimer aucun ancien fichier pour le moment.
3. Committer et pousser les nouveaux fichiers.
4. Appliquer dans Supabase la migration
   `supabase/migrations/202608170001_fighter_identity_admin_review.sql`.
5. Vérifier que la vue `admin_fighter_identity_queue` existe.
6. Lancer **Sync FightIQ canonical fighter database** avec `dry_run = true`.
7. Vérifier : `unresolved = 0`, aucune collision d'ID et des volumes cohérents
   avec la référence de 4 607 combattants.
8. Télécharger l'artefact `fightiq-fighter-identity-report-*` et examiner les
   profils `quarantined`. Ils sont hors de `fighters` et ne bloquent pas les
   mises à jour fiables.
9. Relancer le workflow combattants avec `dry_run = false`.
10. Vérifier le rapport final obtenu après relecture de Supabase.
11. Lancer **Sync FightIQ UFC rankings** avec `dry_run = true`.
12. Si le plan est cohérent, relancer le ranking avec `dry_run = false`.
13. Supprimer seulement ensuite les anciens scripts et workflows remplacés.

Le workflow combattants est planifié à 05:30 UTC. Le ranking est séparé et
planifié à 06:30 UTC.

Après ce remplacement, il ne doit rester qu'un seul script et un seul workflow
pour le domaine **combattants généraux** : `sync_fighters.py` et
`update-fighters.yml`. `enrich_rankings_cito.py` et `update-rankings.yml` ne
sont pas des doublons : ils restent séparés par la décision d'architecture déjà
validée pour le ranking.

## Ancien code supprimable après validation réelle

```text
scripts/import_fighters.py
scripts/enrich_fighters_cito_v5_1_update_only.py
scripts/complete_fighter_profiles_cito_v2.py
scripts/consolidate_fighter_identities_v5_6.py
scripts/audit_fighters_v1.py
scripts/audit_remaining_fighter_gaps.py
.github/workflows/complete-fighter-profiles-cito-v2.yml
.github/workflows/consolidate-fighter-identities-v5-6.yml
```

Conserver ensuite :

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

## Comportement de sécurité

- Les sources et l'état Supabase sont chargés avant la première écriture.
- Un échec de téléchargement, de connexion, de matching structurel ou de
  validation laisse les données précédentes en place.
- Aucun combattant n'est supprimé parce qu'il disparaît temporairement d'une
  source.
- Seules les nouveautés et différences sont écrites.
- Un nom seul ne provoque jamais de fusion.
- Un palmarès global identique seul ne provoque jamais de fusion.
- Les noms complets/inversés, surnoms, accents, translittérations et anciens
  noms de source servent à retrouver des candidats, pas à décider seuls.
- Un alias de nom différent ne peut être relié automatiquement que si au moins
  deux combats et leurs résultats sont compatibles **et** si une preuve
  biographique indépendante concorde : date de naissance exacte, lieu de
  naissance précis, ou au moins deux mensurations. Avec les seules
  mensurations, il faut en plus trois combats reconnus ou deux IDs de combats
  directs.
- Un pays identique seul et un slug identique seul restent des indices
  secondaires et ne rendent jamais la fusion décisive.
- Deux dates de naissance complètes différentes ou deux fortes incohérences de
  taille/allonge bloquent le rapprochement automatique.
- Les homonymes UFCStats non distinguables par les CSV ne sont jamais devinés.
- Une contradiction d'adversaire ou de résultat bloque le rapprochement.
- Un profil ambigu passe en statut `quarantined` dans
  `cito_unmatched_fighters`, mais n'entre pas dans `fighters`.
- Une quarantaine inchangée n'est réanalysée que si son profil Cito ou l'index
  canonique UFCStats/FightIQ a évolué.
- Un conflit entre deux identifiants déjà canoniques reste bloquant : il vaut
  mieux interrompre le run que fusionner deux personnes.
- Une identité Cito secondaire reste un alias de source et ne peut pas écraser
  les données du profil Cito principal.
- Une classification administrateur terminale (`power_slap`, `non_mma`, test
  ou non-combattant) est mémorisée et ne réapparaît pas à chaque run.
- Le ranking n'effectue plus de remise à zéro globale avant validation.
- Si un nom du ranking reste non résolu, les mises à jour sûres continuent mais
  le nettoyage des anciens rankings est reporté afin de préserver l'existant.
- Le même nettoyage est reporté si la couverture du nouveau snapshot descend
  sous 75 % du nombre de combattants précédemment classés.

Les écritures Supabase sont incrémentales et idempotentes, mais ne constituent
pas encore une transaction SQL unique entre les trois tables. Une interruption
pendant les écritures peut donc laisser un sous-ensemble déjà mis à jour ; un
nouveau run complète alors le même plan sans supprimer l'ancienne base.

L'échec du seul endpoint d'historique Cito pour un profil ne bloque pas le run :
le script utilise les autres preuves disponibles et place le cas en quarantaine
si elles ne suffisent pas. L'indisponibilité de la liste principale Cito ou des
snapshots UFCStats reste bloquante avant toute écriture.

## Tests locaux

```bash
python -m unittest discover -s tests -v
python -m py_compile scripts/sync_fighters.py scripts/enrich_rankings_cito.py
```

## Architecture fonctionnelle

| Domaine | Contenu | Pipeline |
|---|---|---|
| Combattants | identité, biographie, mensurations, record et statistiques globales | présent paquet |
| Ranking | rankings, P4P, champions et intérimaires | présent paquet, séparé |
| Événements/cartes | événements UFC passés et futurs, dates, lieux et cartes | futur pipeline |
| Combats/rounds | année → événement → combat → round et statistiques détaillées | futur pipeline |
| Presse/articles | contexte par combattant, combat, événement ou période | futur pipeline |
| Utilisateurs/pronostics | profils, matchmaking et choix des vainqueurs | futur module |

Tous les futurs domaines sportifs doivent référencer `fighters.fightiq_id` et
ne jamais créer une deuxième identité pour le même combattant.
