import json
import os
import re
import time
import unicodedata
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import urlencode, quote, urlparse
from urllib.request import Request, urlopen

from supabase import create_client

PAGE_SIZE = 1000
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

# Verified Wikidata MMA identifier properties
PROP_DOB = "P569"
PROP_HEIGHT = "P2048"
PROP_BIRTH_PLACE = "P19"
PROP_IMAGE = "P18"
PROP_UFC_ID = "P9722"
PROP_FIGHT_MATRIX_ID = "P9724"
PROP_BELLATOR_ID = "P9726"
PROP_TAPOLOGY_ID = "P9728"
PROP_SHERDOG_ID = "P2818"

FIGHTER_FIELDS = (
    "fightiq_id,display_name,first_name,last_name,nickname,"
    "date_of_birth,height_cm,place_of_birth,photo_url,"
    "photo_source,photo_original_source,photo_rights_status,"
    "ufcstats_id,cito_id,cito_profile_url,slug,cito_slug,is_active"
)

FILL_FIELDS = ["date_of_birth", "height_cm", "place_of_birth", "photo_url"]


def present(v):
    return v is not None and (not isinstance(v, str) or bool(v.strip()))


def normalize(value):
    value = unicodedata.normalize("NFKD", value or "")
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def normalize_id(value):
    if not present(value):
        return None
    return normalize(str(value)).replace(" ", "-")


def api_get(params, timeout=30):
    params = dict(params)
    params["format"] = "json"
    url = WIKIDATA_API + "?" + urlencode(params)
    req = Request(
        url,
        headers={
            "User-Agent": (
                "FightIQ-Wikidata-Identity/2.0 "
                "(conservative identity resolution; github.com/kilianl72/FightIQ)"
            ),
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def fetch_all(sb, table, fields):
    rows, start = [], 0
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


def search_candidates(name):
    payload = api_get(
        {
            "action": "wbsearchentities",
            "search": name,
            "language": "en",
            "uselang": "en",
            "type": "item",
            "limit": 10,
        }
    )
    return payload.get("search") or []


def get_entities(ids):
    result = {}
    ids = list(dict.fromkeys(x for x in ids if x))
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        payload = api_get(
            {
                "action": "wbgetentities",
                "ids": "|".join(chunk),
                "props": "labels|aliases|descriptions|claims",
                "languages": "en|fr",
                "languagefallback": 1,
            },
            timeout=45,
        )
        result.update(payload.get("entities") or {})
    return result


def claims(entity, prop):
    values = []
    raw = (entity.get("claims") or {}).get(prop) or []
    usable = [c for c in raw if c.get("rank") != "deprecated"]
    usable.sort(key=lambda c: 0 if c.get("rank") == "preferred" else 1)
    for claim in usable:
        value = (((claim.get("mainsnak") or {}).get("datavalue") or {}).get("value"))
        if value is not None:
            values.append(value)
    return values


def first_claim(entity, prop):
    vals = claims(entity, prop)
    return vals[0] if vals else None


def exact_date(entity):
    value = first_claim(entity, PROP_DOB)
    if not isinstance(value, dict):
        return None
    if (value.get("precision") or 0) < 11:
        return None
    t = value.get("time") or ""
    m = re.match(r"^\+?(\d{4})-(\d{2})-(\d{2})T", t)
    return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else None


def quantity_to_cm(value):
    if not isinstance(value, dict):
        return None
    try:
        amount = float(str(value.get("amount", "")).replace("+", ""))
    except Exception:
        return None

    unit = value.get("unit") or ""
    if unit.endswith("/Q11573"):       # metre
        cm = amount * 100
    elif unit.endswith("/Q174728"):    # centimetre
        cm = amount
    elif unit.endswith("/Q218593"):    # inch
        cm = amount * 2.54
    elif unit.endswith("/Q3710"):      # foot
        cm = amount * 30.48
    else:
        return None

    return round(cm, 2) if 120 <= cm <= 230 else None


def height_cm(entity):
    return quantity_to_cm(first_claim(entity, PROP_HEIGHT))


def item_id(entity, prop):
    value = first_claim(entity, prop)
    return value.get("id") if isinstance(value, dict) else None


def external_id(entity, prop):
    value = first_claim(entity, prop)
    return str(value).strip() if present(value) else None


def image_url(entity):
    value = first_claim(entity, PROP_IMAGE)
    if not isinstance(value, str) or not value.strip():
        return None
    return (
        "https://commons.wikimedia.org/wiki/Special:Redirect/file/"
        + quote(value.replace(" ", "_"), safe="")
    )


def entity_names(entity):
    result = set()
    for lang in ("en", "fr"):
        label = (entity.get("labels") or {}).get(lang)
        if isinstance(label, dict) and present(label.get("value")):
            result.add(normalize(label["value"]))
        for alias in (entity.get("aliases") or {}).get(lang) or []:
            if present(alias.get("value")):
                result.add(normalize(alias["value"]))
    return result


def entity_description(entity):
    values = []
    for lang in ("en", "fr"):
        d = (entity.get("descriptions") or {}).get(lang)
        if isinstance(d, dict) and present(d.get("value")):
            values.append(d["value"])
    return normalize(" ".join(values))


def ufc_slug_from_fighter(fighter):
    url = fighter.get("cito_profile_url")
    if present(url):
        path = urlparse(url).path.strip("/")
        parts = path.split("/")
        if len(parts) >= 2 and parts[-2].lower() == "athlete":
            return normalize_id(parts[-1])

    # Cito slug usually mirrors the UFC athlete slug, but it is weaker evidence
    for field in ("cito_slug", "slug"):
        if present(fighter.get(field)):
            return normalize_id(fighter.get(field))
    return None


def fighter_names(fighter):
    names = {
        normalize(fighter.get("display_name")),
        normalize(
            " ".join(
                p for p in [fighter.get("first_name"), fighter.get("last_name")] if p
            )
        ),
    }
    if present(fighter.get("nickname")):
        # Nickname alone is not enough, but useful for corroboration.
        names.add(normalize(fighter.get("nickname")))
    names.discard("")
    return names


def score_candidate(fighter, entity):
    names = entity_names(entity)
    fighter_name_set = fighter_names(fighter)

    full_name_matches = {
        normalize(fighter.get("display_name")),
        normalize(
            " ".join(
                p for p in [fighter.get("first_name"), fighter.get("last_name")] if p
            )
        ),
    }
    full_name_matches.discard("")

    if not (full_name_matches & names):
        return None, ["no_exact_name_or_alias_match"]

    score = 20
    reasons = ["exact_name_or_alias"]

    description = entity_description(entity)
    mma_terms = (
        "mixed martial",
        "mma fighter",
        "ultimate fighting",
        "ufc fighter",
        "martial artist",
        "arts martiaux mixtes",
    )
    if any(term in description for term in mma_terms):
        score += 20
        reasons.append("mma_description")

    # STRONGEST POSSIBLE CHECK: official UFC athlete slug/ID
    wd_ufc = normalize_id(external_id(entity, PROP_UFC_ID))
    fiq_ufc = ufc_slug_from_fighter(fighter)
    if wd_ufc and fiq_ufc:
        if wd_ufc == fiq_ufc:
            score += 100
            reasons.append("ufc_athlete_id_exact")
        else:
            return None, ["ufc_athlete_id_conflict"]

    # Exact DOB: decisive corroboration; a conflict is an immediate rejection.
    fiq_dob = str(fighter.get("date_of_birth"))[:10] if present(fighter.get("date_of_birth")) else None
    wd_dob = exact_date(entity)
    if fiq_dob and wd_dob:
        if fiq_dob == wd_dob:
            score += 80
            reasons.append("dob_exact")
        else:
            return None, ["dob_conflict"]

    # Height is supporting evidence, not decisive by itself.
    fiq_height = fighter.get("height_cm")
    wd_height = height_cm(entity)
    if present(fiq_height) and present(wd_height):
        try:
            delta = abs(float(fiq_height) - float(wd_height))
            if delta > 10:
                return None, ["height_conflict_gt_10cm"]
            if delta <= 2:
                score += 20
                reasons.append("height_within_2cm")
            elif delta <= 5:
                score += 10
                reasons.append("height_within_5cm")
        except Exception:
            pass

    # Nickname/alias corroboration if available.
    nickname = normalize(fighter.get("nickname"))
    if nickname and nickname in names:
        score += 15
        reasons.append("nickname_alias")

    # Presence of MMA authority-control identifiers adds confidence that this is
    # genuinely a fighter entity rather than an unrelated same-name person.
    authority_props = [PROP_SHERDOG_ID, PROP_TAPOLOGY_ID, PROP_FIGHT_MATRIX_ID, PROP_UFC_ID]
    authority_count = sum(1 for p in authority_props if external_id(entity, p))
    if authority_count:
        score += min(25, authority_count * 7)
        reasons.append(f"mma_external_ids={authority_count}")

    return score, reasons


def choose_candidate(fighter, candidate_rows, entity_cache):
    scored = []

    for row in candidate_rows:
        qid = row.get("id")
        entity = entity_cache.get(qid) or {}
        score, reasons = score_candidate(fighter, entity)
        if score is not None:
            scored.append((score, qid, entity, reasons))

    if not scored:
        return None, "no_confident_candidate"

    scored.sort(key=lambda x: x[0], reverse=True)
    best = scored[0]

    # HIGH CONFIDENCE RULES:
    # 1. UFC athlete ID exact match => accepted.
    # 2. Exact DOB + exact name + MMA context/authority IDs => accepted.
    # 3. Otherwise require a very strong score and clear separation.
    reasons = set(best[3])
    has_decisive = "ufc_athlete_id_exact" in reasons or "dob_exact" in reasons

    if has_decisive:
        minimum = 100
    else:
        minimum = 75

    if best[0] < minimum:
        return None, f"score_too_low_{best[0]}"

    if len(scored) > 1:
        second = scored[1]
        # Require a healthy margin unless the winner has an exact UFC ID.
        margin_required = 10 if "ufc_athlete_id_exact" in reasons else 25
        if best[0] - second[0] < margin_required:
            return None, f"ambiguous_margin_{best[0]}_{second[0]}"

    return best, None


def place_label(entity_cache, qid):
    if not qid:
        return None
    entity = entity_cache.get(qid) or {}
    for lang in ("en", "fr"):
        label = (entity.get("labels") or {}).get(lang)
        if isinstance(label, dict) and present(label.get("value")):
            return label["value"]
    return None


def save_source_id(sb, fighter, source, source_id):
    if not present(source_id):
        return
    sb.table("fighter_source_ids").upsert(
        {
            "fightiq_id": fighter["fightiq_id"],
            "source": source,
            "source_id": str(source_id),
            "source_name": fighter.get("display_name"),
            "is_primary": False,
        },
        on_conflict="source,source_id",
    ).execute()


def main():
    sb = create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SECRET_KEY"],
    )

    fighters = fetch_all(sb, "fighters", FIGHTER_FIELDS)
    source_rows = fetch_all(
        sb,
        "fighter_source_ids",
        "fightiq_id,source,source_id",
    )

    source_by_fiq = {}
    for row in source_rows:
        source_by_fiq.setdefault(row["fightiq_id"], {})[row["source"]] = row["source_id"]

    # Every fighter is eligible for identity-linking, but active fighters and
    # fighters with missing target fields go first.
    fighters.sort(
        key=lambda f: (
            0 if f.get("is_active") is True else 1,
            0 if any(not present(f.get(x)) for x in FILL_FIELDS) else 1,
            normalize(f.get("display_name")),
        )
    )

    before = Counter(
        {
            field: sum(1 for f in fighters if not present(f.get(field)))
            for field in FILL_FIELDS
        }
    )

    search_cache = {}
    entity_cache = {}

    matched = 0
    already_linked = 0
    skipped = 0
    updated = 0
    fills = Counter()
    saved_ids = Counter()

    print("===== WIKIDATA ULTRA-CONSERVATIVE IDENTITY + COMPLETION =====")
    print(f"fighters_total: {len(fighters)}")

    for idx, fighter in enumerate(fighters, start=1):
        fiq = fighter["fightiq_id"]
        known = source_by_fiq.get(fiq, {})
        existing_qid = known.get("wikidata")

        if existing_qid:
            if existing_qid not in entity_cache:
                try:
                    entity_cache.update(get_entities([existing_qid]))
                except Exception as exc:
                    print(f"ENTITY_ERROR existing {existing_qid}: {exc}")
                    skipped += 1
                    continue

            entity = entity_cache.get(existing_qid) or {}
            score, reasons = score_candidate(fighter, entity)
            if score is None:
                print(
                    f"WARNING_EXISTING_WIKIDATA_CONFLICT "
                    f"{fighter.get('display_name')} {existing_qid} {reasons}"
                )
                skipped += 1
                continue

            chosen = (score, existing_qid, entity, reasons)
            already_linked += 1

        else:
            name = fighter.get("display_name") or ""
            if not present(name):
                skipped += 1
                continue

            if name not in search_cache:
                try:
                    search_cache[name] = search_candidates(name)
                    time.sleep(0.07)
                except Exception as exc:
                    print(f"SEARCH_ERROR {name}: {exc}")
                    search_cache[name] = []

            candidate_ids = [
                r.get("id") for r in search_cache[name] if r.get("id")
            ]
            to_fetch = [qid for qid in candidate_ids if qid not in entity_cache]

            if to_fetch:
                try:
                    entity_cache.update(get_entities(to_fetch))
                except Exception as exc:
                    print(f"ENTITY_ERROR {name}: {exc}")

            chosen, reject_reason = choose_candidate(
                fighter, search_cache[name], entity_cache
            )

            if not chosen:
                skipped += 1
                if reject_reason and (
                    fighter.get("is_active") is True
                    or idx % 500 == 0
                ):
                    print(
                        f"SKIP {fighter.get('display_name')} | "
                        f"reason={reject_reason}"
                    )
                continue

        score, qid, entity, reasons = chosen
        matched += 1

        birth_place_qid = item_id(entity, PROP_BIRTH_PLACE)
        if birth_place_qid and birth_place_qid not in entity_cache:
            try:
                entity_cache.update(get_entities([birth_place_qid]))
            except Exception:
                pass

        candidate = {
            "date_of_birth": exact_date(entity),
            "height_cm": height_cm(entity),
            "place_of_birth": place_label(entity_cache, birth_place_qid),
            "photo_url": image_url(entity),
        }

        changes = {}
        for field, value in candidate.items():
            if not present(fighter.get(field)) and present(value):
                changes[field] = value
                fighter[field] = value
                fills[field] += 1

        if "photo_url" in changes:
            changes["photo_source"] = "wikidata"
            changes["photo_original_source"] = "wikimedia_commons"
            changes["photo_rights_status"] = "pending_confirmation"

        if changes:
            changes["fightiq_updated_at"] = datetime.now(timezone.utc).isoformat()
            sb.table("fighters").update(changes).eq("fightiq_id", fiq).execute()
            updated += 1

        # Save Wikidata QID and every MMA external ID exposed by Wikidata.
        identifiers = {
            "wikidata": qid,
            "ufc": external_id(entity, PROP_UFC_ID),
            "sherdog": external_id(entity, PROP_SHERDOG_ID),
            "tapology": external_id(entity, PROP_TAPOLOGY_ID),
            "fightmatrix": external_id(entity, PROP_FIGHT_MATRIX_ID),
            "bellator": external_id(entity, PROP_BELLATOR_ID),
        }

        for source, source_id in identifiers.items():
            if not present(source_id):
                continue
            try:
                save_source_id(sb, fighter, source, source_id)
                saved_ids[source] += 1
            except Exception as exc:
                print(
                    f"SOURCE_ID_WARNING {fighter.get('display_name')} "
                    f"{source}={source_id}: {exc}"
                )

        if idx % 250 == 0:
            print(
                f"Processed {idx}/{len(fighters)} | matched={matched} "
                f"updated={updated} skipped={skipped}"
            )

    print("\n===== RESULTS =====")
    print(f"confident_wikidata_matches: {matched}")
    print(f"already_linked_verified: {already_linked}")
    print(f"skipped_or_ambiguous: {skipped}")
    print(f"fighters_updated: {updated}")

    print("\n===== FILLED PROFILE CELLS =====")
    for field in FILL_FIELDS:
        print(f"{field}: +{fills[field]}")

    print("\n===== EXTERNAL IDS SAVED =====")
    for source in ("wikidata", "ufc", "sherdog", "tapology", "fightmatrix", "bellator"):
        print(f"{source}: {saved_ids[source]}")

    print("\n===== BEFORE / AFTER MISSING =====")
    for field in FILL_FIELDS:
        after = sum(1 for f in fighters if not present(f.get(field)))
        print(f"{field}: {before[field]} -> {after}")

    print("\nWIKIDATA IDENTITY + COMPLETION COMPLETE")


if __name__ == "__main__":
    main()
