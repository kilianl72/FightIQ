import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from supabase import create_client


SPARQL_URL = "https://query.wikidata.org/sparql"

USER_AGENT = (
    "FightIQ/1.0 "
    "(MMA fighter database enrichment)"
)

# Nombre de combattants FightIQ examinés par run
BATCH_LIMIT = 5000

# Nombre de noms envoyés dans une seule requête SPARQL
SPARQL_BATCH_SIZE = 20

# Petite pause entre les requêtes groupées
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

    # Important :
    # on n'arrête PAS le workflow.
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

    # On ne garde que les personnes
    # identifiées comme combattants MMA.
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

    # Wikidata renvoie généralement :
    # 1990-01-01T00:00:00Z
    if len(value) >= 10:
        return value[:10]

    return None


def fetch_fighters(supabase):
    response = (
        supabase
        .table("fighters")
        .select(
            "id,"
            "display_name,"
            "date_of_birth,"
            "wikidata_id,"
            "wikidata_updated_at,"
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
            "wikidata_updated_at",
            "null"
        )
        .order(
            "display_name"
        )
        .limit(
            BATCH_LIMIT
        )
        .execute()
    )

    return response.data or []


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

        nationality = (
            binding_value(
                binding,
                "countryLabel"
            )
        )

        country_code = (
            binding_value(
                binding,
                "countryCode"
            )
        )

        birth_place = (
            binding_value(
                binding,
                "birthPlaceLabel"
            )
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


def choose_candidate(
    fighter,
    candidates
):
    if not candidates:
        return None, "not_found"

    values = list(
        candidates.values()
    )

    fighter_dob = fighter.get(
        "date_of_birth"
    )

    # Si FightIQ possède déjà la date
    # de naissance, on s'en sert pour
    # éviter les homonymes.
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
                "dob"
            )

        # Un seul résultat Wikidata,
        # mais sa date contredit
        # explicitement UFCStats :
        # on refuse le rapprochement.
        if len(values) == 1:
            candidate_dob = (
                values[0].get(
                    "date_of_birth"
                )
            )

            if (
                candidate_dob
                and candidate_dob
                != fighter_dob
            ):
                return (
                    None,
                    "dob_mismatch"
                )

    if len(values) == 1:
        return values[0], "unique"

    return None, "ambiguous"


def mark_checked(
    supabase,
    fighter_id,
    timestamp,
):
    (
        supabase
        .table("fighters")
        .update({
            "wikidata_updated_at":
                timestamp
        })
        .eq(
            "id",
            fighter_id
        )
        .execute()
    )


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

    if not fighters:
        print(
            "No unchecked fighters "
            "remaining."
        )
        return

    now = datetime.now(
        timezone.utc
    ).isoformat()

    matched = 0
    not_found = 0
    ambiguous = 0
    dob_mismatch = 0
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

        # Si Wikidata bloque ce lot,
        # on ne marque aucun combattant
        # comme vérifié.
        # Ils seront repris lors
        # d'un prochain run.
        if payload is None:
            deferred += len(group)

            print(
                "Batch deferred. "
                "No FightIQ data changed."
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

            candidate, status = (
                choose_candidate(
                    fighter,
                    fighter_candidates,
                )
            )

            if candidate:
                save_match(
                    supabase,
                    fighter,
                    candidate,
                    now,
                )

                matched += 1

                print(
                    "MATCH: "
                    f"{name} -> "
                    f"{candidate['qid']} "
                    f"({status})"
                )

                continue

            # La requête a bien fonctionné.
            # On note donc que ce combattant
            # a déjà été vérifié afin de
            # progresser dans les 4 582.
            mark_checked(
                supabase,
                fighter["id"],
                now,
            )

            if status == "not_found":
                not_found += 1

                print(
                    "NOT FOUND: "
                    f"{name}"
                )

            elif status == "ambiguous":
                ambiguous += 1

                print(
                    "AMBIGUOUS: "
                    f"{name}"
                )

            elif (
                status
                == "dob_mismatch"
            ):
                dob_mismatch += 1

                print(
                    "DOB MISMATCH: "
                    f"{name}"
                )

        time.sleep(
            REQUEST_DELAY
        )

    print()
    print(
        "FightIQ Wikidata "
        "enrichment complete"
    )

    print(
        f"Matched: {matched}"
    )

    print(
        f"Not found: {not_found}"
    )

    print(
        f"Ambiguous: {ambiguous}"
    )

    print(
        "DOB mismatch: "
        f"{dob_mismatch}"
    )

    print(
        f"Deferred: {deferred}"
    )


if __name__ == "__main__":
    main()
