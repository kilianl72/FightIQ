import json
import os
from datetime import datetime, timezone
from urllib.request import Request, urlopen
from supabase import create_client

API_URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"

def present(v):
    return v is not None and (not isinstance(v, str) or bool(v.strip()))

def as_float(v):
    if not present(v): return None
    try: return float(v)
    except: return None

def as_int(v):
    if not present(v): return None
    try: return int(v)
    except: return None

def inches_to_cm(v):
    v = as_float(v)
    return round(v * 2.54, 2) if v is not None else None

def lbs_to_kg(v):
    v = as_float(v)
    return round(v * 0.45359237, 2) if v is not None else None

def iso_date(v):
    return str(v)[:10] if present(v) else None

def nested_value(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict): return None
        cur = cur.get(key)
    return cur

def nested_birth_date(f):
    graph = (((f.get("raw") or {}).get("jsonLd") or {}).get("@graph") or [])
    for item in graph:
        if isinstance(item, dict):
            entity = item.get("mainEntity")
            if isinstance(entity, dict) and present(entity.get("birthDate")):
                return iso_date(entity.get("birthDate"))
    return None

def fetch_cito(api_key):
    req = Request(API_URL, headers={
        "x-api-key": api_key,
        "User-Agent": "FightIQ/5.1",
        "Accept": "application/json",
    })
    with urlopen(req, timeout=120) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if not payload.get("success"):
        raise RuntimeError("Cito API returned success=false")
    return payload.get("data") or []

def fetch_existing(supabase):
    rows, start, size = [], 0, 1000
    fields = (
        "id,fightiq_id,ufcstats_id,display_name,date_of_birth,"
        "height_cm,reach_cm,stance,current_weight_kg,nickname"
    )
    while True:
        batch = (
            supabase.table("fighters")
            .select(fields)
            .range(start, start + size - 1)
            .execute()
        ).data or []
        rows.extend(batch)
        if len(batch) < size: break
        start += size
    return rows

def count_fighters(supabase):
    res = supabase.table("fighters").select("id", count="exact").limit(1).execute()
    return res.count

def build_update(f, current, now):
    stats = f.get("stats") or {}
    availability = stats.get("dataAvailability") or {}
    record = f.get("record") or {}

    update = {
        "cito_id": f.get("id"),
        "cito_slug": f.get("slug"),
        "slug": f.get("slug"),
        "cito_status": f.get("status"),
        "is_active": f.get("isActive"),
        "current_division": f.get("division"),
        "champion_status": f.get("championStatus"),
        "place_of_birth": f.get("placeOfBirth"),
        "trains_at": f.get("trainsAt"),
        "fighting_style": f.get("fightingStyle"),
        "leg_reach_cm": inches_to_cm(f.get("legReachInches")),
        "octagon_debut": iso_date(f.get("octagonDebut")),
        "cito_profile_url": f.get("profileUrl"),
        "photo_url": f.get("proxiedImageUrl"),
        "photo_source": "cito" if f.get("proxiedImageUrl") else None,
        "photo_original_source": "ufc" if f.get("proxiedImageUrl") else None,
        "photo_rights_status": "pending_confirmation" if f.get("proxiedImageUrl") else None,
        "body_image_url": f.get("bodyImageUrl"),
        "cito_record_wins": f.get("recordWins", record.get("wins")),
        "cito_record_losses": f.get("recordLosses", record.get("losses")),
        "cito_record_draws": f.get("recordDraws", record.get("draws")),
        "cito_record_nc": f.get("recordNoContest", record.get("noContest")),
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
        "cito_stats_freshness": availability.get("dataFreshness"),
        "cito_stats_synced_at": stats.get("lastSyncedAt"),
        "cito_synced_at": now,
        "fightiq_updated_at": now,
    }

    if not present(current.get("date_of_birth")):
        update["date_of_birth"] = nested_birth_date(f)
    if not present(current.get("height_cm")):
        update["height_cm"] = inches_to_cm(f.get("heightInches"))
    if not present(current.get("reach_cm")):
        update["reach_cm"] = inches_to_cm(f.get("reachInches"))
    if not present(current.get("stance")):
        update["stance"] = f.get("stance")
    if not present(current.get("current_weight_kg")):
        update["current_weight_kg"] = lbs_to_kg(f.get("weightLbs"))
    if not present(current.get("nickname")):
        update["nickname"] = f.get("nickname")

    return {k: v for k, v in update.items() if v is not None}

def unmatched_row(f, now):
    return {
        "cito_id": f.get("id"),
        "name": f.get("name"),
        "first_name": f.get("firstName"),
        "last_name": f.get("lastName"),
        "nickname": f.get("nickname"),
        "slug": f.get("slug"),
        "division": f.get("division"),
        "status": f.get("status"),
        "is_active": f.get("isActive"),
        "record_text": f.get("recordText"),
        "place_of_birth": f.get("placeOfBirth"),
        "height_inches": as_float(f.get("heightInches")),
        "weight_lbs": as_float(f.get("weightLbs")),
        "reach_inches": as_float(f.get("reachInches")),
        "stance": f.get("stance"),
        "birth_date": nested_birth_date(f),
        "photo_url": f.get("proxiedImageUrl"),
        "profile_url": f.get("profileUrl"),
        "stats_available": bool(f.get("stats")),
        "raw_json": f,
        "last_seen_at": now,
    }

def main():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    cito_key = os.environ.get("CITO_API_KEY")
    if not url or not key or not cito_key:
        raise RuntimeError("Missing required secrets")

    supabase = create_client(url, key)
    before = count_fighters(supabase)
    print(f"Safety check - fighters before Cito: {before}")

    existing_rows = fetch_existing(supabase)
    by_ufcstats = {r["ufcstats_id"]: r for r in existing_rows if r.get("ufcstats_id")}
    print(f"FightIQ UFCStats IDs available: {len(by_ufcstats)}")

    cito = fetch_cito(cito_key)
    print(f"Cito fighters returned: {len(cito)}")

    now = datetime.now(timezone.utc).isoformat()
    matched = updated = 0
    unmatched = []
    missing_from_fightiq = []

    for f in cito:
        uid = f.get("ufcStatsId")
        if not uid:
            unmatched.append(unmatched_row(f, now))
            continue

        current = by_ufcstats.get(uid)
        if not current:
            missing_from_fightiq.append({
                "name": f.get("name"),
                "ufcStatsId": uid,
                "cito_id": f.get("id"),
            })
            continue

        matched += 1
        update = build_update(f, current, now)
        res = (
            supabase.table("fighters")
            .update(update)
            .eq("ufcstats_id", uid)
            .execute()
        )
        if res.data:
            updated += 1

    for row in unmatched:
        supabase.table("cito_unmatched_fighters").upsert(
            row, on_conflict="cito_id"
        ).execute()

    after = count_fighters(supabase)
    print(f"Safety check - fighters after Cito: {after}")

    if before != after:
        raise RuntimeError(
            f"SAFETY FAILURE: fighters count changed during Cito enrichment "
            f"({before} -> {after})"
        )

    print("===== CITO V5.1 UPDATE-ONLY COMPLETE =====")
    print(f"Matched by ufcStatsId: {matched}")
    print(f"Updated existing FightIQ rows: {updated}")
    print(f"Cito without ufcStatsId saved separately: {len(unmatched)}")
    print(f"Cito with ufcStatsId but absent from FightIQ: {len(missing_from_fightiq)}")

    if missing_from_fightiq:
        print("Unexpected missing sample:", missing_from_fightiq[:20])

if __name__ == "__main__":
    main()
