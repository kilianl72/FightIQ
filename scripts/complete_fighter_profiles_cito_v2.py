import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from supabase import create_client

API_URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"
PAGE_SIZE = 1000

FIGHTER_FIELDS = (
    "id,fightiq_id,display_name,cito_id,first_name,last_name,nickname,date_of_birth,"
    "height_cm,reach_cm,stance,current_weight_kg,slug,cito_slug,cito_status,is_active,"
    "current_division,champion_status,place_of_birth,trains_at,fighting_style,"
    "leg_reach_cm,octagon_debut,cito_profile_url,photo_url,photo_source,"
    "photo_original_source,photo_rights_status,body_image_url,"
    "cito_record_wins,cito_record_losses,cito_record_draws,cito_record_nc,"
    "cito_sig_strikes_landed,cito_sig_strikes_attempted,cito_striking_accuracy,"
    "cito_sig_strikes_landed_per_min,cito_sig_strikes_absorbed_per_min,"
    "cito_sig_strike_defense,cito_takedowns_landed,cito_takedowns_attempted,"
    "cito_takedown_accuracy,cito_takedown_defense,cito_takedown_avg_per_15,"
    "cito_submission_avg_per_15,cito_knockdown_avg,cito_average_fight_time_seconds,"
    "cito_strikes_head_pct,cito_strikes_body_pct,cito_strikes_leg_pct,"
    "cito_strikes_standing_pct,cito_strikes_clinch_pct,cito_strikes_ground_pct,"
    "cito_wins_ko_tko,cito_wins_submission,cito_wins_decision,"
    "cito_wins_ko_tko_pct,cito_wins_submission_pct,cito_wins_decision_pct,"
    "cito_stats_source,cito_stats_freshness,cito_stats_synced_at"
)

TRACKED_FIELDS = [
    "first_name","last_name","nickname","date_of_birth","height_cm","reach_cm",
    "stance","current_weight_kg","slug","cito_slug","cito_status","is_active",
    "current_division","place_of_birth","trains_at","fighting_style",
    "leg_reach_cm","octagon_debut","cito_profile_url","photo_url","body_image_url",
    "cito_record_wins","cito_record_losses","cito_record_draws","cito_record_nc",
    "cito_sig_strikes_landed","cito_sig_strikes_attempted","cito_striking_accuracy",
    "cito_sig_strikes_landed_per_min","cito_sig_strikes_absorbed_per_min",
    "cito_sig_strike_defense","cito_takedowns_landed","cito_takedowns_attempted",
    "cito_takedown_accuracy","cito_takedown_defense","cito_takedown_avg_per_15",
    "cito_submission_avg_per_15","cito_knockdown_avg","cito_average_fight_time_seconds",
    "cito_strikes_head_pct","cito_strikes_body_pct","cito_strikes_leg_pct",
    "cito_strikes_standing_pct","cito_strikes_clinch_pct","cito_strikes_ground_pct",
    "cito_wins_ko_tko","cito_wins_submission","cito_wins_decision",
    "cito_wins_ko_tko_pct","cito_wins_submission_pct","cito_wins_decision_pct",
    "cito_stats_source","cito_stats_freshness","cito_stats_synced_at"
]

def present(v):
    return v is not None and (not isinstance(v, str) or bool(v.strip()))

def as_float(v):
    if not present(v):
        return None
    try:
        return float(v)
    except Exception:
        return None

def as_int(v):
    if not present(v):
        return None
    try:
        return int(float(v))
    except Exception:
        return None

def inches_to_cm(v):
    v = as_float(v)
    if v is None or v <= 0:
        return None
    return round(v * 2.54, 2)

def lbs_to_kg(v):
    v = as_float(v)
    if v is None or v <= 0:
        return None
    return round(v * 0.45359237, 2)

def iso_date(v):
    return str(v)[:10] if present(v) else None

def nested(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur

def birth_date_from_raw(f):
    if present(f.get("birthDate")):
        return iso_date(f.get("birthDate"))
    graph = (((f.get("raw") or {}).get("jsonLd") or {}).get("@graph") or [])
    for item in graph:
        if not isinstance(item, dict):
            continue
        entity = item.get("mainEntity")
        if isinstance(entity, dict) and present(entity.get("birthDate")):
            return iso_date(entity.get("birthDate"))
    return None

def raw_bio(f):
    raw = f.get("raw") or {}
    bio = raw.get("bioFields") or {}
    if not isinstance(bio, dict):
        return {}
    return {str(k).strip().lower(): v for k, v in bio.items()}

def bio_value(bio, *keys):
    for key in keys:
        value = bio.get(key.lower())
        if present(value):
            return value
    return None

def parse_ufc_text_date(v):
    if not present(v):
        return None
    from datetime import datetime
    s = str(v).strip()
    for fmt in ("%b. %d, %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).date().isoformat()
        except ValueError:
            pass
    return None

def fetch_cito(api_key):
    req = Request(API_URL, headers={
        "x-api-key": api_key,
        "User-Agent": "FightIQ-cito-completion/2.0",
        "Accept": "application/json",
    })
    with urlopen(req, timeout=120) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if not payload.get("success"):
        raise RuntimeError("Cito API returned success=false")
    return payload.get("data") or []

def fetch_all(sb, table, fields):
    rows = []
    start = 0
    while True:
        batch = (
            sb.table(table)
            .select(fields)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        ).data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows

def cito_candidate(f):
    stats = f.get("stats") or {}
    availability = stats.get("dataAvailability") or {}
    record = f.get("record") or {}
    bio = raw_bio(f)
    hero = ((f.get("raw") or {}).get("heroStats") or {})

    photo = (
        f.get("proxiedImageUrl")
        or f.get("headshotUrl")
        or f.get("imageUrl")
    )

    return {
        "first_name": f.get("firstName"),
        "last_name": f.get("lastName"),
        "nickname": f.get("nickname"),
        "date_of_birth": birth_date_from_raw(f),
        "height_cm": inches_to_cm(f.get("heightInches") or bio_value(bio, "height")),
        "reach_cm": inches_to_cm(f.get("reachInches") or bio_value(bio, "reach")),
        "stance": f.get("stance") or bio_value(bio, "stance"),
        "current_weight_kg": lbs_to_kg(f.get("weightLbs") or bio_value(bio, "weight")),
        "slug": f.get("slug"),
        "cito_slug": f.get("slug"),
        "cito_status": f.get("status") or bio_value(bio, "status"),
        "is_active": f.get("isActive"),
        "current_division": f.get("division"),
        "place_of_birth": f.get("placeOfBirth") or bio_value(bio, "place of birth"),
        "trains_at": f.get("trainsAt") or bio_value(bio, "trains at"),
        "fighting_style": f.get("fightingStyle") or bio_value(bio, "fighting style"),
        "leg_reach_cm": inches_to_cm(f.get("legReachInches") or bio_value(bio, "leg reach")),
        "octagon_debut": iso_date(f.get("octagonDebut")) or parse_ufc_text_date(bio_value(bio, "octagon debut")),
        "cito_profile_url": f.get("profileUrl") or f.get("sourceUrl"),
        "photo_url": photo,
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
        "cito_strikes_head_pct": as_float(nested(stats, "sigStrikesByTarget", "head", "percent")),
        "cito_strikes_body_pct": as_float(nested(stats, "sigStrikesByTarget", "body", "percent")),
        "cito_strikes_leg_pct": as_float(nested(stats, "sigStrikesByTarget", "leg", "percent")),
        "cito_strikes_standing_pct": as_float(nested(stats, "sigStrikesByPosition", "standing", "percent")),
        "cito_strikes_clinch_pct": as_float(nested(stats, "sigStrikesByPosition", "clinch", "percent")),
        "cito_strikes_ground_pct": as_float(nested(stats, "sigStrikesByPosition", "ground", "percent")),
        "cito_wins_ko_tko": as_int(nested(stats, "winsByMethod", "ko-tko", "count") or hero.get("wins by knockout")),
        "cito_wins_submission": as_int(nested(stats, "winsByMethod", "sub", "count") or hero.get("wins by submission")),
        "cito_wins_decision": as_int(nested(stats, "winsByMethod", "dec", "count")),
        "cito_wins_ko_tko_pct": as_float(nested(stats, "winsByMethod", "ko-tko", "percent")),
        "cito_wins_submission_pct": as_float(nested(stats, "winsByMethod", "sub", "percent")),
        "cito_wins_decision_pct": as_float(nested(stats, "winsByMethod", "dec", "percent")),
        "cito_stats_source": stats.get("source"),
        "cito_stats_freshness": availability.get("dataFreshness") or f.get("dataFreshness"),
        "cito_stats_synced_at": stats.get("lastSyncedAt") or f.get("lastSyncedAt"),
    }

def fill_nulls(current, candidate, changes, counts):
    for field, value in candidate.items():
        if field not in current:
            continue
        if not present(current.get(field)) and present(value):
            current[field] = value
            changes[field] = value
            counts[field] += 1

def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    fighters = fetch_all(sb, "fighters", FIGHTER_FIELDS)
    source_rows = fetch_all(sb, "fighter_source_ids", "fightiq_id,source,source_id")
    cito = fetch_cito(os.environ["CITO_API_KEY"])
    cito_by_id = {f.get("id"): f for f in cito if f.get("id")}

    cito_ids_by_fiq = defaultdict(list)
    for row in source_rows:
        if row.get("source") == "cito":
            cito_ids_by_fiq[row["fightiq_id"]].append(row["source_id"])
    for fighter in fighters:
        cid = fighter.get("cito_id")
        if cid and cid not in cito_ids_by_fiq[fighter["fightiq_id"]]:
            cito_ids_by_fiq[fighter["fightiq_id"]].append(cid)

    before_missing = Counter({
        field: sum(1 for f in fighters if not present(f.get(field)))
        for field in TRACKED_FIELDS
    })

    counts = Counter()
    updated = 0
    mapped_profiles = 0
    now = datetime.now(timezone.utc).isoformat()

    # champion_status is maintained by the rankings workflow.
    # Clean the placeholder string written by older Cito profile runs.
    sb.table("fighters").update({"champion_status": None}).ilike("champion_status", "none").execute()

    for index, fighter in enumerate(fighters, start=1):
        current = dict(fighter)
        changes = {}

        for cid in cito_ids_by_fiq.get(fighter["fightiq_id"], []):
            cf = cito_by_id.get(cid)
            if not cf:
                continue
            mapped_profiles += 1
            fill_nulls(current, cito_candidate(cf), changes, counts)

        if changes:
            if "photo_url" in changes:
                changes["photo_source"] = "cito"
                changes["photo_original_source"] = "ufc"
                changes["photo_rights_status"] = "pending_confirmation"
            changes["cito_synced_at"] = now
            changes["fightiq_updated_at"] = now
            sb.table("fighters").update(changes).eq("fightiq_id", fighter["fightiq_id"]).execute()
            fighter.update(changes)
            updated += 1

        if index % 500 == 0:
            print(f"Processed {index}/{len(fighters)}")

    after_missing = Counter({
        field: sum(1 for f in fighters if not present(f.get(field)))
        for field in TRACKED_FIELDS
    })

    print("===== CITO RAW COMPLETION V2 =====")
    print(f"fighters_total: {len(fighters)}")
    print(f"cito_api_profiles: {len(cito)}")
    print(f"mapped_cito_profiles_processed: {mapped_profiles}")
    print(f"fighters_updated: {updated}")

    print("\n===== FILLED CELLS =====")
    total = 0
    for field in TRACKED_FIELDS:
        gained = before_missing[field] - after_missing[field]
        if gained:
            print(f"{field}: missing {before_missing[field]} -> {after_missing[field]} | filled +{gained}")
            total += gained

    print(f"total_cells_filled: {total}")
    print("champion_status_source: rankings workflow")
    print("CITO RAW COMPLETION COMPLETE")

if __name__ == "__main__":
    main()
