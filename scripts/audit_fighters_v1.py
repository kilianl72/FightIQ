import os
from collections import Counter
from supabase import create_client

PAGE_SIZE = 1000

FIGHTER_FIELDS = (
    "id,fightiq_id,display_name,ufcstats_id,cito_id,"
    "ufc_rank,p4p_rank,champion_status,is_active,current_division,"
    "photo_url,date_of_birth,height_cm,reach_cm,stance,current_weight_kg,"
    "cito_record_wins,cito_record_losses,cito_record_draws,cito_record_nc,"
    "cito_sig_strikes_landed,cito_sig_strikes_attempted,cito_striking_accuracy,"
    "cito_sig_strikes_landed_per_min,cito_sig_strikes_absorbed_per_min,"
    "cito_sig_strike_defense,cito_takedowns_landed,cito_takedowns_attempted,"
    "cito_takedown_accuracy,cito_takedown_defense,cito_takedown_avg_per_15,"
    "cito_submission_avg_per_15,cito_knockdown_avg,cito_average_fight_time_seconds,"
    "cito_strikes_head_pct,cito_strikes_body_pct,cito_strikes_leg_pct,"
    "cito_strikes_standing_pct,cito_strikes_clinch_pct,cito_strikes_ground_pct,"
    "cito_wins_ko_tko,cito_wins_submission,cito_wins_decision"
)

STAT_FIELDS = [
    "cito_sig_strikes_landed","cito_sig_strikes_attempted","cito_striking_accuracy",
    "cito_sig_strikes_landed_per_min","cito_sig_strikes_absorbed_per_min",
    "cito_sig_strike_defense","cito_takedowns_landed","cito_takedowns_attempted",
    "cito_takedown_accuracy","cito_takedown_defense","cito_takedown_avg_per_15",
    "cito_submission_avg_per_15","cito_knockdown_avg","cito_average_fight_time_seconds",
    "cito_strikes_head_pct","cito_strikes_body_pct","cito_strikes_leg_pct",
    "cito_strikes_standing_pct","cito_strikes_clinch_pct","cito_strikes_ground_pct",
    "cito_wins_ko_tko","cito_wins_submission","cito_wins_decision"
]

def fetch_all(sb, table, fields):
    rows, start = [], 0
    while True:
        batch = sb.table(table).select(fields).range(start, start + PAGE_SIZE - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows

def dup(rows, key):
    c = Counter(r.get(key) for r in rows if r.get(key) is not None)
    return {k:v for k,v in c.items() if v > 1}

def pct(n,d):
    return round(100*n/d,1) if d else 0

def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    fighters = fetch_all(sb, "fighters", FIGHTER_FIELDS)
    sources = fetch_all(sb, "fighter_source_ids", "fightiq_id,source,source_id,source_name,is_primary")
    cito = fetch_all(sb, "cito_unmatched_fighters", "cito_id,name,resolution_status,resolution_reason,matched_fightiq_id,matched_ufcstats_id")

    total = len(fighters)
    fiq_ids = {f.get("fightiq_id") for f in fighters if f.get("fightiq_id")}
    missing_fiq = [f for f in fighters if not f.get("fightiq_id")]
    orphan_sources = [s for s in sources if s.get("fightiq_id") not in fiq_ids]
    res_counts = Counter(r.get("resolution_status") or "NULL" for r in cito)
    src_counts = Counter(s.get("source") or "NULL" for s in sources)

    ufc = sum(1 for f in fighters if f.get("ufcstats_id"))
    cito_primary = sum(1 for f in fighters if f.get("cito_id"))
    both = sum(1 for f in fighters if f.get("ufcstats_id") and f.get("cito_id"))
    any_stats = sum(1 for f in fighters if any(f.get(x) is not None for x in STAT_FIELDS))
    core = ["cito_striking_accuracy","cito_sig_strikes_landed_per_min","cito_sig_strikes_absorbed_per_min",
            "cito_sig_strike_defense","cito_takedown_accuracy","cito_takedown_defense",
            "cito_takedown_avg_per_15","cito_submission_avg_per_15"]
    full_core = sum(1 for f in fighters if all(f.get(x) is not None for x in core))

    print("===== FIGHTIQ FINAL FIGHTER AUDIT =====")
    print(f"fighters_total: {total}")
    print(f"fightiq_id_missing: {len(missing_fiq)}")
    print(f"duplicate_fightiq_id_values: {len(dup(fighters,'fightiq_id'))}")
    print(f"duplicate_ufcstats_id_values: {len(dup(fighters,'ufcstats_id'))}")
    print(f"duplicate_primary_cito_id_values: {len(dup(fighters,'cito_id'))}")
    print(f"fighter_source_ids_total: {len(sources)}")
    print(f"fighter_source_ids_by_source: {dict(src_counts)}")
    print(f"fighter_source_orphans: {len(orphan_sources)}")

    print("\n===== ID COVERAGE =====")
    print(f"with_ufcstats_id: {ufc} ({pct(ufc,total)}%)")
    print(f"with_primary_cito_id: {cito_primary} ({pct(cito_primary,total)}%)")
    print(f"with_both_primary_ids: {both} ({pct(both,total)}%)")
    print(f"cito_only_fighters: {sum(1 for f in fighters if f.get('cito_id') and not f.get('ufcstats_id'))}")
    print(f"ufcstats_only_fighters: {sum(1 for f in fighters if f.get('ufcstats_id') and not f.get('cito_id'))}")

    print("\n===== CITO RESOLUTION =====")
    print(f"cito_resolution_rows: {len(cito)}")
    print(f"resolution_status_counts: {dict(res_counts)}")
    print(f"resolution_null: {res_counts.get('NULL',0)}")

    print("\n===== GLOBAL STAT COVERAGE =====")
    print(f"fighters_with_any_cito_global_stat: {any_stats} ({pct(any_stats,total)}%)")
    print(f"fighters_with_full_core_cito_stats: {full_core} ({pct(full_core,total)}%)")
    for field in STAT_FIELDS:
        n = sum(1 for f in fighters if f.get(field) is not None)
        print(f"{field}: {n} ({pct(n,total)}%)")

    print("\n===== PROFILE COVERAGE =====")
    for field in ["display_name","date_of_birth","height_cm","reach_cm","stance","current_weight_kg",
                  "photo_url","current_division","is_active","ufc_rank","p4p_rank","champion_status"]:
        n = sum(1 for f in fighters if f.get(field) is not None)
        print(f"{field}: {n} ({pct(n,total)}%)")

    errors = []
    if missing_fiq: errors.append("fighters without fightiq_id")
    if dup(fighters,"fightiq_id"): errors.append("duplicate fightiq_id")
    if dup(fighters,"ufcstats_id"): errors.append("duplicate ufcstats_id")
    if dup(fighters,"cito_id"): errors.append("duplicate primary cito_id")
    if orphan_sources: errors.append("orphan fighter_source_ids")
    if res_counts.get("NULL",0): errors.append("unresolved Cito rows")

    if errors:
        print("\nAUDIT RESULT: FAIL")
        for e in errors: print("-", e)
        raise RuntimeError("Final fighter audit failed")

    print("\nAUDIT RESULT: PASS")

if __name__ == "__main__":
    main()
