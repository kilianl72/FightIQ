# FightIQ — journal technique

## 17 août 2026 — sauvegarde consolidée du pipeline fighters

### État de référence préservé

- 4 607 combattants lors du dernier audit connu ;
- 3 182 UFCStats + Cito ;
- 1 406 UFCStats seuls ;
- 19 Cito seuls ;
- consolidation V5.6 validée avec `unresolved = 0` ;
- registre V5.6 sauvegardé : 133 liens, 20 créations/21 profils et 26 exclusions.
- ajout d'un registre `cito_identity_overrides.json` séparé afin de conserver
  V5.6 immuable tout en résolvant les futures quarantaines ;

### Synchronisation des combattants

- fusion des anciens imports, enrichissements, consolidations et audits dans
  `scripts/sync_fighters.py` ;
- préservation des `fightiq_id` et des liens existants ;
- écriture incrémentale des seules différences ;
- backfill des liens primaires `fighter_source_ids` manquants ;
- matching prioritaire par IDs, puis décisions historiques, puis preuves fortes ;
- refus d'une fusion par le nom seul ;
- protection contre les collisions d'IDs et les homonymes ;
- exclusion Power Slap/test/non-MMA ;
- création Cito-only limitée aux preuves MMA fortes ou aux décisions V5.6 ;
- protection empêchant un alias Cito secondaire d'écraser le profil principal ;
- validation des trois catégories canoniques ;
- validation obligatoire `unresolved = 0`.

### Quarantaine non bloquante

- ajout du statut `quarantined` dans le registre de résolution ;
- conservation du profil brut, de la raison et des candidats ;
- aucune insertion du profil douteux dans `fighters` ;
- poursuite des autres mises à jour fiables ;
- réévaluation seulement lorsque le profil ou l'index canonique évolue ;
- conversion des anciennes lignes NULL disparues de Cito en quarantaine explicite.

### Alias vérifiés par l'historique de combats

- ajout des snapshots UFCStats `ufc_fight_results.csv` et
  `ufc_event_details.csv` au contrôle d'identité ;
- chargement à la demande de l'historique Cito
  `/ufc/fighters/{slug}/fights` uniquement pour un nouveau cas sans lien sûr ;
- comparaison des IDs de combats, adversaires, événements, dates et résultats ;
- découverte des candidats par nom complet/inversé, surnom, alias de source,
  accents et translittérations ;
- association possible malgré un nom différent lorsque plusieurs combats et le
  palmarès de ces combats sont compatibles et qu'une preuve biographique
  indépendante concorde ;
- prise en compte d'une date de naissance complète exacte, d'un lieu de
  naissance précis compatible ou d'au moins deux mensurations cohérentes ;
- refus explicite de rendre l'historique décisif sans cette corroboration ;
- pays identique seul et slug identique seul conservés comme indices non
  décisifs ;
- interdiction d'une association fondée sur le record global seul ;
- blocage de deux dates de naissance complètes différentes ou de fortes
  contradictions simultanées de taille/allonge ;
- rejet des résultats/adversaires contradictoires ;
- refus d'attribuer par supposition l'historique de deux homonymes UFCStats ;
- panne individuelle de l'historique Cito rendue non bloquante.

### Revue administrateur préparée

- ajout de la migration
  `202608170001_fighter_identity_admin_review.sql` ;
- ajout de la vue `admin_fighter_identity_queue`, des contraintes et des règles
  RLS administrateur ;
- ajout des décisions `link`, `create`, `exclude` et `needs_info` ;
- revalidation de chaque décision par le synchroniseur avant écriture canonique ;
- ajout de la complétion manuelle sur une liste blanche de champs métriques ;
- classification terminale des profils Power Slap/non-MMA/test/non-combattant
  afin qu'ils ne réapparaissent plus à chaque run ;
- génération de `reports/fighter_identity_quarantine.json` et téléversement
  comme artefact GitHub Actions ;
- ajout de `FIGHTIQ_ADMIN_IDENTITY_REVIEW.md` pour le futur écran de l'app.

### Ranking séparé et sécurisé

- maintien du ranking dans `scripts/enrich_rankings_cito.py` ;
- workflow indépendant `update-rankings.yml` ;
- suppression de la remise à zéro globale avant matching ;
- plan complet calculé avant la première écriture ;
- matching renforcé par UFCStats ID, Cito ID, slug, alias puis nom unique ;
- conservation des anciens rankings si la source est absente ou trop petite ;
- en présence d'un nom non résolu, application des mises à jour sûres et report
  du nettoyage des anciens classés ;
- report automatique du nettoyage si la couverture tombe sous 75 % du nombre
  de combattants précédemment classés ;
- ajout d'un mode `dry_run` manuel.

### Architecture enregistrée

- combattants généraux séparés des rankings ;
- événements/cartes séparés des combats/rounds ;
- navigation future année → événement → combat → round ;
- presse/articles reliés aux combattants, combats, événements et périodes ;
- utilisateurs/pronostics séparés des données sportives ;
- tous les domaines futurs référenceront `fighters.fightiq_id`.

### Vérifications locales

- compilation Python des deux scripts ;
- 37 tests d'identité, historique, alias, biographie, quarantaine, administration,
  validation et ranking ;
- validation de la structure des deux workflows YAML ;
- vérification de l'intégrité du registre JSON et de l'archive finale.

### Limite restante connue

Les écritures Supabase sont planifiées avant application et ne suppriment pas la
base existante, mais elles ne sont pas encore regroupées dans une transaction
SQL unique couvrant `fighters`, `fighter_source_ids` et le registre de
résolution. Une interruption en milieu d'écriture peut laisser un état partiel
que le run suivant doit compléter.
