import json
import os
from collections import Counter, defaultdict
from urllib.request import Request, urlopen

from supabase import create_client

CITO_URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"
PAGE_SIZE = 1000

FIELDS = [
    "ufcStatsId",
    "slug",
    "nickname",
    "division",
    "status",
    "isActive",
    "championStatus",
    "p4pRank",
    "recordWins",
    "recordLosses",
    "recordDraws",
    "recordNoContest",
    "country",
    "placeOfBirth",
    "trainsAt",
    "fightingStyle",
    "age",
    "birthDate",
    "heightInches",
    "weightLbs",
    "reachInches",
    "legReachInches",
    "stance",
    "octagonDebut",
    "profileUrl",
    "imageUrl",
    "headshotUrl",
    "bodyImageUrl",
    "proxiedImageUrl",
    "bio",
]

def fetch_cito(api_key):
    request = Request(
        CITO_URL,
        headers={
            "x-api-key": api_key,
            "User-Agent": "FightIQ/2.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=90) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not payload.get("success"):
        raise RuntimeError("Cito API returned success=false")

    fighters = payload.get("data")
    meta = payload.get("meta", {})

    if not isinstance(fighters, list):
        raise RuntimeError("Unexpected Cito fighters response")

    return fighters, meta

def fetch_fightiq_fighters(supabase):
    rows = []
    start = 0

    while True:
        response = (
            supabase
            .table("fighters")
            .select("id,display_name,ufcstats_id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        )

        batch = response.data or []
        rows.extend(batch)

        if len(batch) < PAGE_SIZE:
            break

        start += PAGE_SIZE

    return rows

def value_present(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True

def nested_birth_date(fighter):
    try:
        graph = ((fighter.get("raw") or {}).get("jsonLd") or {}).get("@graph") or []
        for item in graph:
            entity = item.get("mainEntity") if isinstance(item, dict) else None
            if isinstance(entity, dict) and value_present(entity.get("birthDate")):
                return entity.get("birthDate")
    except Exception:
        pass
    return None

def nested_description(fighter):
    try:
        graph = ((fighter.get("raw") or {}).get("jsonLd") or {}).get("@graph") or []
        for item in graph:
            entity = item.get("mainEntity") if isinstance(item, dict) else None
            if isinstance(entity, dict) and value_present(entity.get("description")):
                return entity.get("description")
    except Exception:
        pass
    return None

def pct(count, total):
    return round((count / total) * 100, 1) if total else 0.0

def main():
    cito_key = os.environ.get("CITO_API_KEY")
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SECRET_KEY")

    if not cito_key:
        raise RuntimeError("Missing CITO_API_KEY")
    if not supabase_url or not supabase_key:
        raise RuntimeError("Missing Supabase secrets")

    print("Fetching all Cito UFC fighters in ONE API call...")
    cito_fighters, meta = fetch_cito(cito_key)

    supabase = create_client(supabase_url, supabase_key)
    fightiq_fighters = fetch_fightiq_fighters(supabase)

    fightiq_by_ufcstats = {
        row["ufcstats_id"]: row
        for row in fightiq_fighters
        if row.get("ufcstats_id")
    }

    cito_ids = [f.get("ufcStatsId") for f in cito_fighters if f.get("ufcStatsId")]
    cito_id_counts = Counter(cito_ids)
    duplicate_ids = sorted(
        ufcstats_id for ufcstats_id, count in cito_id_counts.items() if count > 1
    )

    direct_matches = [
        f for f in cito_fighters
        if f.get("ufcStatsId") in fightiq_by_ufcstats
    ]

    cito_with_id_not_in_fightiq = [
        f for f in cito_fighters
        if f.get("ufcStatsId") and f.get("ufcStatsId") not in fightiq_by_ufcstats
    ]

    cito_without_id = [
        f for f in cito_fighters
        if not f.get("ufcStatsId")
    ]

    cito_id_set = set(cito_ids)
    fightiq_missing_from_cito = [
        row for row in fightiq_fighters
        if row.get("ufcstats_id") and row.get("ufcstats_id") not in cito_id_set
    ]

    field_counts = {}
    for field in FIELDS:
        field_counts[field] = sum(
            1 for fighter in cito_fighters
            if value_present(fighter.get(field))
        )

    nested_dob_count = sum(
        1 for fighter in cito_fighters
        if value_present(nested_birth_date(fighter))
    )

    best_dob_count = sum(
        1 for fighter in cito_fighters
        if value_present(fighter.get("birthDate")) or value_present(nested_birth_date(fighter))
    )

    nested_description_count = sum(
        1 for fighter in cito_fighters
        if value_present(nested_description(fighter))
    )

    best_bio_count = sum(
        1 for fighter in cito_fighters
        if value_present(fighter.get("bio")) or value_present(nested_description(fighter))
    )

    active_counts = Counter(
        str(f.get("isActive")) for f in cito_fighters
    )
    status_counts = Counter(
        f.get("status") or "(null)" for f in cito_fighters
    )
    division_counts = Counter(
        f.get("division") or "(null)" for f in cito_fighters
    )

    total_cito = len(cito_fighters)
    total_fightiq = len(fightiq_fighters)

    print("\n===== V2 CITO FULL COVERAGE AUDIT =====")
    print(f"Cito returned: {total_cito}")
    print(f"Cito meta total: {meta.get('total')}")
    print(f"Cito meta totalPages: {meta.get('totalPages')}")
    print(f"FightIQ fighters: {total_fightiq}")
    print()

    print("===== UFCSTATS ID LINKAGE =====")
    print(f"Cito with ufcStatsId: {len(cito_ids)} ({pct(len(cito_ids), total_cito)}%)")
    print(f"Direct Cito -> FightIQ matches: {len(direct_matches)} ({pct(len(direct_matches), total_cito)}%)")
    print(f"Cito without ufcStatsId: {len(cito_without_id)}")
    print(f"Cito ID not found in FightIQ: {len(cito_with_id_not_in_fightiq)}")
    print(f"FightIQ UFCStats fighters absent from Cito: {len(fightiq_missing_from_cito)}")
    print(f"Duplicate Cito ufcStatsId values: {len(duplicate_ids)}")

    if duplicate_ids:
        print("Duplicate ID sample:", duplicate_ids[:20])

    if cito_without_id:
        print("Cito without ID sample:", [f.get("name") for f in cito_without_id[:20]])

    if cito_with_id_not_in_fightiq:
        print(
            "Cito IDs not in FightIQ sample:",
            [(f.get("name"), f.get("ufcStatsId")) for f in cito_with_id_not_in_fightiq[:20]],
        )

    print("\n===== FIELD COVERAGE =====")
    for field in FIELDS:
        count = field_counts[field]
        print(f"{field}: {count}/{total_cito} ({pct(count, total_cito)}%)")

    print(
        f"raw.jsonLd.mainEntity.birthDate: "
        f"{nested_dob_count}/{total_cito} ({pct(nested_dob_count, total_cito)}%)"
    )
    print(
        f"BEST DOB (top-level OR raw JSON-LD): "
        f"{best_dob_count}/{total_cito} ({pct(best_dob_count, total_cito)}%)"
    )
    print(
        f"raw.jsonLd.mainEntity.description: "
        f"{nested_description_count}/{total_cito} ({pct(nested_description_count, total_cito)}%)"
    )
    print(
        f"BEST BIO (top-level OR raw description): "
        f"{best_bio_count}/{total_cito} ({pct(best_bio_count, total_cito)}%)"
    )

    print("\n===== ACTIVE / STATUS =====")
    print("isActive:", dict(active_counts))
    print("status:", dict(status_counts))

    print("\n===== TOP DIVISIONS =====")
    for division, count in division_counts.most_common(20):
        print(f"{division}: {count}")

    print("\n===== PHOTO COVERAGE =====")
    for field in ["imageUrl", "headshotUrl", "bodyImageUrl", "proxiedImageUrl"]:
        count = field_counts[field]
        print(f"{field}: {count}/{total_cito} ({pct(count, total_cito)}%)")

    report = {
        "cito_meta": meta,
        "totals": {
            "cito": total_cito,
            "fightiq": total_fightiq,
            "cito_with_ufcstats_id": len(cito_ids),
            "direct_matches": len(direct_matches),
            "cito_without_ufcstats_id": len(cito_without_id),
            "cito_id_not_in_fightiq": len(cito_with_id_not_in_fightiq),
            "fightiq_absent_from_cito": len(fightiq_missing_from_cito),
            "duplicate_ufcstats_ids": len(duplicate_ids),
        },
        "field_coverage": {
            field: {
                "count": field_counts[field],
                "percent": pct(field_counts[field], total_cito),
            }
            for field in FIELDS
        },
        "derived_coverage": {
            "nested_birth_date": {
                "count": nested_dob_count,
                "percent": pct(nested_dob_count, total_cito),
            },
            "best_birth_date": {
                "count": best_dob_count,
                "percent": pct(best_dob_count, total_cito),
            },
            "nested_description": {
                "count": nested_description_count,
                "percent": pct(nested_description_count, total_cito),
            },
            "best_bio": {
                "count": best_bio_count,
                "percent": pct(best_bio_count, total_cito),
            },
        },
        "duplicate_ufcstats_ids": duplicate_ids,
        "cito_without_ufcstats_id_sample": [
            {"name": f.get("name"), "slug": f.get("slug")}
            for f in cito_without_id[:50]
        ],
        "cito_id_not_in_fightiq_sample": [
            {
                "name": f.get("name"),
                "ufcStatsId": f.get("ufcStatsId"),
                "slug": f.get("slug"),
            }
            for f in cito_with_id_not_in_fightiq[:50]
        ],
        "fightiq_absent_from_cito_sample": [
            {
                "display_name": f.get("display_name"),
                "ufcstats_id": f.get("ufcstats_id"),
            }
            for f in fightiq_missing_from_cito[:50]
        ],
    }

    with open("cito_coverage_v2_report.json", "w", encoding="utf-8") as file:
        json.dump(report, file, ensure_ascii=False, indent=2)

    print("\nReport written to cito_coverage_v2_report.json")
    print("AUDIT ONLY: no Supabase rows were modified.")

if __name__ == "__main__":
    main()
