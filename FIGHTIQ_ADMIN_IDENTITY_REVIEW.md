# FightIQ — revue administrateur des identités combattants

> Spécification prête pour la future application. Le backend, la migration et
> le rapport sont préparés ; l'écran n'existe pas encore puisque l'application
> FightIQ n'est pas encore développée.

## Objectif

Une identité incertaine ne doit jamais entrer automatiquement dans `fighters`.
Elle apparaît dans une file réservée aux administrateurs, avec les éléments de
preuve qui ont conduit à la quarantaine. Une décision validée est consommée par
le prochain `sync_fighters.py`.

La page cible est :

```text
/admin/fighter-identities
```

Elle lit la vue Supabase :

```text
public.admin_fighter_identity_queue
```

## Contenu du rapport

Pour chaque profil :

- identité Cito, nom, alias, slug et lien source ;
- date de naissance, mensurations, division et palmarès disponibles ;
- motif de la quarantaine ;
- candidats FightIQ proposés avec leur score et leurs preuves ;
- comparaison explicite des dates et lieux de naissance ;
- mensurations concordantes, écarts calculés et conflits physiques ;
- liste des preuves biographiques indépendantes réellement retenues ;
- nombre de combats Cito examinés ;
- combats communs, IDs de combats communs et résultats compatibles ;
- contradictions éventuelles d'adversaire ou de résultat ;
- signal clair lorsqu'un historique compatible ne dispose d'aucune preuve
  biographique indépendante et ne peut donc pas être lié automatiquement ;
- état de la revue et notes administrateur.

Le workflow produit également l'artefact :

```text
reports/fighter_identity_quarantine.json
```

Cet artefact sert au contrôle avant la construction de l'écran. Supabase reste
la source de vérité des décisions administrateur.

## Actions disponibles

| Action | Usage | Données obligatoires | Résultat du prochain sync |
|---|---|---|---|
| `link` | même personne qu'une fiche existante | `target_fightiq_id` ou `target_ufcstats_id` | ajoute l'ID Cito comme source/alias sans créer de doublon |
| `create` | vrai combattant MMA sans fiche correspondante | classification MMA et, si nécessaire, données manuelles | crée une seule identité déterministe après un dernier contrôle anti-doublon |
| `exclude` | Power Slap, test, non-MMA ou autre non-combattant | classification terminale ; note obligatoire pour `other` | reste hors de `fighters` et ne revient plus dans la file |
| `needs_info` | preuve encore insuffisante | note recommandée | conserve la quarantaine sans bloquer les autres mises à jour |

## Classifications

### Identités à conserver

- `ufc_fighter` ;
- `mma_fighter_non_ufc` ;
- `duplicate_cito_profile` : doit être relié en alias secondaire à la bonne
  fiche, jamais exclu comme s'il ne s'agissait pas d'un combattant.

### Exclusions terminales

- `power_slap` ;
- `non_mma` ;
- `test_placeholder` ;
- `not_a_fighter` ;
- `other`, avec explication obligatoire.

Une exclusion terminale approuvée reçoit `review_status = applied`. Même si le
payload Cito change ensuite, elle ne remonte pas automatiquement dans la file.
Une réouverture doit être une action administrateur explicite.

## Complétion manuelle autorisée

Le formulaire peut renseigner uniquement les champs suivants dans
`manual_profile` :

```text
display_name
first_name
last_name
nickname
date_of_birth
height_cm
reach_cm
current_weight_kg
stance
division
place_of_birth
profile_url
record_wins
record_losses
record_draws
record_nc
```

Ces valeurs comblent les champs manquants. Elles ne doivent pas écraser une
identité fiable déjà enregistrée. Les unités saisies sont métriques.

## Parcours d'une décision

1. L'administrateur ouvre un cas et consulte les preuves.
2. L'application écrit `admin_action`, `review_classification`, la cible ou les
   champs manuels et les notes.
3. L'application place `review_status = approved`.
   Le trigger Supabase renseigne `reviewed_by` avec `auth.uid()` et
   `reviewed_at` automatiquement.
4. Le prochain workflow relit la décision et refait les contrôles d'identité.
5. Si elle est cohérente, le script applique le lien, la création ou
   l'exclusion et place `review_status = applied`.
6. Si la cible manque ou si les IDs se contredisent, la décision devient
   `rejected` avec une raison visible ; aucune identité canonique n'est altérée.

L'interface ne doit jamais écrire directement dans `fighters` ni dans
`fighter_source_ids`. Elle enregistre une décision ; le synchroniseur reste le
seul composant qui modifie l'identité canonique.

## Sécurité

- route et requêtes accessibles uniquement à un utilisateur authentifié avec
  `app_metadata.role = admin` ou `app_metadata.is_admin = true` ;
- clé Cito et clé Supabase service-role uniquement côté serveur ;
- aucune clé dans le navigateur ou le dépôt ;
- journaliser l'administrateur, la date et la note de chaque décision ;
- afficher une confirmation avant `create` et `exclude`.

## Installation du backend

Appliquer avant le nouveau script :

```text
supabase/migrations/202608170001_fighter_identity_admin_review.sql
```

Cette migration ajoute les champs de revue à `cito_unmatched_fighters`, la vue
administrateur, les contraintes, l'index et les règles RLS.
