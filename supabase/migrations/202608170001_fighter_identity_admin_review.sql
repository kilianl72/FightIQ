-- FightIQ — revue administrateur des identités Cito en quarantaine.
-- À appliquer avant de déployer la version correspondante de sync_fighters.py.

alter table public.cito_unmatched_fighters
    add column if not exists review_status text,
    add column if not exists review_classification text,
    add column if not exists admin_action text,
    add column if not exists target_fightiq_id text,
    add column if not exists target_ufcstats_id text,
    add column if not exists manual_profile jsonb not null default '{}'::jsonb,
    add column if not exists admin_notes text,
    add column if not exists reviewed_by uuid references auth.users(id),
    add column if not exists reviewed_at timestamptz,
    add column if not exists action_applied_at timestamptz;

do $$
begin
    if not exists (
        select 1
        from pg_constraint
        where conname = 'cito_identity_review_status_check'
          and conrelid = 'public.cito_unmatched_fighters'::regclass
    ) then
        alter table public.cito_unmatched_fighters
            add constraint cito_identity_review_status_check
            check (
                review_status is null
                or review_status in (
                    'pending',
                    'needs_info',
                    'approved',
                    'applied',
                    'rejected',
                    'archived'
                )
            );
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'cito_identity_admin_action_check'
          and conrelid = 'public.cito_unmatched_fighters'::regclass
    ) then
        alter table public.cito_unmatched_fighters
            add constraint cito_identity_admin_action_check
            check (
                admin_action is null
                or admin_action in ('link', 'create', 'exclude', 'needs_info')
            );
    end if;

    if not exists (
        select 1
        from pg_constraint
        where conname = 'cito_identity_review_classification_check'
          and conrelid = 'public.cito_unmatched_fighters'::regclass
    ) then
        alter table public.cito_unmatched_fighters
            add constraint cito_identity_review_classification_check
            check (
                review_classification is null
                or review_classification in (
                    'ufc_fighter',
                    'mma_fighter_non_ufc',
                    'duplicate_cito_profile',
                    'power_slap',
                    'non_mma',
                    'test_placeholder',
                    'not_a_fighter',
                    'other'
                )
            );
    end if;
end
$$;

update public.cito_unmatched_fighters
set review_status = 'pending'
where resolution_status = 'quarantined'
  and review_status is null;

update public.cito_unmatched_fighters
set review_status = 'applied'
where resolution_status in (
        'linked_existing_fighter',
        'created_new_fighter',
        'excluded'
    )
  and review_status is null;

create index if not exists cito_identity_admin_queue_idx
    on public.cito_unmatched_fighters (resolution_status, review_status, last_seen_at desc);

create or replace function public.set_fighter_identity_review_audit()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
begin
    if new.review_status is distinct from old.review_status
       and new.review_status in ('approved', 'needs_info', 'archived') then
        new.reviewed_by := auth.uid();
        new.reviewed_at := now();
    end if;
    return new;
end
$$;

drop trigger if exists set_fighter_identity_review_audit
    on public.cito_unmatched_fighters;
create trigger set_fighter_identity_review_audit
before update of review_status
on public.cito_unmatched_fighters
for each row
execute function public.set_fighter_identity_review_audit();

create or replace view public.admin_fighter_identity_queue
with (security_invoker = true)
as
select
    cito_id,
    name,
    first_name,
    last_name,
    nickname,
    slug,
    division,
    status as cito_status,
    is_active,
    record_text,
    birth_date,
    place_of_birth,
    height_inches,
    weight_lbs,
    reach_inches,
    stance,
    profile_url,
    photo_url,
    resolution_reason,
    coalesce(review_status, 'pending') as review_status,
    review_classification,
    admin_action,
    target_fightiq_id,
    target_ufcstats_id,
    manual_profile,
    admin_notes,
    reviewed_by,
    reviewed_at,
    last_seen_at,
    raw_json #> '{_fightiq_quarantine,candidates}' as candidates,
    raw_json #> '{_fightiq_quarantine,fight_history}' as fight_history
from public.cito_unmatched_fighters
where resolution_status = 'quarantined'
  and coalesce(review_status, 'pending') in (
      'pending',
      'needs_info',
      'approved',
      'rejected'
  );

comment on view public.admin_fighter_identity_queue is
    'File FightIQ réservée aux administrateurs pour résoudre les identités Cito en quarantaine.';

alter table public.cito_unmatched_fighters enable row level security;

drop policy if exists "FightIQ admins read identity reviews"
    on public.cito_unmatched_fighters;
create policy "FightIQ admins read identity reviews"
    on public.cito_unmatched_fighters
    for select
    to authenticated
    using (
        coalesce(auth.jwt() -> 'app_metadata' ->> 'role', '') = 'admin'
        or coalesce(auth.jwt() -> 'app_metadata' ->> 'is_admin', '') = 'true'
    );

drop policy if exists "FightIQ admins update identity reviews"
    on public.cito_unmatched_fighters;
create policy "FightIQ admins update identity reviews"
    on public.cito_unmatched_fighters
    for update
    to authenticated
    using (
        coalesce(auth.jwt() -> 'app_metadata' ->> 'role', '') = 'admin'
        or coalesce(auth.jwt() -> 'app_metadata' ->> 'is_admin', '') = 'true'
    )
    with check (
        coalesce(auth.jwt() -> 'app_metadata' ->> 'role', '') = 'admin'
        or coalesce(auth.jwt() -> 'app_metadata' ->> 'is_admin', '') = 'true'
    );

revoke all on public.admin_fighter_identity_queue from anon;
grant select on public.admin_fighter_identity_queue to authenticated;
grant select on public.cito_unmatched_fighters to authenticated;
revoke update on public.cito_unmatched_fighters from authenticated;
grant update (
    review_status,
    review_classification,
    admin_action,
    target_fightiq_id,
    target_ufcstats_id,
    manual_profile,
    admin_notes
) on public.cito_unmatched_fighters to authenticated;

