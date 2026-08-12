import json
import os
import time
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from supabase import create_client

USER_AGENT = "FightIQ/1.0 (contact: FightIQ project)"
WIKIDATA_API = "https://www.wikidata.org/w/api.php"

BATCH_LIMIT = 100
SLEEP_BETWEEN_CALLS = 0.25


def api_get(params):
    url = f"{WIKIDATA_API}?{urlencode(params)}"

    request = Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=30) as response:
        return json.loads(
            response.read().decode("utf-8")
        )


def search_wikidata_entity(name):
    payload = api_get({
        "action": "wbsearchentities",
        "search": name,
        "language": "en",
        "format": "json",
        "limit": 5,
        "type": "item",
    })

    results = payload.get("search", [])

    if not results:
        return None

    # On privilégie les résultats liés au MMA/UFC/combat
    keywords = [
        "mixed martial",
        "mma",
        "ufc",
        "fighter",
        "martial artist",
    ]

    for result in results:
        description = (
            result.get("description") or ""
        ).lower()

        if any(
            keyword in description
            for keyword in keywords
        ):
            return result.get("id")

    # Sinon on prend le premier résultat
    return results[0].get("id")


def get_entity(qid):
    payload = api_get({
        "action": "wbgetentities",
        "ids": qid,
        "format": "json",
        "props": "claims|sitelinks|info",
    })

    return (
        payload
        .get("entities", {})
        .get(qid)
    )


def get_claim_entity_id(entity, property_id):
    claims = entity.get("claims", {})
    values = claims.get(property_id, [])

    if not values:
        return None

    try:
        return (
            values[0]
            ["mainsnak"]
            ["datavalue"]
            ["value"]
            ["id"]
        )
    except (
        KeyError,
        TypeError,
        IndexError,
    ):
        return None


def get_claim_string(entity, property_id):
    claims = entity.get("claims", {})
    values = claims.get(property_id, [])

    if not values:
        return None

    try:
        value = (
            values[0]
            ["mainsnak"]
            ["datavalue"]
            ["value"]
        )

        if isinstance(value, str):
            return value

    except (
        KeyError,
        TypeError,
        IndexError,
    ):
        pass

    return None


def get_entity_label(qid):
    if not qid:
        return None

    payload = api_get({
        "action": "wbgetentities",
        "ids": qid,
        "format": "json",
        "props": "labels",
        "languages": "en|fr",
    })

    entity = (
        payload
        .get("entities", {})
        .get(qid, {})
    )

    labels = entity.get("labels", {})

    if "fr" in labels:
        return labels["fr"]["value"]

    if "en" in labels:
        return labels["en"]["value"]

    return None


def country_code_from_qid(qid):
    if not qid:
        return None

    payload = api_get({
        "action": "wbgetentities",
        "ids": qid,
        "format": "json",
        "props": "claims",
    })

    entity = (
        payload
        .get("entities", {})
        .get(qid, {})
    )

    claims = entity.get("claims", {})
    iso_claims = claims.get("P297", [])

    if not iso_claims:
        return None

    try:
        return (
            iso_claims[0]
            ["mainsnak"]
            ["datavalue"]
            ["value"]
        )
    except (
        KeyError,
        TypeError,
        IndexError,
    ):
        return None


def gender_from_qid(qid):
    if not qid:
        return None

    label = get_entity_label(qid)

    if not label:
        return None

    value = label.lower()

    if value in {
        "male",
        "masculin",
        "homme",
    }:
        return "male"

    if value in {
        "female",
        "féminin",
        "femme",
    }:
        return "female"

    return label


def fetch_fighters_to_enrich(supabase):
    response = (
        supabase
        .table("fighters")
        .select(
            "id,display_name,wikidata_id,"
            "nationality,country_code,"
            "gender,birth_place,website"
        )
        .is_("wikidata_id", "null")
        .limit(BATCH_LIMIT)
        .execute()
    )

    return response.data or []


def main():
    supabase_url = os.environ.get(
        "SUPABASE_URL"
    )

    supabase_key = os.environ.get(
        "SUPABASE_SECRET_KEY"
    )

    if not supabase_url or not supabase_key:
        raise RuntimeError(
            "Missing Supabase secrets"
        )

    supabase = create_client(
        supabase_url,
        supabase_key
    )

    fighters = fetch_fighters_to_enrich(
        supabase
    )

    print(
        f"FightIQ Wikidata batch: "
        f"{len(fighters)} fighters"
    )

    processed = 0
    matched = 0
    not_found = []

    now = datetime.now(
        timezone.utc
    ).isoformat()

    for fighter in fighters:
        processed += 1

        name = fighter.get(
            "display_name"
        )

        if not name:
            continue

        try:
            qid = search_wikidata_entity(
                name
            )

            if not qid:
                not_found.append(name)
                print(
                    f"[{processed}] NOT FOUND: "
                    f"{name}"
                )
                time.sleep(
                    SLEEP_BETWEEN_CALLS
                )
                continue

            entity = get_entity(qid)

            if not entity:
                not_found.append(name)
                continue

            nationality_qid = (
                get_claim_entity_id(
                    entity,
                    "P27"
                )
            )

            birth_place_qid = (
                get_claim_entity_id(
                    entity,
                    "P19"
                )
            )

            gender_qid = (
                get_claim_entity_id(
                    entity,
                    "P21"
                )
            )

            nationality = (
                get_entity_label(
                    nationality_qid
                )
                if nationality_qid
                else None
            )

            country_code = (
                country_code_from_qid(
                    nationality_qid
                )
                if nationality_qid
                else None
            )

            birth_place = (
                get_entity_label(
                    birth_place_qid
                )
                if birth_place_qid
                else None
            )

            gender = (
                gender_from_qid(
                    gender_qid
                )
                if gender_qid
                else None
            )

            website = get_claim_string(
                entity,
                "P856"
            )

            update = {
                "wikidata_id": qid,
                "wikidata_updated_at": now,
                "fightiq_updated_at": now,
            }

            if (
                nationality
                and not fighter.get(
                    "nationality"
                )
            ):
                update[
                    "nationality"
                ] = nationality

            if (
                country_code
                and not fighter.get(
                    "country_code"
                )
            ):
                update[
                    "country_code"
                ] = country_code

            if (
                birth_place
                and not fighter.get(
                    "birth_place"
                )
            ):
                update[
                    "birth_place"
                ] = birth_place

            if (
                gender
                and not fighter.get(
                    "gender"
                )
            ):
                update[
                    "gender"
                ] = gender

            if (
                website
                and not fighter.get(
                    "website"
                )
            ):
                update[
                    "website"
                ] = website

            (
                supabase
                .table("fighters")
                .update(update)
                .eq(
                    "id",
                    fighter["id"]
                )
                .execute()
            )

            matched += 1

            print(
                f"[{processed}] MATCH: "
                f"{name} -> {qid}"
            )

        except Exception as exc:
            print(
                f"[{processed}] ERROR: "
                f"{name}: {exc}"
            )

        time.sleep(
            SLEEP_BETWEEN_CALLS
        )

    print()
    print(
        "FightIQ Wikidata enrichment "
        "complete"
    )
    print(
        f"Processed: {processed}"
    )
    print(
        f"Matched: {matched}"
    )
    print(
        f"Not found: {len(not_found)}"
    )

    if not_found:
        print(
            "Not found sample:",
            not_found[:20]
        )


if __name__ == "__main__":
    main()
