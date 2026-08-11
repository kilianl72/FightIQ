import csv
import io
import os
import re
from datetime import datetime, timezone
from urllib.request import Request, urlopen

from supabase import create_client

DETAILS_URL = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fighter_details.csv"
TOTT_URL = "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/main/ufc_fighter_tott.csv"
BATCH_SIZE = 500

def download_csv(url):
    req = Request(url, headers={"User-Agent": "FightIQ-data-importer/1.0"})
    with urlopen(req, timeout=60) as r:
        return list(csv.DictReader(io.StringIO(r.read().decode("utf-8-sig"))))

def clean(v):
    if v is None:
        return None
    v = v.strip()
    return None if v in {"", "--", "N/A"} else v

def extract_id(url):
    if not url:
        return None
    m = re.search(r"/fighter-details/([A-Za-z0-9]+)", url)
    return m.group(1) if m else None

def height_to_cm(v):
    v = clean(v)
    if not v:
        return None
    m = re.match(r"""^(\d+)'\s*(\d+)"$""", v)
    if not m:
        return None
    feet, inches = map(int, m.groups())
    return round((feet * 12 + inches) * 2.54, 2)

def inches_to_cm(v):
    v = clean(v)
    if not v:
        return None
    m = re.search(r"([\d.]+)", v)
    return round(float(m.group(1)) * 2.54, 2) if m else None

def lbs_to_kg(v):
    v = clean(v)
    if not v:
        return None
    m = re.search(r"([\d.]+)", v)
    return round(float(m.group(1)) * 0.45359237, 2) if m else None

def parse_date(v):
    v = clean(v)
    if not v:
        return None
    try:
        return datetime.strptime(v, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None

def build_records(details_rows, tott_rows):
    details_by_id = {}
    for row in details_rows:
        ext_id = extract_id(clean(row.get("URL")))
        if ext_id:
            details_by_id[ext_id] = row

    now = datetime.now(timezone.utc).isoformat()
    records = []

    for row in tott_rows:
        profile_url = clean(row.get("URL"))
        ext_id = extract_id(profile_url)
        if not ext_id:
            continue

        details = details_by_id.get(ext_id, {})
        first_name = clean(details.get("FIRST"))
        last_name = clean(details.get("LAST"))
        display_name = clean(row.get("FIGHTER")) or " ".join(
            p for p in [first_name, last_name] if p
        ).strip()

        if not display_name:
            continue

        records.append({
            "fightiq_id": f"fiq_{ext_id}",
            "first_name": first_name,
            "last_name": last_name,
            "display_name": display_name,
            "nickname": clean(details.get("NICKNAME")),
            "date_of_birth": parse_date(row.get("DOB")),
            "height_cm": height_to_cm(row.get("HEIGHT")),
            "reach_cm": inches_to_cm(row.get("REACH")),
            "stance": clean(row.get("STANCE")),
            "current_weight_kg": lbs_to_kg(row.get("WEIGHT")),
            "ufcstats_id": ext_id,
            "ufc_profile_url": profile_url,
            "source_updated_at": now,
            "fightiq_updated_at": now,
        })
    return records

def chunks(items, size):
    for i in range(0, len(items), size):
        yield items[i:i + size]

def main():
    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SECRET_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SECRET_KEY")

    records = build_records(download_csv(DETAILS_URL), download_csv(TOTT_URL))
    print(f"Prepared {len(records)} fighters")

    supabase = create_client(url, key)
    total = 0
    for batch in chunks(records, BATCH_SIZE):
        supabase.table("fighters").upsert(batch, on_conflict="ufcstats_id").execute()
        total += len(batch)
        print(f"Upserted {total}/{len(records)}")

    print(f"FightIQ import complete: {total} fighters processed")

if __name__ == "__main__":
    main()
