import json
import os
import re
import unicodedata
from datetime import datetime, timezone

from supabase import create_client

RESOLUTION_FILE = "data/cito_identity_resolution_v5_6.json"


def norm(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(c for c in value if not unicodedata.combining(c)).lower()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def cm_from_inches(value):
    if value is None:
        return None
    try:
        return round(float(value) * 2.54, 2)
    except Exception:
        return None


def kg_from_lbs(value):
    if value is None:
        return None
    try:
        return round(float(value) * 0.45359237, 2)
    except Exception:
        return None


def source_upsert(sb, fightiq_id, source, source_id, source_name=None, is_primary=False):
    sb.table("fighter_source_ids").upsert(
        {
            "fightiq_id": fightiq_id,
            "source": source,
            "source_id": source_id,
            "source_name": source_name,
            "is_primary": is_primary,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="source,source_id",
    ).execute()


def get_cito(sb, cito_id):
    rows = (
        sb.table("cito_unmatched_fighters")
        .select(
            "cito_id,name,first_name,last_name,nickname,slug,division,status,is_active,"
            "record_text,place_of_birth,height_inches,weight_lbs,reach_inches,stance,"
            "birth_date,photo_url,profile_url,stats_available,raw_json"
        )
        .eq("cito_id", cito_id)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


def fighter_by_ufcstats(sb, ufcstats_id):
    rows = (
        sb.table("fighters")
        .select("id,fightiq_id,display_name,ufcstats_id,cito_id")
        .eq("ufcstats_id", ufcstats_id)
        .limit(1)
        .execute()
    ).data or []
    return rows[0] if rows else None


def all_fighters(sb):
    out = []
    start = 0
    size = 1000
    while True:
        batch = (
            sb.table("fighters")
            .select("id,fightiq_id,display_name,ufcstats_id,cito_id")
            .range(start, start + size - 1)
            .execute()
        ).data or []
        out.extend(batch)
        if len(batch) < size:
            break
        start += size
    return out


def mark_cito(sb, cito_id, status, reason, fightiq_id=None, ufcstats_id=None):
    sb.table("cito_unmatched_fighters").update(
        {
            "resolution_status": status,
            "resolution_reason": reason,
            "matched_fightiq_id": fightiq_id,
            "matched_ufcstats_id": ufcstats_id,
            "resolved_at": datetime.now(timezone.utc).isoformat(),
        }
    ).eq("cito_id", cito_id).execute()


def main():
    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )

    with open(RESOLUTION_FILE, encoding="utf-8") as fh:
        plan = json.load(fh)

    before = (
        sb.table("fighters")
        .select("id", count="exact")
        .limit(1)
        .execute()
        .count
    )

    print("===== FIGHTIQ V5.6 =====")
    print(json.dumps(plan["summary"], ensure_ascii=False))

    linked = 0
    created = 0
    duplicate_avoided = 0
    excluded = 0

    # 1) Re-associate every verified Cito identity with the existing canonical fighter.
    for item in plan["links"]:
        target = fighter_by_ufcstats(sb, item["ufcstats_id"])
        if not target:
            raise RuntimeError(
                f"Verified UFCStats target missing: {item['cito_name']} -> "
                f"{item['ufcstats_name']} ({item['ufcstats_id']})"
            )

        fightiq_id = target["fightiq_id"]

        source_upsert(
            sb, fightiq_id, "ufcstats", item["ufcstats_id"],
            item["ufcstats_name"], True
        )
        source_upsert(
            sb, fightiq_id, "cito", item["cito_id"],
            item["cito_name"], target.get("cito_id") == item["cito_id"]
        )

        # Keep fighters.cito_id as its existing primary Cito ID.
        # Only fill it when the fighter does not have one yet.
        if not target.get("cito_id"):
            cito = get_cito(sb, item["cito_id"]) or {}
            raw = cito.get("raw_json") or {}
            update = {
                "cito_id": item["cito_id"],
                "cito_slug": cito.get("slug") or raw.get("slug"),
                "cito_status": cito.get("status") or raw.get("status"),
                "is_active": cito.get("is_active"),
                "current_division": cito.get("division"),
                "champion_status": raw.get("championStatus"),
                "place_of_birth": cito.get("place_of_birth"),
                "trains_at": raw.get("trainsAt"),
                "fighting_style": raw.get("fightingStyle"),
                "cito_profile_url": cito.get("profile_url"),
                "photo_url": cito.get("photo_url") or raw.get("proxiedImageUrl"),
                "body_image_url": raw.get("bodyImageUrl"),
                "cito_record_wins": raw.get("recordWins"),
                "cito_record_losses": raw.get("recordLosses"),
                "cito_record_draws": raw.get("recordDraws"),
                "cito_record_nc": raw.get("recordNoContest"),
                "cito_synced_at": datetime.now(timezone.utc).isoformat(),
                "fightiq_updated_at": datetime.now(timezone.utc).isoformat(),
            }
            update = {k: v for k, v in update.items() if v is not None}
            sb.table("fighters").update(update).eq("fightiq_id", fightiq_id).execute()

        mark_cito(
            sb,
            item["cito_id"],
            "linked_existing_fighter",
            item["resolution_reason"],
            fightiq_id=fightiq_id,
            ufcstats_id=item["ufcstats_id"],
        )
        linked += 1

    # Rebuild exact-name index after verified links.
    fighters = all_fighters(sb)
    by_name = {}
    for fighter in fighters:
        n = norm(fighter.get("display_name"))
        if n:
            by_name.setdefault(n, []).append(fighter)

    # 2) Create only fighters with a documented MMA bout and no safe existing match.
    for item in plan["creates"]:
        canonical_cito = get_cito(sb, item["canonical_cito_id"])
        if not canonical_cito:
            raise RuntimeError(f"Missing Cito source row: {item}")

        intended_name = item["display_name"]
        existing_same_name = by_name.get(norm(intended_name), [])

        # Final anti-duplicate barrier.
        if len(existing_same_name) == 1:
            target = existing_same_name[0]
            fightiq_id = target["fightiq_id"]
            for cid, cname in zip(item["cito_ids"], item["cito_names"]):
                source_upsert(sb, fightiq_id, "cito", cid, cname, False)
                mark_cito(
                    sb, cid, "linked_existing_fighter",
                    "v5_6_exact_name_duplicate_avoided",
                    fightiq_id=fightiq_id,
                    ufcstats_id=target.get("ufcstats_id"),
                )
            duplicate_avoided += 1
            continue
        elif len(existing_same_name) > 1:
            raise RuntimeError(
                f"Unsafe create: multiple existing fighters named {intended_name}"
            )

        raw = canonical_cito.get("raw_json") or {}
        fightiq_id = "fiq_cito_" + item["canonical_cito_id"].replace("-", "")

        record = {
            "fightiq_id": fightiq_id,
            "first_name": canonical_cito.get("first_name"),
            "last_name": canonical_cito.get("last_name"),
            "display_name": intended_name,
            "nickname": canonical_cito.get("nickname"),
            "date_of_birth": canonical_cito.get("birth_date"),
            "height_cm": cm_from_inches(canonical_cito.get("height_inches")),
            "reach_cm": cm_from_inches(canonical_cito.get("reach_inches")),
            "stance": canonical_cito.get("stance"),
            "current_weight_kg": kg_from_lbs(canonical_cito.get("weight_lbs")),
            "ufcstats_id": None,
            "cito_id": item["canonical_cito_id"],
            "cito_slug": canonical_cito.get("slug") or raw.get("slug"),
            "cito_status": canonical_cito.get("status") or raw.get("status"),
            "is_active": canonical_cito.get("is_active"),
            "current_division": canonical_cito.get("division"),
            "champion_status": raw.get("championStatus"),
            "place_of_birth": canonical_cito.get("place_of_birth"),
            "trains_at": raw.get("trainsAt"),
            "fighting_style": raw.get("fightingStyle"),
            "cito_profile_url": canonical_cito.get("profile_url"),
            "photo_url": canonical_cito.get("photo_url") or raw.get("proxiedImageUrl"),
            "body_image_url": raw.get("bodyImageUrl"),
            "cito_record_wins": raw.get("recordWins"),
            "cito_record_losses": raw.get("recordLosses"),
            "cito_record_draws": raw.get("recordDraws"),
            "cito_record_nc": raw.get("recordNoContest"),
            "source_updated_at": datetime.now(timezone.utc).isoformat(),
            "cito_synced_at": datetime.now(timezone.utc).isoformat(),
            "fightiq_updated_at": datetime.now(timezone.utc).isoformat(),
        }
        record = {k: v for k, v in record.items() if v is not None}

        sb.table("fighters").insert(record).execute()
        by_name.setdefault(norm(intended_name), []).append({
            "fightiq_id": fightiq_id,
            "display_name": intended_name,
            "ufcstats_id": None,
            "cito_id": item["canonical_cito_id"],
        })

        for i, (cid, cname) in enumerate(zip(item["cito_ids"], item["cito_names"])):
            source_upsert(
                sb, fightiq_id, "cito", cid, cname,
                is_primary=(cid == item["canonical_cito_id"])
            )
            mark_cito(
                sb, cid, "created_new_fighter",
                item["resolution_reason"],
                fightiq_id=fightiq_id,
                ufcstats_id=None,
            )

        created += 1

    # 3) Explicit exclusions: never create these as MMA fighters.
    for item in plan["exclusions"]:
        mark_cito(
            sb,
            item["cito_id"],
            "excluded",
            item["resolution_reason"],
            fightiq_id=None,
            ufcstats_id=None,
        )
        excluded += 1

    after = (
        sb.table("fighters")
        .select("id", count="exact")
        .limit(1)
        .execute()
        .count
    )

    unresolved = (
        sb.table("cito_unmatched_fighters")
        .select("cito_id", count="exact")
        .is_("resolution_status", "null")
        .limit(1)
        .execute()
        .count
    )

    expected_max_growth = plan["summary"]["create_new_fighters"]
    actual_growth = after - before

    print("===== V5.6 RESULT =====")
    print(f"fighters before: {before}")
    print(f"fighters after: {after}")
    print(f"new fighter rows: {actual_growth}")
    print(f"verified existing links processed: {linked}")
    print(f"creates performed: {created}")
    print(f"exact-name duplicates avoided: {duplicate_avoided}")
    print(f"excluded Cito profiles: {excluded}")
    print(f"unresolved: {unresolved}")

    if actual_growth < 0 or actual_growth > expected_max_growth:
        raise RuntimeError(
            f"Safety failure: unexpected fighters growth {actual_growth}; "
            f"expected 0..{expected_max_growth}"
        )
    if unresolved != 0:
        raise RuntimeError(f"Safety failure: {unresolved} profiles unresolved")

    print("V5.6 CONSOLIDATION COMPLETE")


if __name__ == "__main__":
    main()
