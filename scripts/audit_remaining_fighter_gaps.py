import os
from collections import Counter, defaultdict
from supabase import create_client

PAGE_SIZE = 1000

FIELDS = [
    "fightiq_id","display_name","ufcstats_id","cito_id",
    "date_of_birth","height_cm","reach_cm","stance","current_weight_kg",
    "place_of_birth","trains_at","fighting_style","leg_reach_cm","octagon_debut",
    "photo_url","body_image_url","current_division","is_active",
    "ufc_rank","p4p_rank","champion_status"
]

PRIORITY_FIELDS = [
    "date_of_birth","height_cm","reach_cm","stance","current_weight_kg",
    "place_of_birth","trains_at","fighting_style","leg_reach_cm","octagon_debut",
    "photo_url","body_image_url","current_division"
]

def present(v):
    return v is not None and (not isinstance(v, str) or bool(v.strip()))

def fetch_all(sb, table, fields):
    rows, start = [], 0
    while True:
        batch = (
            sb.table(table)
            .select(",".join(fields))
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        ).data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows

def pct(n, d):
    return round((100*n/d), 1) if d else 0.0

def sample_names(rows, limit=20):
    return [r["display_name"] for r in rows[:limit]]

def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])

    fighters = fetch_all(sb, "fighters", FIELDS)
    sources = fetch_all(sb, "fighter_source_ids", ["fightiq_id","source","source_id"])

    source_map = defaultdict(set)
    for row in sources:
        if row.get("fightiq_id") and row.get("source"):
            source_map[row["fightiq_id"]].add(row["source"])

    total = len(fighters)

    print("===== FIGHTIQ MISSING-DATA AUDIT =====")
    print(f"fighters_total: {total}")

    print("\n===== GLOBAL COVERAGE =====")
    for field in PRIORITY_FIELDS:
        present_rows = [f for f in fighters if present(f.get(field))]
        missing_rows = [f for f in fighters if not present(f.get(field))]
        print(
            f"{field}: present {len(present_rows)} ({pct(len(present_rows), total)}%) "
            f"| missing {len(missing_rows)} ({pct(len(missing_rows), total)}%)"
        )

    print("\n===== MISSING BY SOURCE AVAILABILITY =====")
    for field in PRIORITY_FIELDS:
        missing_rows = [f for f in fighters if not present(f.get(field))]
        buckets = Counter()

        for f in missing_rows:
            fiq = f.get("fightiq_id")
            srcs = source_map.get(fiq, set())

            has_ufcstats = bool(f.get("ufcstats_id")) or "ufcstats" in srcs
            has_cito = bool(f.get("cito_id")) or "cito" in srcs

            if has_ufcstats and has_cito:
                buckets["has_ufcstats_and_cito"] += 1
            elif has_ufcstats:
                buckets["ufcstats_only"] += 1
            elif has_cito:
                buckets["cito_only"] += 1
            else:
                buckets["neither"] += 1

        print(f"{field}: {dict(buckets)}")

    print("\n===== HIGH-VALUE GAPS =====")
    for field in [
        "date_of_birth","reach_cm","stance","place_of_birth",
        "trains_at","fighting_style","leg_reach_cm","octagon_debut","photo_url"
    ]:
        missing_rows = [f for f in fighters if not present(f.get(field))]
        active_missing = [f for f in missing_rows if f.get("is_active") is True]
        ranked_missing = [
            f for f in missing_rows
            if present(f.get("ufc_rank")) or present(f.get("p4p_rank")) or present(f.get("champion_status"))
        ]
        print(
            f"{field}: missing_total={len(missing_rows)} "
            f"| active_missing={len(active_missing)} "
            f"| ranked_or_champion_missing={len(ranked_missing)}"
        )

    print("\n===== SAMPLE MISSING NAMES =====")
    for field in PRIORITY_FIELDS:
        rows = [f for f in fighters if not present(f.get(field))]
        print(f"{field}: {sample_names(rows)}")

    print("\n===== SOURCE SUMMARY =====")
    source_counts = Counter()
    for f in fighters:
        srcs = source_map.get(f.get("fightiq_id"), set())
        has_ufcstats = bool(f.get("ufcstats_id")) or "ufcstats" in srcs
        has_cito = bool(f.get("cito_id")) or "cito" in srcs

        if has_ufcstats and has_cito:
            source_counts["ufcstats_and_cito"] += 1
        elif has_ufcstats:
            source_counts["ufcstats_only"] += 1
        elif has_cito:
            source_counts["cito_only"] += 1
        else:
            source_counts["neither"] += 1

    print(dict(source_counts))
    print("\nAUDIT COMPLETE - READ ONLY")

if __name__ == "__main__":
    main()
