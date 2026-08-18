-- FightIQ V5.8 — fusion canonique atomique des doublons Cito-only.
--
-- Un combattant conserve exactement un fightiq_id. La correspondance
-- ancienne fiche -> fiche canonique n'existe que dans le lot JSON reçu par la
-- fonction : aucune table de redirection permanente n'est créée.
--
-- La fonction est appelée une seule fois par sync_fighters.py avec la clé
-- service_role. Une exception annule l'intégralité du lot PostgreSQL.

create or replace function public.merge_fighter_identities(p_merges jsonb)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    merge_item jsonb;
    v_source_id text;
    v_target_id text;
    source_primary_cito text;
    source_row public.fighters%rowtype;
    target_row public.fighters%rowtype;
    reference_row record;
    column_row record;
    merged_count integer := 0;
    deleted_count integer := 0;
begin
    if p_merges is null or jsonb_typeof(p_merges) <> 'array' then
        raise exception 'p_merges must be a JSON array';
    end if;

    if jsonb_array_length(p_merges) > 100 then
        raise exception 'Refusing an unexpectedly large fighter merge batch';
    end if;

    for merge_item in
        select value
        from jsonb_array_elements(p_merges)
    loop
        v_source_id := nullif(
            btrim(merge_item ->> 'source_fightiq_id'),
            ''
        );
        v_target_id := nullif(
            btrim(merge_item ->> 'target_fightiq_id'),
            ''
        );

        if v_source_id is null
           or v_target_id is null
           or v_source_id = v_target_id then
            raise exception
                'Invalid fighter merge: % -> %',
                v_source_id,
                v_target_id;
        end if;

        select *
        into source_row
        from public.fighters
        where fightiq_id = v_source_id
        for update;

        if not found then
            raise exception 'Merge source fighter not found: %', v_source_id;
        end if;

        select *
        into target_row
        from public.fighters
        where fightiq_id = v_target_id
        for update;

        if not found then
            raise exception 'Merge target fighter not found: %', v_target_id;
        end if;

        if source_row.ufcstats_id is not null
           or exists (
                select 1
                from public.fighter_source_ids
                where fightiq_id = v_source_id
                  and source = 'ufcstats'
           ) then
            raise exception
                'Refusing to merge UFCStats identity % into %',
                v_source_id,
                v_target_id;
        end if;

        if target_row.ufcstats_id is null
           and not exists (
                select 1
                from public.fighter_source_ids
                where fightiq_id = v_target_id
                  and source = 'ufcstats'
           ) then
            raise exception
                'Canonical merge target has no UFCStats identity: %',
                v_target_id;
        end if;

        if exists (
            select 1
            from public.fighter_source_ids
            where fightiq_id = v_source_id
              and source <> 'cito'
        ) then
            raise exception
                'Duplicate fighter % owns an unexpected non-Cito source',
                v_source_id;
        end if;

        select coalesce(
            source_row.cito_id,
            (
                select identity_source.source_id
                from public.fighter_source_ids as identity_source
                where identity_source.fightiq_id = v_source_id
                  and identity_source.source = 'cito'
                order by
                    identity_source.is_primary desc nulls last,
                    identity_source.source_id
                limit 1
            )
        )
        into source_primary_cito;

        if source_primary_cito is null then
            raise exception
                'Duplicate fighter has no Cito identity: %',
                v_source_id;
        end if;

        -- Libère l'unicité éventuelle de fighters.cito_id avant de rattacher
        -- cet ID à la cible. En cas d'erreur ultérieure, la transaction remet
        -- automatiquement la valeur sur la fiche source.
        update public.fighters
        set cito_id = null
        where fightiq_id = v_source_id;

        -- Transfère chaque valeur utile seulement si la fiche canonique ne la
        -- possède pas déjà. Les identités UFCStats et le fightiq_id cible sont
        -- protégés et ne peuvent jamais être écrasés.
        for column_row in
            select attribute.attname as column_name
            from pg_attribute as attribute
            where attribute.attrelid = 'public.fighters'::regclass
              and attribute.attnum > 0
              and not attribute.attisdropped
              and attribute.attgenerated = ''
              and attribute.attidentity = ''
              and attribute.attname not in (
                  'id',
                  'fightiq_id',
                  'ufcstats_id',
                  'ufc_profile_url',
                  'source_updated_at',
                  'cito_id',
                  'fightiq_updated_at'
              )
        loop
            execute format(
                'update public.fighters as target
                 set %1$I = coalesce(target.%1$I, duplicate.%1$I)
                 from public.fighters as duplicate
                 where target.fightiq_id = $1
                   and duplicate.fightiq_id = $2',
                column_row.column_name
            )
            using v_target_id, v_source_id;
        end loop;

        update public.fighters
        set cito_id = coalesce(cito_id, source_primary_cito),
            fightiq_updated_at = now()
        where fightiq_id = v_target_id;

        -- Les tables connues sont transférées explicitement.
        update public.fighter_source_ids
        set fightiq_id = v_target_id,
            updated_at = now()
        where fightiq_id = v_source_id;

        update public.cito_unmatched_fighters
        set matched_fightiq_id = case
                when matched_fightiq_id = v_source_id
                    then v_target_id
                else matched_fightiq_id
            end,
            matched_ufcstats_id = coalesce(
                matched_ufcstats_id,
                target_row.ufcstats_id
            ),
            target_fightiq_id = case
                when target_fightiq_id = v_source_id
                    then v_target_id
                else target_fightiq_id
            end,
            resolved_at = now()
        where matched_fightiq_id = v_source_id
           or target_fightiq_id = v_source_id;

        -- Toute table future possédant une clé étrangère simple vers
        -- fighters.fightiq_id est également transférée. Une contrainte unique
        -- incompatible provoque une exception et annule tout le lot.
        for reference_row in
            select
                namespace_ref.nspname as schema_name,
                table_ref.relname as table_name,
                column_ref.attname as column_name
            from pg_constraint as constraint_ref
            join pg_class as table_ref
              on table_ref.oid = constraint_ref.conrelid
            join pg_namespace as namespace_ref
              on namespace_ref.oid = table_ref.relnamespace
            join lateral unnest(constraint_ref.conkey) with ordinality
                as local_key(attnum, position)
              on true
            join lateral unnest(constraint_ref.confkey) with ordinality
                as foreign_key(attnum, position)
              on foreign_key.position = local_key.position
            join pg_attribute as column_ref
              on column_ref.attrelid = constraint_ref.conrelid
             and column_ref.attnum = local_key.attnum
            join pg_attribute as target_column
              on target_column.attrelid = constraint_ref.confrelid
             and target_column.attnum = foreign_key.attnum
            where constraint_ref.contype = 'f'
              and constraint_ref.confrelid = 'public.fighters'::regclass
              and target_column.attname = 'fightiq_id'
              and array_length(constraint_ref.conkey, 1) = 1
              and not (
                  namespace_ref.nspname = 'public'
                  and table_ref.relname in (
                      'fighter_source_ids',
                      'cito_unmatched_fighters'
                  )
              )
        loop
            execute format(
                'update %I.%I set %I = $1 where %I = $2',
                reference_row.schema_name,
                reference_row.table_name,
                reference_row.column_name,
                reference_row.column_name
            )
            using v_target_id, v_source_id;
        end loop;

        if exists (
            select 1
            from public.fighter_source_ids
            where fightiq_id = v_source_id
        ) then
            raise exception
                'Source mappings remain on duplicate fighter %',
                v_source_id;
        end if;

        if exists (
            select 1
            from public.cito_unmatched_fighters
            where matched_fightiq_id = v_source_id
               or target_fightiq_id = v_source_id
        ) then
            raise exception
                'Identity review rows still reference duplicate fighter %',
                v_source_id;
        end if;

        delete from public.fighters
        where fightiq_id = v_source_id;

        get diagnostics deleted_count = row_count;
        if deleted_count <> 1 then
            raise exception
                'Expected one deleted duplicate fighter for %, got %',
                v_source_id,
                deleted_count;
        end if;

        update public.fighter_source_ids
        set is_primary = (
            source_id = (
                select cito_id
                from public.fighters
                where fightiq_id = v_target_id
            )
        )
        where fightiq_id = v_target_id
          and source = 'cito';

        merged_count := merged_count + 1;
    end loop;

    return jsonb_build_object(
        'merged',
        merged_count,
        'requested',
        jsonb_array_length(p_merges)
    );
end
$$;

revoke all on function public.merge_fighter_identities(jsonb) from public;
revoke all on function public.merge_fighter_identities(jsonb) from anon;
revoke all on function public.merge_fighter_identities(jsonb) from authenticated;
grant execute on function public.merge_fighter_identities(jsonb) to service_role;
