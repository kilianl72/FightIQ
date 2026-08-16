import csv
import io
import json
import os
import re
import time
import html as html_lib
from collections import Counter
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from supabase import create_client

UFCSTATS_DETAILS_URL = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fighter_details.csv"
UFCSTATS_TOTT_URL = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fighter_tott.csv"
CITO_URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"
PAGE_SIZE = 1000

FIGHTER_FIELDS = (
    "id,fightiq_id,ufcstats_id,cito_id,display_name,first_name,last_name,nickname,"
    "date_of_birth,height_cm,reach_cm,stance,current_weight_kg,"
    "slug,cito_slug,cito_status,is_active,current_division,champion_status,"
    "place_of_birth,trains_at,fighting_style,leg_reach_cm,octagon_debut,"
    "cito_profile_url,photo_url,body_image_url,"
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

PROFILE_FILL_FIELDS = [
    "first_name","last_name","nickname","date_of_birth","height_cm","reach_cm",
    "stance","current_weight_kg","slug","cito_slug","cito_status","is_active",
    "current_division","champion_status","place_of_birth","trains_at",
    "fighting_style","leg_reach_cm","octagon_debut","cito_profile_url",
    "photo_url","body_image_url"
]

CITO_STAT_FIELDS = [
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

def clean(v):
    if v is None:
        return None
    if isinstance(v, str):
        v = v.strip()
        return None if v in {"", "--", "N/A", "null", "None"} else v
    return v

def as_float(v):
    try:
        return float(v) if present(v) else None
    except Exception:
        return None

def as_int(v):
    try:
        return int(float(v)) if present(v) else None
    except Exception:
        return None

def inches_to_cm(v):
    v = as_float(v)
    return round(v * 2.54, 2) if v is not None else None

def lbs_to_kg(v):
    v = as_float(v)
    return round(v * 0.45359237, 2) if v is not None else None

def height_to_cm(v):
    v = clean(v)
    if not v:
        return None
    m = re.match(r"^(\d+)'\s*(\d+)\"$", str(v))
    if not m:
        return None
    feet, inches = map(int, m.groups())
    return round((feet * 12 + inches) * 2.54, 2)

def parse_ufcstats_date(v):
    v = clean(v)
    if not v:
        return None
    try:
        return datetime.strptime(v, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None

def iso_date(v):
    return str(v)[:10] if present(v) else None

def extract_ufcstats_id(url):
    if not url:
        return None
    m = re.search(r"/fighter-details/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None

def nested(obj, *keys):
    cur = obj
    for key in keys:
        if not isinstance(cur, dict):
            return None
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

def download_csv(url):
    req = Request(url, headers={"User-Agent": "FightIQ-profile-completion/1.0"})
    with urlopen(req, timeout=90) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))

def fetch_cito(api_key):
    req = Request(CITO_URL, headers={
        "x-api-key": api_key,
        "User-Agent": "FightIQ-profile-completion/1.0",
        "Accept": "application/json",
    })
    with urlopen(req, timeout=120) as r:
        payload = json.loads(r.read().decode("utf-8"))
    if not payload.get("success"):
        raise RuntimeError("Cito API returned success=false")
    return payload.get("data") or []

def fetch_all(sb, table, fields):
    rows, start = [], 0
    while True:
        batch = sb.table(table).select(fields).range(start, start + PAGE_SIZE - 1).execute().data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        start += PAGE_SIZE
    return rows

def fill_nulls(current, candidate, source, changes, source_counts):
    for field, value in candidate.items():
        if field not in current:
            continue
        if not present(current.get(field)) and present(value):
            current[field] = value
            changes[field] = value
            source_counts[(source, field)] += 1

def cito_candidate(f):
    stats = f.get("stats") or {}
    availability = stats.get("dataAvailability") or {}
    record = f.get("record") or {}
    return {
        "first_name": f.get("firstName"),
        "last_name": f.get("lastName"),
        "nickname": f.get("nickname"),
        "date_of_birth": nested_birth_date(f),
        "height_cm": inches_to_cm(f.get("heightInches")),
        "reach_cm": inches_to_cm(f.get("reachInches")),
        "stance": f.get("stance"),
        "current_weight_kg": lbs_to_kg(f.get("weightLbs")),
        "slug": f.get("slug"),
        "cito_slug": f.get("slug"),
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
        "cito_wins_ko_tko": as_int(nested(stats, "winsByMethod", "ko-tko", "count")),
        "cito_wins_submission": as_int(nested(stats, "winsByMethod", "sub", "count")),
        "cito_wins_decision": as_int(nested(stats, "winsByMethod", "dec", "count")),
        "cito_wins_ko_tko_pct": as_float(nested(stats, "winsByMethod", "ko-tko", "percent")),
        "cito_wins_submission_pct": as_float(nested(stats, "winsByMethod", "sub", "percent")),
        "cito_wins_decision_pct": as_float(nested(stats, "winsByMethod", "dec", "percent")),
        "cito_stats_source": stats.get("source"),
        "cito_stats_freshness": availability.get("dataFreshness"),
        "cito_stats_synced_at": stats.get("lastSyncedAt"),
    }

def build_ufcstats_index():
    details = download_csv(UFCSTATS_DETAILS_URL)
    tott = download_csv(UFCSTATS_TOTT_URL)
    details_by_id = {}
    for row in details:
        uid = extract_ufcstats_id(clean(row.get("URL")))
        if uid:
            details_by_id[uid] = row
    index = {}
    for row in tott:
        uid = extract_ufcstats_id(clean(row.get("URL")))
        if not uid:
            continue
        d = details_by_id.get(uid, {})
        index[uid] = {
            "first_name": clean(d.get("FIRST")),
            "last_name": clean(d.get("LAST")),
            "nickname": clean(d.get("NICKNAME")),
            "date_of_birth": parse_ufcstats_date(row.get("DOB")),
            "height_cm": height_to_cm(row.get("HEIGHT")),
            "reach_cm": inches_to_cm(row.get("REACH")),
            "stance": clean(row.get("STANCE")),
            "current_weight_kg": lbs_to_kg(row.get("WEIGHT")),
        }
    return index

def strip_html(raw):
    raw = re.sub(r"<script\b[^>]*>.*?</script>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<style\b[^>]*>.*?</style>", " ", raw, flags=re.I | re.S)
    raw = re.sub(r"<[^>]+>", " ", raw)
    return re.sub(r"\s+", " ", html_lib.unescape(raw)).strip()

def label_value(text, label, next_labels):
    esc_next = "|".join(re.escape(x) for x in next_labels)
    pattern = rf"\b{re.escape(label)}\b\s*(.*?)(?=\b(?:{esc_next})\b|$)"
    m = re.search(pattern, text, flags=re.I)
    if not m:
        return None
    value = m.group(1).strip(" :-|")
    return value[:180] if value else None

def parse_number(value):
    if not value:
        return None
    m = re.search(r"-?\d+(?:\.\d+)?", value)
    return float(m.group(0)) if m else None

def parse_ufc_date(value):
    if not value:
        return None
    for fmt in ("%b. %d, %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date().isoformat()
        except ValueError:
            pass
    return None

def fetch_ufc_profile(url):
    if not url or "ufc.com/" not in url:
        return {}
    req = Request(url, headers={
        "User-Agent": "Mozilla/5.0 FightIQ/1.0",
        "Accept-Language": "en-US,en;q=0.9",
    })
    with urlopen(req, timeout=25) as r:
        raw = r.read().decode("utf-8", errors="ignore")
    text = strip_html(raw)
    labels = [
        "Status","Place of Birth","Trains at","Fighting style","Age","Height",
        "Weight","Octagon Debut","Reach","Leg reach","Fighter Facts","UFC History"
    ]
    values = {label: label_value(text, label, labels) for label in labels[:-2]}
    return {
        "cito_status": values.get("Status"),
        "is_active": True if (values.get("Status") or "").lower() == "active" else None,
        "place_of_birth": values.get("Place of Birth"),
        "trains_at": values.get("Trains at"),
        "fighting_style": values.get("Fighting style"),
        "height_cm": inches_to_cm(parse_number(values.get("Height"))),
        "current_weight_kg": lbs_to_kg(parse_number(values.get("Weight"))),
        "reach_cm": inches_to_cm(parse_number(values.get("Reach"))),
        "leg_reach_cm": inches_to_cm(parse_number(values.get("Leg reach"))),
        "octagon_debut": parse_ufc_date(values.get("Octagon Debut")),
    }

def main():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    cito_key = os.environ.get("CITO_API_KEY")
    if not url or not key or not cito_key:
        raise RuntimeError("Missing SUPABASE_URL / SUPABASE_SECRET_KEY / CITO_API_KEY")

    sb = create_client(url, key)
    fighters = fetch_all(sb, "fighters", FIGHTER_FIELDS)
    source_rows = fetch_all(sb, "fighter_source_ids", "fightiq_id,source,source_id")

    cito_ids_by_fiq = {}
    for row in source_rows:
        if row.get("source") == "cito":
            cito_ids_by_fiq.setdefault(row["fightiq_id"], []).append(row["source_id"])
    for f in fighters:
        if f.get("cito_id"):
            cito_ids_by_fiq.setdefault(f["fightiq_id"], [])
            if f["cito_id"] not in cito_ids_by_fiq[f["fightiq_id"]]:
                cito_ids_by_fiq[f["fightiq_id"]].append(f["cito_id"])

    print("Downloading UFCStats profile data...")
    ufcstats = build_ufcstats_index()
    print("Downloading Cito fighter data...")
    cito_list = fetch_cito(cito_key)
    cito_by_id = {f.get("id"): f for f in cito_list if f.get("id")}

    tracked = PROFILE_FILL_FIELDS + CITO_STAT_FIELDS
    before_missing = Counter({
        field: sum(1 for f in fighters if not present(f.get(field)))
        for field in tracked
    })

    source_counts = Counter()
    fighters_updated = 0
    direct_ufc_checked = 0
    direct_ufc_success = 0
    direct_ufc_fail = 0
    direct_ufc_limit = int(os.environ.get("UFC_PROFILE_MAX", "1200"))

    for idx, fighter in enumerate(fighters, start=1):
        current = dict(fighter)
        changes = {}

        uid = fighter.get("ufcstats_id")
        if uid and uid in ufcstats:
            fill_nulls(current, ufcstats[uid], "ufcstats", changes, source_counts)

        for cid in cito_ids_by_fiq.get(fighter["fightiq_id"], []):
            cf = cito_by_id.get(cid)
            if cf:
                fill_nulls(current, cito_candidate(cf), "cito", changes, source_counts)

        needs_ufc = any(
            not present(current.get(field))
            for field in [
                "place_of_birth","trains_at","fighting_style","leg_reach_cm",
                "octagon_debut","height_cm","reach_cm","current_weight_kg"
            ]
        )
        profile_url = current.get("cito_profile_url")
        if needs_ufc and profile_url and direct_ufc_checked < direct_ufc_limit:
            direct_ufc_checked += 1
            try:
                direct = fetch_ufc_profile(profile_url)
                if any(present(v) for v in direct.values()):
                    direct_ufc_success += 1
                    fill_nulls(current, direct, "ufc.com", changes, source_counts)
                time.sleep(0.12)
            except Exception as exc:
                direct_ufc_fail += 1
                if direct_ufc_fail <= 10:
                    print(f"UFC.com fetch failed: {profile_url} -> {exc}")

        if changes:
            changes["fightiq_updated_at"] = datetime.now(timezone.utc).isoformat()
            sb.table("fighters").update(changes).eq("fightiq_id", fighter["fightiq_id"]).execute()
            fighter.update(changes)
            fighters_updated += 1

        if idx % 500 == 0:
            print(f"Processed {idx}/{len(fighters)}")

    after_missing = Counter({
        field: sum(1 for f in fighters if not present(f.get(field)))
        for field in tracked
    })

    print("\n===== FIGHTIQ PROFILE COMPLETION =====")
    print(f"fighters_total: {len(fighters)}")
    print(f"fighters_updated: {fighters_updated}")
    print(f"ufc.com_profiles_checked: {direct_ufc_checked}")
    print(f"ufc.com_profiles_with_data: {direct_ufc_success}")
    print(f"ufc.com_fetch_failures: {direct_ufc_fail}")

    print("\n===== FILLS BY SOURCE =====")
    by_source = {}
    for (source, field), count in sorted(source_counts.items()):
        by_source.setdefault(source, {})[field] = count
    for source, fields in by_source.items():
        print(source, json.dumps(fields, ensure_ascii=False, sort_keys=True))

    print("\n===== BEFORE / AFTER MISSING =====")
    total_filled = 0
    for field in tracked:
        gained = before_missing[field] - after_missing[field]
        total_filled += gained
        print(f"{field}: missing {before_missing[field]} -> {after_missing[field]} | filled +{gained}")

    print(f"\ntotal_cells_filled: {total_filled}")
    print("FILL-NULL-ONLY COMPLETE")

if __name__ == "__main__":
    main()
