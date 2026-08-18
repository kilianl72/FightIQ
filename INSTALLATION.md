# FightIQ — exploitation des pipelines de données

Ce document décrit l'ossature active après consolidation. Le dépôt ne contient
plus les anciens scripts ponctuels d'import, d'enrichissement, de consolidation
ou d'audit : leur rôle est couvert par les deux pipelines maintenus.

## Fichiers exécutables

```text
scripts/sync_fighters.py
scripts/enrich_rankings_cito.py
.github/workflows/update-fighters.yml
.github/workflows/update-rankings.yml
```

## Fichiers de données indispensables

```text
data/fighter_identity_registry.json
```

Ce registre unique consolide les décisions V5.6 et toutes les corrections
vérifiées. Chaque ID Cito possède une seule décision finale. Son absence bloque
le workflow afin d'éviter toute reconstruction incomplète.

## Migrations Supabase conservées

```text
supabase/migrations/202608170001_fighter_identity_admin_review.sql
supabase/migrations/202608180001_merge_fighter_identities.sql
```

La première migration fournit la file de revue administrateur. La seconde
fournit la fusion atomique des anciennes fiches Cito-only dupliquées, sans
conserver plusieurs `fightiq_id` pour un même combattant.

Dans l'éditeur Supabase, nommer la seconde migration :

```text
V5.8 - Canonical fighter merges
```

## Premier passage après la correction V5.8

1. Appliquer `202608180001_merge_fighter_identities.sql`.
2. Ouvrir l'onglet **Actions** du dépôt GitHub.
3. Lancer **Sync FightIQ canonical fighter database**.
4. Conserver `dry_run = true`.
5. Contrôler le plan :
   - `unresolved = 0` ;
   - aucune collision d'identité ;
   - `fighter_merges = 12` ;
   - `fighters_total = 4595` ;
   - `cito_only = 7`.
6. Examiner l'artefact administrateur si une quarantaine apparaît.
7. Relancer avec `dry_run = false`.
8. Vérifier le bloc **FINAL DATABASE STATE**.

Le mode dry-run n'écrit aucune ligne Supabase. Le run réel traite les 12
fusions dans une transaction PostgreSQL unique : si une seule fusion est
invalide, le lot entier est annulé.

## Fonctionnement permanent du pipeline combattants

`sync_fighters.py` :

- télécharge les snapshots UFCStats avant toute écriture ;
- télécharge les profils Cito ;
- conserve les `fightiq_id` existants ;
- ne modifie que les fiches nouvelles ou réellement changées ;
- réutilise les liens multi-sources déjà écrits ;
- vérifie les noms complets, inversés, surnoms et translittérations ;
- compare les historiques de combats lorsque le nom ne suffit pas ;
- exige une preuve biographique indépendante avant un lien automatique
  d'alias ;
- exclut les profils Power Slap et non-MMA ;
- place les cas incertains dans la file administrateur ;
- applique les décisions approuvées sans recommencer la base ;
- valide les trois catégories canoniques avant toute écriture.

Une nouvelle fiche Cito MMA sans correspondance certaine n'est jamais créée
silencieusement. Elle attend une décision `link`, `create`, `exclude` ou
`needs_info`.

## Préservation des données en cas d'échec

- Une panne des sources principales bloque le run avant les écritures.
- Une validation invalide bloque le run avant les écritures.
- Les valeurs absentes d'un nouveau snapshot n'effacent pas les anciennes
  valeurs stables.
- Une quarantaine n'empêche pas les autres fiches fiables d'être actualisées.
- Une fusion explicite réaffecte les futures clés étrangères simples pointant
  vers `fighters.fightiq_id`.
- L'ancien `fightiq_id` n'est supprimé qu'après transfert et contrôle de
  toutes les références connues.
- Un conflit entre deux identités UFCStats arrête le traitement.

## Ranking

Le ranking reste dans son propre pipeline :

- exécution hebdomadaire le dimanche à 10 h 30, heure de Paris ;
- calcul du plan complet avant écriture ;
- conservation de l'ancien snapshot si le nouveau est incomplet ;
- aucune remise à zéro globale non validée.

Pour le tester manuellement, lancer **Sync FightIQ UFC rankings** avec
`dry_run = true`, puis avec `dry_run = false` si le plan est cohérent.

## Contrôles locaux

```bash
python -m unittest discover -s tests -v
python -m py_compile scripts/sync_fighters.py scripts/enrich_rankings_cito.py
python -m json.tool data/fighter_identity_registry.json >/dev/null
```

## Domaines suivants

| Domaine | Organisation prévue |
|---|---|
| Combats et rounds | année → événement → combat → round |
| Événements et cartes | passés, à venir, lieux et cartes UFC |
| Presse et articles | combattant, combat, événement ou période |
| Utilisateurs et pronostics | profils, matchmaking et vainqueurs de carte |

Tous ces domaines devront référencer le `fightiq_id` canonique existant et ne
jamais recréer une identité combattant.
