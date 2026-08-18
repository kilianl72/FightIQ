# FightIQ

FightIQ est une application d'analyse MMA/UFC construite autour d'une identité
canonique unique par combattant et de données UFCStats + Cito.

## État de référence

Le dernier état Supabase contrôlé avant la correction des doublons V5.6 était :

- 4 607 combattants ;
- 3 185 UFCStats + Cito ;
- 1 403 UFCStats uniquement ;
- 19 Cito uniquement ;
- 0 identité non résolue.

L'analyse des 19 fiches Cito-only a identifié 12 anciennes fiches dupliquées
qui doivent rejoindre un combattant UFCStats existant. Après la fusion, la cible
est donc de **4 595 combattants**, dont **7 Cito-only légitimes**. Ces 7 profils
ont une activité MMA documentée mais aucun combat officiel présent dans
UFCStats.

## Ossature active

| Domaine | Script | Workflow |
|---|---|---|
| Identité, profil et statistiques globales | `scripts/sync_fighters.py` | `.github/workflows/update-fighters.yml` |
| Rankings, P4P et champions | `scripts/enrich_rankings_cito.py` | `.github/workflows/update-rankings.yml` |

Le ranking reste volontairement séparé : il s'agit d'un état sportif
hebdomadaire, pas d'un ancien doublon du pipeline combattants.

## Registre d'identité

`data/fighter_identity_registry.json` contient toutes les décisions validées :
liens Cito/UFCStats, créations Cito-only légitimes et exclusions. Il consolide
le registre V5.6 et les corrections ultérieures, avec une seule décision finale
par ID Cito. Supabase reste la source de vérité en production ; ce registre
versionné permet de reconstruire les associations sans refaire les recherches.

## Fiabilité canonique

Une fiche `fighters` doit appartenir à une seule de ces catégories :

1. UFCStats + Cito ;
2. UFCStats uniquement ;
3. Cito uniquement avec preuve MMA suffisante.

Les profils Power Slap, tests et non-MMA sont exclus. Une identité ambiguë reste
hors de `fighters` dans la file administrateur, sans bloquer les mises à jour
sûres.

Un nom, un slug ou un palmarès global identique ne suffit jamais à fusionner.
Les alias sont confirmés par l'historique des combats et une preuve indépendante
compatible : date de naissance, lieu de naissance précis ou plusieurs
mensurations. Toute contradiction bloque le rapprochement.

Les corrections explicitement validées peuvent fusionner une ancienne fiche
Cito-only dupliquée vers l'identité UFCStats canonique. La fusion est réalisée
dans une transaction Supabase unique. Les données et références sont déplacées
avant la suppression de l'ancien `fightiq_id` ; aucune redirection permanente
n'est conservée. Le mécanisme refuse de supprimer une fiche qui possède déjà
une identité UFCStats.

## Mise en service de la correction

1. Appliquer la migration
   `supabase/migrations/202608180001_merge_fighter_identities.sql` dans
   Supabase sous le nom **V5.8 - Canonical fighter merges**.
2. Lancer **Sync FightIQ canonical fighter database** avec
   `dry_run = true`.
3. Vérifier notamment : `unresolved = 0`, `fighter_merges = 12`,
   `fighters_total = 4595` et `cito_only = 7`.
4. Examiner toute quarantaine éventuellement apparue depuis le dernier run.
5. Relancer le même workflow avec `dry_run = false`.
6. Vérifier le rapport final relu directement depuis Supabase.

Le workflow combattants tourne ensuite quotidiennement. Le ranking tourne une
fois par semaine, le dimanche à 10 h 30, heure de Paris.

## Documentation

- `FIGHTIQ_PROJECT_CONTEXT.md` : mémoire complète, objectifs et prochaines
  étapes ;
- `FIGHTIQ_DATA_ARCHITECTURE.md` : tables, champs et règles d'identité ;
- `FIGHTIQ_ADMIN_IDENTITY_REVIEW.md` : fonctionnement de la file
  administrateur ;
- `FIGHTIQ_CHANGELOG.md` : historique technique ;
- `INSTALLATION.md` : exploitation, contrôles et reprise en cas d'échec.

## Tests

```bash
python -m unittest discover -s tests -v
python -m py_compile scripts/sync_fighters.py scripts/enrich_rankings_cito.py
```

Les clés Supabase et Cito restent exclusivement dans les secrets GitHub
Actions.
