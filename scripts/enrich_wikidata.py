import json
import os
import time
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from supabase import create_client


WIKIDATA_API = "https://www.wikidata.org/w/api.php"

USER_AGENT = (
    "FightIQ/1.0 "
    "(MMA fighter database enrichment)"
)

BATCH_LIMIT = 100

# Une recherche par combattant, espacée volontairement
SEARCH_DELAY = 1.0

# Nombre d'essais si Wikidata renvoie 429
MAX_RETRIES = 5


def api_get(params):
    params["format"] = "json"
    params["maxlag"] = "5"

    url = (
        WIKIDATA_API
        + "?"
        + urlencode(params)
    )

    for attempt in range(MAX_RETRIES):
        request = Request(
            url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/json",
            },
        )

        try:
            with urlopen(
                request,
                timeout=30
            ) as response:
                return json.loads(
                    response
                    .read()
                    .decode("utf-8")
                )

        except HTTPError as exc:
            if exc.code != 429:
                raise

            wait = 2 ** attempt

            print(
                f"Wikidata 429 - "
                f"retry in {wait}s"
            )

            time.sleep(wait)

    raise RuntimeError(
        "Wikidata rate limit "
        "after retries"
    )


def search_fighter(name):
    payload = api_get({
        "action": "wbsearchentities",
        "search": name,
        "language": "en",
        "uselang": "en",
        "limit": 5,
        "type": "item",
    })

    results = payload.get(
        "search",
        []
    )

    mma_keywords = (
        "mixed martial",
        "mma",
        "ufc",
        "martial artist",
        "mixed martial artist",
    )

    for result in results:
        description = (
            result.get(
                "description"
            )
            or ""
        ).lower()

        if any(
            word in description
            for word in mma_keywords
        ):
            return result.get("id")

    # Sécurité :
    # on ne prend PAS le premier
    # homonyme au hasard.
    return None


def fetch_entities(qids):
    if not qids:
        return {}

    payload = api_get({
        "action": "wbgetentities",
        "ids": "|".join(qids),
        "props": "claims|labels",
        "languages": "fr|en",
        "languagefallback": "1",
    })

    return payload.get(
        "entities",
        {}
    )


def first_entity_claim(
    entity,
    property_id
):
    claims = (
        entity
        .get("claims", {})
        .get(property_id, [])
    )

    for claim in claims:
        try:
            value = (
                claim["mainsnak"]
                ["datavalue"]
                ["value"]
            )

            if (
                isinstance(value, dict)
                and value.get("id")
            ):
                return value["id"]

        except (
            KeyError,
            TypeError
        ):
            continue

    return None


def first_string_claim(
    entity,
    property_id
):
    claims = (
        entity
        .get("claims", {})
        .get(property_id, [])
    )

    for claim in claims:
        try:
            value = (
                claim["mainsnak"]
                ["datavalue"]
                ["value"]
            )

            if isinstance(
                value,
                str
            ):
                return value

        except (
            KeyError,
            TypeError
        ):
            continue

    return None


def get_label(entity):
    if not entity:
        return None

    labels = entity.get(
        "labels",
        {}
    )

    if "fr" in labels:
        return labels[
            "fr"
        ].get("value")

    if "en" in labels:
        return labels[
            "en"
        ].get("value")

    return None


def normalize_gender(label):
    if not label:
        return None

    value = label.lower()

    if value in (
        "male",
        "masculin",
        "homme",
    ):
        return "male"

    if value in (
        "female",
        "féminin",
        "femme",
    ):
        return "female"

    return label


def iso_country_code(
    country_entity
):
    if not country_entity:
        return None

    return first_string_claim(
        country_entity,
        "P297"
    )


def fetch_fighters(
    supabase
):
    response = (
        supabase
        .table("fighters")
        .select(
            "id,"
            "display_name,"
            "wikidata_id,"
            "nationality,"
            "country_code,"
            "gender,"
            "birth_place,"
            "website"
        )
        .is_(
            "wikidata_id",
            "null"
        )
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

    if (
        not supabase_url
        or not supabase_key
    ):
        raise RuntimeError(
            "Missing Supabase secrets"
        )

    supabase = create_client(
        supabase_url,
        supabase_key
    )

    fighters = fetch_fighters(
        supabase
    )

    print(
        "FightIQ Wikidata batch: "
        f"{len(fighters)} fighters"
    )

    matches = {}
    not_found = []

    # ------------------------------------------------
    # 1. Recherche des QID
    # ------------------------------------------------

    for index, fighter in enumerate(
        fighters,
        start=1
    ):
        name = fighter.get(
            "display_name"
        )

        if not name:
            continue

        try:
            qid = search_fighter(
                name
            )

            if qid:
                matches[
                    fighter["id"]
                ] = {
                    "fighter": fighter,
                    "qid": qid,
                }

                print(
                    f"[{index}] FOUND: "
                    f"{name} -> {qid}"
                )

            else:
                not_found.append(name)

                print(
                    f"[{index}] NOT FOUND: "
                    f"{name}"
                )

        except Exception as exc:
            print(
                f"[{index}] ERROR: "
                f"{name}: {exc}"
            )

        time.sleep(
            SEARCH_DELAY
        )

    # ------------------------------------------------
    # 2. Récupération groupée des fiches fighters
    # ------------------------------------------------

    fighter_qids = [
        item["qid"]
        for item in matches.values()
    ]

    fighter_entities = {}

    for i in range(
        0,
        len(fighter_qids),
        50
    ):
        group = fighter_qids[
            i:i + 50
        ]

        fighter_entities.update(
            fetch_entities(group)
        )

        time.sleep(1)

    # ------------------------------------------------
    # 3. Collecte des QID liés
    # ------------------------------------------------

    linked_qids = set()

    for item in matches.values():
        entity = (
            fighter_entities
            .get(item["qid"], {})
        )

        for property_id in (
            "P27",  # citizenship
            "P19",  # birthplace
            "P21",  # gender
        ):
            linked = first_entity_claim(
                entity,
                property_id
            )

            if linked:
                linked_qids.add(linked)

    # ------------------------------------------------
    # 4. Récupération groupée pays / lieux / sexe
    # ------------------------------------------------

    linked_entities = {}

    linked_qids = list(
        linked_qids
    )

    for i in range(
        0,
        len(linked_qids),
        50
    ):
        group = linked_qids[
            i:i + 50
        ]

        linked_entities.update(
            fetch_entities(group)
        )

        time.sleep(1)

    # ------------------------------------------------
    # 5. Mise à jour Supabase
    # ------------------------------------------------

    now = datetime.now(
        timezone.utc
    ).isoformat()

    updated = 0

    for item in matches.values():
        fighter = item["fighter"]
        qid = item["qid"]

        entity = (
            fighter_entities
            .get(qid)
        )

        if not entity:
            continue

        nationality_qid = (
            first_entity_claim(
                entity,
                "P27"
            )
        )

        birth_place_qid = (
            first_entity_claim(
                entity,
                "P19"
            )
        )

        gender_qid = (
            first_entity_claim(
                entity,
                "P21"
            )
        )

        nationality_entity = (
            linked_entities.get(
                nationality_qid
            )
        )

        birth_place_entity = (
            linked_entities.get(
                birth_place_qid
            )
        )

        gender_entity = (
            linked_entities.get(
                gender_qid
            )
        )

        nationality = get_label(
            nationality_entity
        )

        country_code = (
            iso_country_code(
                nationality_entity
            )
        )

        birth_place = get_label(
            birth_place_entity
        )

        gender = normalize_gender(
            get_label(
                gender_entity
            )
        )

        website = (
            first_string_claim(
                entity,
                "P856"
            )
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

        updated += 1

    print()
    print(
        "FightIQ Wikidata enrichment "
        "complete"
    )
    print(
        f"Found: {len(matches)}"
    )
    print(
        f"Updated: {updated}"
    )
    print(
        f"Not found: "
        f"{len(not_found)}"
    )

    if not_found:
        print(
            "Not found sample:",
            not_found[:20]
        )


if __name__ == "__main__":
    main()
