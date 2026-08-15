import json
import os
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from supabase import create_client

API_URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"
BATCH_SIZE = 250

def fetch_cito(api_key):
    request = Request(
        API_URL,
        headers={
            "x-api-key": api_key,
            "User-Agent": "FightIQ/5.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=120) as response:
        payload = json.loads(response.read().decode("utf-8"))

    if not payload.get("success"):
        raise RuntimeError("Cito API returned success=false")

    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Cito fighters response")

    return data

def present(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True

def as_float(value):
    if not present(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None

def as_int(value):
    if not present(value):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def inches_to_cm(value):
    value = as_float(value)
    return round(value * 2.54, 2) if value is not None else None

def lbs_to_kg(value):
    value = as_float(value)
    return round(value * 0.45359237, 2) if value is not None else None

def iso_date(value):
    if not present(value):
        return None
    return str(value)[:10]

def nested_birth_date(fighter):
    graph = (((fighter.get("raw") or {}).get("jsonLd") or {}).get("@graph") or [])
    for item in graph:
        if not isinstance(item, dict):
            continue
        entity = item.get("mainEntity")
        if isinstance(entity, dict) and present(entity.get("birthDate")):
            return iso_date(entity.get("birthDate"))
    return None

def nested_value(obj, *keys):
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current

def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]

def fetch_existing_fighters(supabase):
    rows = []
    start = 0
    page_size = 1000
    select = (
        "ufcstats_id,date_of_birth,height_cm,reach_cm,stance,"
        "current_weight_kg,nickname"
    )
    while True:
        response = (
            supabase.table("fighters")
            .select(select)
            .range(start, start + page_size - 1)
            .execute()
        )
        batch = response.data or []
        rows.extend(batch)
        if len(batch) < page_size:
            break
        start += page_size
    return {
        row["ufcstats_id"]: row
        for row in rows
        if row.get("ufcstats_id")
    }

def build_record(fighter, existing, now):
    ufcstats_id = fighter.get("ufcStatsId")
    if not ufcstats_id or ufcstats_id not in existing:
        return None

    current = existing[ufcstats_id]
    stats = fighter.get("stats") or {}
    data_availability = stats.get("dataAvailability") or {}
    wins = fighter.get("record") or {}

    update = {
        "ufcstats_id": ufcstats_id,
        "cito_id": fighter.get("id"),
        "cito_slug": fighter.get("slug"),
        "slug": fighter.get("slug"),
        "cito_status": fighter.get("status"),
        "is_active": fighter.get("isActive"),
        "current_division": fighter.get("division"),
        "champion_status": fighter.get("championStatus"),
        "place_of_birth": fighter.get("placeOfBirth"),
        "trains_at": fighter.get("trainsAt"),
        "fighting_style": fighter.get("fightingStyle"),
        "leg_reach_cm": inches_to_cm(fighter.get("legReachInches")),
        "octagon_debut": iso_date(fighter.get("octagonDebut")),
        "cito_profile_url": fighter.get("profileUrl"),

        "photo_url": fighter.get("proxiedImageUrl"),
        "photo_source": "cito" if fighter.get("proxiedImageUrl") else None,
        "photo_original_source": "ufc" if fighter.get("proxiedImageUrl") else None,
        "photo_rights_status": "pending_confirmation" if fighter.get("proxiedImageUrl") else None,
        "body_image_url": fighter.get("bodyImageUrl"),

        "cito_record_wins": fighter.get("recordWins", wins.get("wins")),
        "cito_record_losses": fighter.get("recordLosses", wins.get("losses")),
        "cito_record_draws": fighter.get("recordDraws", wins.get("draws")),
        "cito_record_nc": fighter.get("recordNoContest", wins.get("noContest")),

        "cito_sig_strikes_landed": as_int(stats.get("significantStrikesLanded")),
        "cito_sig_strikes_attempted": as_int(stats.get("significantStrikesAttempted")),
        "cito_striking_accuracy": as_float(stats.get("strikingAccuracy")),
        "cito_sig_strikes_landed_per_min": as_float(stats.get("sigStrikesLandedPerMin")),
        "cito_sig_strikes_absorbed_per_min": as_float(stats.get("sigStrikesAbsorbedPerMin")),
        "cito_sig_strike_defense": as_float(stats.get("sigStrikeDefense")),

        "cito_takedowns_landed": as_int(stats.get("takedownsLanded")),
        "cito_takedowns_attempted": as_int(stats.get("takedownsAttempted")),
        "cito_takedown_accuracy": as_float(stats.get("takedownAccuracy")),
        "cito_takedown_defense": as_float(stats.get("takedownDefense")),
        "cito_takedown_avg_per_15": as_float(stats.get("takedownAvgPer15Min")),
        "cito_submission_avg_per_15": as_float(stats.get("submissionAvgPer15Min")),
        "cito_knockdown_avg": as_float(stats.get("knockdownAvg")),
        "cito_average_fight_time_seconds": as_int(stats.get("averageFightTimeSeconds")),

        "cito_strikes_head_pct": as_float(nested_value(stats, "sigStrikesByTarget", "head", "percent")),
        "cito_strikes_body_pct": as_float(nested_value(stats, "sigStrikesByTarget", "body", "percent")),
        "cito_strikes_leg_pct": as_float(nested_value(stats, "sigStrikesByTarget", "leg", "percent")),
        "cito_strikes_standing_pct": as_float(nested_value(stats, "sigStrikesByPosition", "standing", "percent")),
        "cito_strikes_clinch_pct": as_float(nested_value(stats, "sigStrikesByPosition", "clinch", "percent")),
        "cito_strikes_ground_pct": as_float(nested_value(stats, "sigStrikesByPosition", "ground", "percent")),

        "cito_wins_ko_tko": as_int(nested_value(stats, "winsByMethod", "ko-tko", "count")),
        "cito_wins_submission": as_int(nested_value(stats, "winsByMethod", "sub", "count")),
        "cito_wins_decision": as_int(nested_value(stats, "winsByMethod", "dec", "count")),
        "cito_wins_ko_tko_pct": as_float(nested_value(stats, "winsByMethod", "ko-tko", "percent")),
        "cito_wins_submission_pct": as_float(nested_value(stats, "winsByMethod", "sub", "percent")),
        "cito_wins_decision_pct": as_float(nested_value(stats, "winsByMethod", "dec", "percent")),

        "cito_stats_source": stats.get("source"),
        "cito_stats_freshness": data_availability.get("dataFreshness"),
        "cito_stats_synced_at": stats.get("lastSyncedAt"),
        "cito_synced_at": now,
        "fightiq_updated_at": now,
    }

    # UFCStats remains authoritative. Cito fills these generic fields only if UFCStats is missing.
    if not present(current.get("date_of_birth")):
        update["date_of_birth"] = nested_birth_date(fighter)

    if not present(current.get("height_cm")):
        update["height_cm"] = inches_to_cm(fighter.get("heightInches"))

    if not present(current.get("reach_cm")):
        update["reach_cm"] = inches_to_cm(fighter.get("reachInches"))

    if not present(current.get("stance")):
        update["stance"] = fighter.get("stance")

    if not present(current.get("current_weight_kg")):
        update["current_weight_kg"] = lbs_to_kg(fighter.get("weightLbs"))

    if not present(current.get("nickname")):
        update["nickname"] = fighter.get("nickname")

    # Avoid overwriting existing values with null.
    return {
        key: value
        for key, value in update.items()
        if value is not None
    }

def main():
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SECRET_KEY")
    cito_key = os.environ.get("CITO_API_KEY")

    if not supabase_url or not supabase_key:
        raise RuntimeError("Missing Supabase secrets")
    if not cito_key:
        raise RuntimeError("Missing CITO_API_KEY")

    supabase = create_client(supabase_url, supabase_key)

    print("Fetching FightIQ fighters...")
    existing = fetch_existing_fighters(supabase)
    print(f"FightIQ UFCStats IDs: {len(existing)}")

    print("Fetching ALL Cito fighters in one API call...")
    cito_fighters = fetch_cito(cito_key)
    print(f"Cito fighters returned: {len(cito_fighters)}")

    now = datetime.now(timezone.utc).isoformat()
    records = []
    no_id = 0
    not_in_fightiq = 0

    for fighter in cito_fighters:
        if not fighter.get("ufcStatsId"):
            no_id += 1
            continue
        record = build_record(fighter, existing, now)
        if record is None:
            not_in_fightiq += 1
            continue
        records.append(record)

    print(f"Direct ufcStatsId matches prepared: {len(records)}")
    print(f"Cito without ufcStatsId skipped: {no_id}")
    print(f"Cito IDs absent from FightIQ skipped: {not_in_fightiq}")

    processed = 0
    for batch in chunks(records, BATCH_SIZE):
        (
            supabase.table("fighters")
            .upsert(batch, on_conflict="ufcstats_id")
            .execute()
        )
        processed += len(batch)
        print(f"Upserted Cito enrichment: {processed}/{len(records)}")

    with_photo = sum(1 for r in records if r.get("photo_url"))
    with_stats = sum(1 for r in records if r.get("cito_sig_strikes_landed_per_min") is not None)
    active = sum(1 for r in records if r.get("is_active") is True)

    print("===== CITO ENRICHMENT COMPLETE =====")
    print(f"Matched fighters: {len(records)}")
    print(f"Active matched fighters: {active}")
    print(f"With proxied photo: {with_photo}")
    print(f"With core Cito stats: {with_stats}")

if __name__ == "__main__":
    main()
