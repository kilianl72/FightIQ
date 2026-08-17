# FightIQ

FightIQ est un projet d'application d'analyse MMA/UFC reposant sur une identité
canonique unique par combattant et sur des données UFCStats + Cito.

## État actuel

- socle connu : 4 607 combattants ;
- consolidation V5.6 : `unresolved = 0` ;
- identités multi-sources via `fighter_source_ids` ;
- informations générales et statistiques globales disponibles ;
- matching des alias renforcé par les combats et par une preuve biographique
  indépendante (date ou lieu de naissance, ou mensurations concordantes) ;
- backend et rapport de revue administrateur des quarantaines préparés ;
- ranking actuel géré séparément ;
- application et données combats/rounds encore à construire.

## Pipelines

```text
scripts/sync_fighters.py
    identité + profil + statistiques globales

scripts/enrich_rankings_cito.py
    ranking + P4P + champions
```

Les workflows correspondants sont :

```text
.github/workflows/update-fighters.yml
.github/workflows/update-rankings.yml
```

## Documentation

- `START_HERE.txt` : ordre d'installation ;
- `INSTALLATION.md` : déploiement et nettoyage ;
- `FIGHTIQ_PROJECT_CONTEXT.md` : mémoire complète du projet ;
- `FIGHTIQ_DATA_ARCHITECTURE.md` : champs, tables et règles d'identité ;
- `FIGHTIQ_CHANGELOG.md` : travail sauvegardé.

## Principe de fiabilité

La table `fighters` accepte seulement :

1. UFCStats + Cito associés ;
2. UFCStats seul ;
3. Cito seul avec preuve MMA.

Les profils Power Slap/test/non-MMA sont exclus. Les profils ambigus sont placés
en quarantaine hors de `fighters` afin de préserver la base sans bloquer les
autres mises à jour.

Les noms complets, noms inversés, surnoms, accents, translittérations et alias
de source servent à retrouver les candidats. Deux alias peuvent être associés
malgré un nom différent uniquement lorsque plusieurs combats communs et leurs
résultats sont compatibles **et** qu'au moins une preuve indépendante confirme
l'identité : date de naissance exacte, lieu de naissance précis compatible, ou
au moins deux mensurations concordantes. Un pays seul, un slug seul, un nom
seul ou le palmarès global seul ne décide jamais d'une fusion. Toute
contradiction reste en quarantaine.

Il existe un seul script et un seul workflow pour les informations générales,
l'identité et les statistiques globales des combattants :
`scripts/sync_fighters.py` et `.github/workflows/update-fighters.yml`. Le
ranking reste volontairement séparé, car il s'agit d'un snapshot sportif
distinct et non d'un ancien workflow d'identité à conserver.

La future application administrateur utilisera la vue
`admin_fighter_identity_queue`. Sa migration et son contrat sont décrits dans
`FIGHTIQ_ADMIN_IDENTITY_REVIEW.md`. Le fichier
`data/cito_identity_overrides.json` reste un secours versionné, sans modifier le
registre historique V5.6.

## Tests

```bash
python -m unittest discover -s tests -v
```

Ne jamais enregistrer les clés Supabase ou Cito dans le dépôt. Elles doivent
rester dans les secrets GitHub Actions.
