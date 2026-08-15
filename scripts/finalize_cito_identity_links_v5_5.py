import json
import os
from datetime import datetime, timezone
from supabase import create_client

MAPPING_FILE = "data/cito_identity_links_v5_5.json"

def main():
    sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SECRET_KEY"])
    with open(MAPPING_FILE, encoding="utf-8") as f:
        payload = json.load(f)

    links = payload["links"]
    classifications = payload["classifications"]
    now = datetime.now(timezone.utc).isoformat()

    before = sb.table("fighters").select("id", count="exact").limit(1).execute().count
    print(f"fighters before: {before}")
    print(f"validated links to apply: {len(links)}")
    print(f"classifications: {len(classifications)}")

    linked = 0
    skipped = 0

    for link in links:
        cito_id = link["cito_id"]
        uid = link["ufcstats_id"]

        target = (
            sb.table("fighters")
            .select("id,ufcstats_id,cito_id,display_name")
            .eq("ufcstats_id", uid)
            .limit(1)
            .execute()
        ).data or []

        if not target:
            print(f"SKIP missing UFCStats target: {link}")
            skipped += 1
            continue

        # Never overwrite a different existing Cito link.
        existing_cito = target[0].get("cito_id")
        if existing_cito and existing_cito != cito_id:
            print(f"SKIP target already has different cito_id: {link}")
            skipped += 1
            continue

        source = (
            sb.table("cito_unmatched_fighters")
            .select("raw_json")
            .eq("cito_id", cito_id)
            .limit(1)
            .execute()
        ).data or []

        raw = (source[0].get("raw_json") if source else None) or {}

        update = {
            "cito_id": cito_id,
            "cito_slug": raw.get("slug"),
            "cito_status": raw.get("status"),
            "is_active": raw.get("isActive"),
            "current_division": raw.get("division"),
            "champion_status": raw.get("championStatus"),
            "place_of_birth": raw.get("placeOfBirth"),
            "trains_at": raw.get("trainsAt"),
            "fighting_style": raw.get("fightingStyle"),
            "cito_profile_url": raw.get("profileUrl"),
            "photo_url": raw.get("proxiedImageUrl"),
            "body_image_url": raw.get("bodyImageUrl"),
            "cito_record_wins": raw.get("recordWins"),
            "cito_record_losses": raw.get("recordLosses"),
            "cito_record_draws": raw.get("recordDraws"),
            "cito_record_nc": raw.get("recordNoContest"),
            "cito_synced_at": now,
            "fightiq_updated_at": now,
        }
        update = {k: v for k, v in update.items() if v is not None}

        (
            sb.table("fighters")
            .update(update)
            .eq("ufcstats_id", uid)
            .execute()
        )

        (
            sb.table("cito_unmatched_fighters")
            .update({
                "resolution_status": "matched",
                "resolution_reason": link["resolution_reason"],
                "matched_ufcstats_id": uid,
                "resolved_at": now,
            })
            .eq("cito_id", cito_id)
            .execute()
        )
        linked += 1

    # Classify every remaining row so operational unresolved count becomes zero.
    for item in classifications:
        if item["status"] == "matched":
            continue
        (
            sb.table("cito_unmatched_fighters")
            .update({
                "resolution_status": item["status"],
                "resolution_reason": item["reason"],
                "matched_ufcstats_id": None,
                "resolved_at": now,
            })
            .eq("cito_id", item["cito_id"])
            .execute()
        )

    after = sb.table("fighters").select("id", count="exact").limit(1).execute().count
    unresolved = (
        sb.table("cito_unmatched_fighters")
        .select("cito_id", count="exact")
        .is_("resolution_status", "null")
        .limit(1)
        .execute()
    ).count

    print(f"fighters after: {after}")
    print(f"linked: {linked}")
    print(f"skipped: {skipped}")
    print(f"operationally unresolved: {unresolved}")

    if before != after:
        raise RuntimeError(f"SAFETY FAILURE: fighters row count changed {before} -> {after}")
    if unresolved != 0:
        raise RuntimeError(f"SAFETY FAILURE: {unresolved} Cito rows still unclassified")

    print("V5.5 FINAL IDENTITY RESOLUTION COMPLETE")

if __name__ == "__main__":
    main()
