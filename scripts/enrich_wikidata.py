import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from supabase import create_client


# ============================================================
# FightIQ - Wikidata enrichment
# Version : V2
#
# Objectifs V2 :
# - pagination Supabase
# - aucun match automatique sur le nom seul
# - validation par date de naissance
# - détection des QID déjà utilisés
# - conservation des conflits / cas douteux
# - aucun conflit ne fait planter le workflow
# ============================================================


SPARQL_URL = "https://query.wikidata.org/sparql"

USER_AGENT = (
    "FightIQ/2.0 "
    "(MMA fighter database enrichment)"
)

# Nombre maximal de combattants examinés par run
BATCH_LIMIT = 5000

# Pagination Supabase
SUPABASE_PAGE_SIZE = 1000

# Nombre de noms par requête Wikidata
SPARQL_BATCH_SIZE = 20

# Pause entre deux requêtes Wikidata
REQUEST_DELAY = 5

MAX_RETRIES = 5


def escape_sparql_string(value):
    return (
        value
        .replace("\\", "\\\\")
        .replace('"', '\\"')
        .replace("\n", " ")
        .replace("\r", " ")
    )


def sparql_request(query):
    data = urlencode({
        "query": query,
        "format": "json",
    }).encode("utf-8")

    for attempt in range(MAX_RETRIES):
        request = Request(
            SPARQL_URL,
            data=data,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": (
                    "application/"
                    "sparql-results+json"
                ),
                "Content-Type": (
                    "application/"
                    "x-www-form-urlencoded"
                ),
            },
            method="POST",
        )

        try:
            with urlopen(
                request,
                timeout=60
            ) as response:
                return json.loads(
                    response
                    .read()
                    .decode("utf-8")
                )

        except HTTPError as exc:
            if exc.code not in {
                429,
                500,
                502,
                503,
                504,
            }:
                print(
                    "Wikidata HTTP error: "
                    f"{exc.code}"
                )
                return None

            wait = min(
                60,
                5 * (2 ** attempt)
            )

            print(
                "Wikidata temporarily "
                f"unavailable ({exc.code}). "
                f"Retry in {wait}s."
            )

            time.sleep(wait)

        except (
            URLError,
            TimeoutError,
        ) as exc:
            wait = min(
                60,
                5 * (2 ** attempt)
            )

            print(
                "Wikidata network error: "
                f"{exc}. "
                f"Retry in {wait}s."
            )

            time.sleep(wait)

    print(
        "Wikidata batch deferred "
        "after retries."
    )

    return None


def build_query(names):
    values = "\n".join(
        f'"{escape_sparql_string(name)}"@en'
        for name in names
    )

    return f"""
PREFIX wd:
    <http://www.wikidata.org/entity/>

PREFIX wdt:
    <http://www.wikidata.org/prop/direct/>

PREFIX rdfs:
    <http://www.w3.org/2000/01/rdf-schema#>

PREFIX skos:
    <http://www.w3.org/2004/02/skos/core#>


SELECT DISTINCT
    ?wantedLabel
    ?item
    ?dob
    ?country
    ?countryLabel
    ?countryCode
    ?birthPlace
    ?birthPlaceLabel
    ?gender
    ?genderLabel
    ?website

WHERE {{

    VALUES ?wantedLabel {{
        {values}
    }}

    {{
        ?item rdfs:label ?wantedLabel .
    }}
    UNION
    {{
        ?item skos:altLabel ?wantedLabel .
    }}

    ?item
        wdt:P106/wdt:P279*
        wd:Q11607585 .

    OPTIONAL {{
        ?item wdt:P569 ?dob .
    }}

    OPTIONAL {{
        ?item wdt:P27 ?country .

        OPTIONAL {{
            ?country
                rdfs:label
                ?countryLabel .

            FILTER(
                LANG(?countryLabel)
                = "en"
            )
        }}

        OPTIONAL {{
            ?country
                wdt:P297
                ?countryCode .
        }}
    }}

    OPTIONAL {{
        ?item
            wdt:P19
            ?birthPlace .

        OPTIONAL {{
            ?birthPlace
                rdfs:label
                ?birthPlaceLabel .

            FILTER(
                LANG(?birthPlaceLabel)
                = "en"
            )
        }}
    }}

    OPTIONAL {{
        ?item
            wdt:P21
            ?gender .

        OPTIONAL {{
            ?gender
                rdfs:label
                ?genderLabel .

            FILTER(
                LANG(?genderLabel)
                = "en"
            )
        }}
    }}

    OPTIONAL {{
        ?item
            wdt:P856
            ?website .
    }}
}}
"""


def binding_value(binding, key):
    value = binding.get(key)

    if not value:
        return None

    return value.get("value")


def qid_from_uri(uri):
    if not uri:
        return None

    return uri.rstrip(
        "/"
    ).split("/")[-1]


def normalize_gender(value):
    if not value:
        return None

    normalized = (
        value
        .lower()
        .strip()
    )

    if normalized in {
        "male",
        "man",
    }:
        return "male"

    if normalized in {
        "female",
        "woman",
    }:
        return "female"

    return value


def normalize_date(value):
    if not value:
        return None

    if len(value) >= 10:
        return value[:10]

    return None


# ============================================================
# SUPABASE
# ============================================================


def fetch_fighters(supabase):
    fighters = []
    offset = 0

    while len(fighters) < BATCH_LIMIT:
        remaining = (
            BATCH_LIMIT
            - len(fighters)
        )

        current_page_size = min(
            SUPABASE_PAGE_SIZE,
            remaining
        )

        start = offset
        end = (
            start
            + current_page_size
            - 1
        )

        response = (
            supabase
            .table("fighters")
            .select(
                "id,"
                "display_name,"
                "date_of_birth,"
                "ufcstats_id,"
                "wikidata_id,"
                "wikidata_updated_at,"
                "wikidata_checked_at,"
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
            .is_(
                "wikidata_checked_at",
                "null"
            )
            .order(
                "display_name"
            )
            .range(
                start,
                end
            )
            .execute()
        )

        page = response.data or []

        if not page:
            break

        fighters.extend(page)

        print(
            "Supabase fighters fetched: "
            f"{len(fighters)}"
        )

        if len(page) < current_page_size:
            break

        offset += current_page_size

    return fighters[:BATCH_LIMIT]


def find_existing_qid_owner(
    supabase,
    qid,
):
    response = (
        supabase
        .table("fighters")
        .select(
            "id,"
            "display_name,"
            "date_of_birth,"
            "ufcstats_id,"
            "wikidata_id"
        )
        .eq(
            "wikidata_id",
            qid
        )
        .limit(1)
        .execute()
    )

    rows = response.data or []

    if not rows:
        return None

    return rows[0]


# ============================================================
# WIKIDATA RESULTS
# ============================================================


def parse_results(payload):
    candidates = defaultdict(
        dict
    )

    bindings = (
        payload
        .get("results", {})
        .get("bindings", [])
    )

    for binding in bindings:
        name = binding_value(
            binding,
            "wantedLabel"
        )

        item_uri = binding_value(
            binding,
            "item"
        )

        qid = qid_from_uri(
            item_uri
        )

        if (
            not name
            or not qid
        ):
            continue

        candidate = (
            candidates[name]
            .setdefault(
                qid,
                {
                    "qid": qid,
                    "date_of_birth": None,
                    "nationality": None,
                    "country_code": None,
                    "birth_place": None,
                    "gender": None,
                    "website": None,
                },
            )
        )

        dob = normalize_date(
            binding_value(
                binding,
                "dob"
            )
        )

        nationality = binding_value(
            binding,
            "countryLabel"
        )

        country_code = binding_value(
            binding,
            "countryCode"
        )

        birth_place = binding_value(
            binding,
            "birthPlaceLabel"
        )

        gender = normalize_gender(
            binding_value(
                binding,
                "genderLabel"
            )
        )

        website = binding_value(
            binding,
            "website"
        )

        if (
            dob
            and not candidate[
                "date_of_birth"
            ]
        ):
            candidate[
                "date_of_birth"
            ] = dob

        if (
            nationality
            and not candidate[
                "nationality"
            ]
        ):
            candidate[
                "nationality"
            ] = nationality

        if (
            country_code
            and not candidate[
                "country_code"
            ]
        ):
            candidate[
                "country_code"
            ] = country_code

        if (
            birth_place
            and not candidate[
                "birth_place"
            ]
        ):
            candidate[
                "birth_place"
            ] = birth_place

        if (
            gender
            and not candidate[
                "gender"
            ]
        ):
            candidate[
                "gender"
            ] = gender

        if (
            website
            and not candidate[
                "website"
            ]
        ):
            candidate[
                "website"
            ] = website

    return candidates


# ============================================================
# MATCHING V2
# ============================================================


def choose_candidate(
    fighter,
    candidates,
):
    if not candidates:
        return (
            None,
            "not_found_exact",
            "exact_name_no_result",
        )

    values = list(
        candidates.values()
    )

    fighter_dob = fighter.get(
        "date_of_birth"
    )

    # --------------------------------------------------------
    # La date de naissance FightIQ est disponible.
    # Elle devient notre preuve principale.
    # --------------------------------------------------------

    if fighter_dob:
        dob_matches = [
            candidate
            for candidate in values
            if (
                candidate.get(
                    "date_of_birth"
                )
                == fighter_dob
            )
        ]

        if len(dob_matches) == 1:
            return (
                dob_matches[0],
                "matched",
                "exact_name+dob",
            )

        if len(dob_matches) > 1:
            return (
                None,
                "ambiguous",
                "multiple_candidates_same_dob",
            )

        # Aucun DOB Wikidata ne correspond.
        if len(values) == 1:
            candidate = values[0]

            candidate_dob = (
                candidate.get(
                    "date_of_birth"
                )
            )

            if candidate_dob:
                return (
                    candidate,
                    "dob_mismatch",
                    "exact_name_dob_conflict",
                )

            return (
                candidate,
                "needs_review",
                "exact_name_missing_wikidata_dob",
            )

        return (
            None,
            "ambiguous",
            "multiple_candidates_no_dob_match",
        )

    # --------------------------------------------------------
    # Pas de date de naissance FightIQ.
    # On refuse désormais de matcher sur le nom seul.
    # --------------------------------------------------------

    if len(values) == 1:
        return (
            values[0],
            "needs_review",
            "exact_name_without_fightiq_dob",
        )

    return (
        None,
        "ambiguous",
        "multiple_candidates_without_fightiq_dob",
    )


# ============================================================
# TRACKING
# ============================================================


def save_tracking(
    supabase,
    fighter_id,
    timestamp,
    status,
    method=None,
    candidate_id=None,
    note=None,
):
    update = {
        "wikidata_match_status":
            status,
        "wikidata_match_method":
            method,
        "wikidata_candidate_id":
            candidate_id,
        "wikidata_match_note":
            note,
        "wikidata_checked_at":
            timestamp,
    }

    (
        supabase
        .table("fighters")
        .update(update)
        .eq(
            "id",
            fighter_id
        )
        .execute()
    )


# ============================================================
# CONFIRMED MATCH
# ============================================================


def save_match(
    supabase,
    fighter,
    candidate,
    timestamp,
):
    update = {
        "wikidata_id":
            candidate["qid"],

        "wikidata_updated_at":
            timestamp,

        "wikidata_checked_at":
            timestamp,

        "wikidata_match_status":
            "matched",

        "wikidata_match_method":
            "exact_name+dob",

        "wikidata_candidate_id":
            candidate["qid"],

        "wikidata_match_note":
            None,

        "fightiq_updated_at":
            timestamp,
    }

    if (
        candidate.get(
            "nationality"
        )
        and not fighter.get(
            "nationality"
        )
    ):
        update[
            "nationality"
        ] = candidate[
            "nationality"
        ]

    if (
        candidate.get(
            "country_code"
        )
        and not fighter.get(
            "country_code"
        )
    ):
        update[
            "country_code"
        ] = (
            candidate[
                "country_code"
            ]
            .upper()
        )

    if (
        candidate.get(
            "birth_place"
        )
        and not fighter.get(
            "birth_place"
        )
    ):
        update[
            "birth_place"
        ] = candidate[
            "birth_place"
        ]

    if (
        candidate.get(
            "gender"
        )
        and not fighter.get(
            "gender"
        )
    ):
        update[
            "gender"
        ] = candidate[
            "gender"
        ]

    if (
        candidate.get(
            "website"
        )
        and not fighter.get(
            "website"
        )
    ):
        update[
            "website"
        ] = candidate[
            "website"
        ]

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


# ============================================================
# QID CONFLICT
# ============================================================


def build_conflict_note(
    fighter,
    candidate,
    existing_owner,
):
    current_name = (
        fighter.get(
            "display_name"
        )
        or "unknown"
    )

    current_ufcstats = (
        fighter.get(
            "ufcstats_id"
        )
        or "unknown"
    )

    existing_name = (
        existing_owner.get(
            "display_name"
        )
        or "unknown"
    )

    existing_ufcstats = (
        existing_owner.get(
            "ufcstats_id"
        )
        or "unknown"
    )

    existing_dob = (
        existing_owner.get(
            "date_of_birth"
        )
        or "unknown"
    )

    candidate_dob = (
        candidate.get(
            "date_of_birth"
        )
        or "unknown"
    )

    return (
        f"QID {candidate['qid']} already used by "
        f"{existing_name} "
        f"(UFCStats {existing_ufcstats}, "
        f"DOB {existing_dob}). "
        f"Current fighter: {current_name} "
        f"(UFCStats {current_ufcstats}). "
        f"Wikidata DOB: {candidate_dob}."
    )


# ============================================================
# MAIN
# ============================================================


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

    print()
    print(
        "FightIQ Wikidata V2 batch: "
        f"{len(fighters)} fighters"
    )

    if not fighters:
        print(
            "No unchecked fighters "
            "remaining."
        )
        return

    timestamp = datetime.now(
        timezone.utc
    ).isoformat()

    matched = 0
    not_found = 0
    needs_review = 0
    ambiguous = 0
    dob_mismatch = 0
    qid_conflict = 0
    deferred = 0

    for start in range(
        0,
        len(fighters),
        SPARQL_BATCH_SIZE
    ):
        group = fighters[
            start:
            start + SPARQL_BATCH_SIZE
        ]

        names = [
            fighter[
                "display_name"
            ]
            for fighter in group
            if fighter.get(
                "display_name"
            )
        ]

        batch_number = (
            start
            // SPARQL_BATCH_SIZE
            + 1
        )

        print()
        print(
            "SPARQL batch "
            f"{batch_number}: "
            f"{len(names)} fighters"
        )

        query = build_query(
            names
        )

        payload = sparql_request(
            query
        )

        if payload is None:
            deferred += len(group)

            print(
                "BATCH DEFERRED: "
                "no FightIQ data changed."
            )

            time.sleep(
                REQUEST_DELAY
            )

            continue

        results = parse_results(
            payload
        )

        for fighter in group:
            name = fighter.get(
                "display_name"
            )

            fighter_candidates = (
                results.get(
                    name,
                    {}
                )
            )

            (
                candidate,
                status,
                method,
            ) = choose_candidate(
                fighter,
                fighter_candidates,
            )

            # =================================================
            # MATCH POTENTIELLEMENT VALIDÉ
            # =================================================

            if status == "matched":
                qid = candidate[
                    "qid"
                ]

                existing_owner = (
                    find_existing_qid_owner(
                        supabase,
                        qid,
                    )
                )

                if (
                    existing_owner
                    and existing_owner[
                        "id"
                    ] != fighter["id"]
                ):
                    note = build_conflict_note(
                        fighter,
                        candidate,
                        existing_owner,
                    )

                    save_tracking(
                        supabase,
                        fighter["id"],
                        timestamp,
                        "qid_conflict",
                        method,
                        qid,
                        note,
                    )

                    qid_conflict += 1

                    print(
                        "QID CONFLICT: "
                        f"{name} -> {qid} "
                        f"already used by "
                        f"{existing_owner.get('display_name')}"
                    )

                    continue

                save_match(
                    supabase,
                    fighter,
                    candidate,
                    timestamp,
                )

                matched += 1

                print(
                    "MATCH CONFIRMED: "
                    f"{name} -> {qid} "
                    "(name + DOB)"
                )

                continue

            # =================================================
            # PAS DE MATCH AUTOMATIQUE
            # =================================================

            candidate_id = None

            if candidate:
                candidate_id = (
                    candidate.get(
                        "qid"
                    )
                )

            if status == "not_found_exact":
                save_tracking(
                    supabase,
                    fighter["id"],
                    timestamp,
                    status,
                    method,
                    None,
                    (
                        "No exact English "
                        "Wikidata label or alias "
                        "found."
                    ),
                )

                not_found += 1

                print(
                    "NOT FOUND EXACT: "
                    f"{name}"
                )

            elif status == "needs_review":
                save_tracking(
                    supabase,
                    fighter["id"],
                    timestamp,
                    status,
                    method,
                    candidate_id,
                    (
                        "Candidate found but "
                        "automatic identity "
                        "validation is insufficient."
                    ),
                )

                needs_review += 1

                print(
                    "NEEDS REVIEW: "
                    f"{name}"
                    + (
                        f" -> {candidate_id}"
                        if candidate_id
                        else ""
                    )
                )

            elif status == "ambiguous":
                save_tracking(
                    supabase,
                    fighter["id"],
                    timestamp,
                    status,
                    method,
                    candidate_id,
                    (
                        "Multiple possible "
                        "Wikidata candidates."
                    ),
                )

                ambiguous += 1

                print(
                    "AMBIGUOUS: "
                    f"{name}"
                )

            elif status == "dob_mismatch":
                candidate_dob = None

                if candidate:
                    candidate_dob = (
                        candidate.get(
                            "date_of_birth"
                        )
                    )

                note = (
                    "FightIQ DOB: "
                    f"{fighter.get('date_of_birth')}; "
                    "Wikidata DOB: "
                    f"{candidate_dob}."
                )

                save_tracking(
                    supabase,
                    fighter["id"],
                    timestamp,
                    status,
                    method,
                    candidate_id,
                    note,
                )

                dob_mismatch += 1

                print(
                    "DOB MISMATCH: "
                    f"{name}"
                    + (
                        f" -> {candidate_id}"
                        if candidate_id
                        else ""
                    )
                )

        time.sleep(
            REQUEST_DELAY
        )

    print()
    print(
        "================================"
    )
    print(
        "FightIQ Wikidata V2 complete"
    )
    print(
        "================================"
    )

    print(
        f"Matched confirmed: {matched}"
    )

    print(
        f"Not found exact: {not_found}"
    )

    print(
        f"Needs review: {needs_review}"
    )

    print(
        f"Ambiguous: {ambiguous}"
    )

    print(
        f"DOB mismatch: {dob_mismatch}"
    )

    print(
        f"QID conflicts: {qid_conflict}"
    )

    print(
        f"Deferred: {deferred}"
    )


if __name__ == "__main__":
    main()
