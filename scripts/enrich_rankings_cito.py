import os
import re
import unicodedata
from datetime import datetime, timezone
from urllib.request import Request, urlopen
import json

from supabase import create_client

API_URL = "https://api.citoapi.com/api/v1/ufc/rankings"

NAME_ALIASES = {
    "michael venom page": "michael page",
    "jose miguel delgado": "jose delgado",
}

FORCED_UFCSTATS_IDS = {
    "jean silva": "52ef95b5860fb28c",
}


def normalize_name(value):
    if not value:
        return ""

    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        ch for ch in value
        if not unicodedata.combining(ch)
    )

    value = value.lower().strip()
    value = re.sub(r"[^a-z0-9]+", " ", value)

    return re.sub(r"\s+", " ", value).strip()


def fetch_rankings(api_key):
    request = Request(
        API_URL,
        headers={
            "x-api-key": api_key,
            "User-Agent": "FightIQ/1.0",
            "Accept": "application/json",
        },
    )

    with urlopen(request, timeout=30) as response:
        payload = json.loads(
            response.read().decode("utf-8")
        )

    if not payload.get("success"):
        raise RuntimeError(
            "Cito API returned success=false"
        )

    data = payload.get("data")

    if not isinstance(data, list):
        raise RuntimeError(
            "Unexpected Cito response"
        )

    return data


def fetch_all_fighters(supabase):
    rows = []
    start = 0
    page_size = 1000

    while True:
        response = (
            supabase
            .table("fighters")
            .select(
                "id,display_name,first_name,"
                "last_name,slug,ufcstats_id"
            )
            .range(
                start,
                start + page_size - 1
            )
            .execute()
        )

        batch = response.data or []
        rows.extend(batch)

        if len(batch) < page_size:
            break

        start += page_size

    return rows


def build_name_index(fighters):
    index = {}

    for fighter in fighters:
        full_name = " ".join(
            part
            for part in [
                fighter.get("first_name"),
                fighter.get("last_name"),
            ]
            if part
        )

        candidates = {
            normalize_name(
                fighter.get("display_name")
            ),
            normalize_name(full_name),
        }

        for key in candidates:
            if not key:
                continue

            index.setdefault(
                key,
                []
            ).append(fighter)

    return index


def build_ufcstats_index(fighters):
    return {
        fighter["ufcstats_id"]: fighter
        for fighter in fighters
        if fighter.get("ufcstats_id")
    }


def is_p4p_division(item):
    division = normalize_name(
        item.get("division")
    )

    normalized = normalize_name(
        item.get("normalizedDivision")
    )

    combined = (
        f"{division} {normalized}"
    )

    return (
        "pound for pound" in combined
        or "p4p" in combined
        or "poundforpound"
        in combined.replace(" ", "")
    )


def parse_rank(item):
    rank = item.get("rank")

    if isinstance(rank, int):
        return rank

    if (
        isinstance(rank, str)
        and rank.isdigit()
    ):
        return int(rank)

    return None


def resolve_fighter(
    fighter_name,
    name_index,
    ufcstats_index,
):
    normalized = normalize_name(
        fighter_name
    )

    forced_id = FORCED_UFCSTATS_IDS.get(
        normalized
    )

    if forced_id:
        fighter = ufcstats_index.get(
            forced_id
        )

        if fighter:
            return fighter, "forced"

    alias = NAME_ALIASES.get(
        normalized
    )

    if alias:
        normalized = normalize_name(
            alias
        )

    matches = name_index.get(
        normalized,
        []
    )

    if len(matches) == 1:
        return matches[0], "name"

    if len(matches) == 0:
        return None, "unmatched"

    return None, "ambiguous"


def main():
    supabase_url = os.environ.get(
        "SUPABASE_URL"
    )

    supabase_key = os.environ.get(
        "SUPABASE_SECRET_KEY"
    )

    cito_key = os.environ.get(
        "CITO_API_KEY"
    )

    if not supabase_url or not supabase_key:
        raise RuntimeError(
            "Missing Supabase secrets"
        )

    if not cito_key:
        raise RuntimeError(
            "Missing CITO_API_KEY"
        )

    supabase = create_client(
        supabase_url,
        supabase_key
    )

    rankings = fetch_rankings(cito_key)

    fighters = fetch_all_fighters(
        supabase
    )

    name_index = build_name_index(
        fighters
    )

    ufcstats_index = build_ufcstats_index(
        fighters
    )

    print(
        f"Cito rows: {len(rankings)}"
    )

    print(
        f"FightIQ fighters: "
        f"{len(fighters)}"
    )

    (
        supabase
        .table("fighters")
        .update({
            "ufc_rank": None,
            "p4p_rank": None,
            "champion_status": None,
            "interim_champion": False,
        })
        .not_.is_("id", "null")
        .execute()
    )

    updates = {}
    unmatched = []
    ambiguous = []
    forced_matches = []

    now = datetime.now(
        timezone.utc
    ).isoformat()

    for item in rankings:
        fighter_name = item.get(
            "fighterName"
        )

        fighter, match_type = resolve_fighter(
            fighter_name,
            name_index,
            ufcstats_index,
        )

        if not fighter:
            if match_type == "unmatched":
                unmatched.append(
                    fighter_name
                )
            else:
                ambiguous.append(
                    fighter_name
                )
            continue

        if match_type == "forced":
            forced_matches.append(
                fighter_name
            )

        fighter_id = fighter["id"]

        update = updates.setdefault(
            fighter_id,
            {
                "fightiq_updated_at": now
            },
        )

        rank = parse_rank(item)

        is_champion = bool(
            item.get("isChampion")
        )

        champion_status = item.get(
            "championStatus"
        )

        rank_text = item.get(
            "rankText"
        )

        if is_p4p_division(item):
            if rank is not None:
                current = update.get(
                    "p4p_rank"
                )

                if (
                    current is None
                    or rank < current
                ):
                    update[
                        "p4p_rank"
                    ] = rank

            continue

        division = (
            item.get("division")
            or item.get(
                "normalizedDivision"
            )
        )

        if division:
            update[
                "current_division"
            ] = division

        if rank is not None:
            update[
                "ufc_rank"
            ] = rank

        if (
            is_champion
            or (
                isinstance(
                    rank_text,
                    str
                )
                and rank_text.upper()
                in {"C", "IC"}
            )
        ):
            if champion_status:
                status = champion_status
            elif (
                str(rank_text).upper()
                == "IC"
            ):
                status = (
                    "Interim Champion"
                )
            else:
                status = "Champion"

            update[
                "champion_status"
            ] = status

            update[
                "interim_champion"
            ] = (
                "interim"
                in normalize_name(status)
                or str(
                    rank_text
                ).upper()
                == "IC"
            )

        cito_slug = item.get(
            "fighterSlug"
        )

        if (
            cito_slug
            and not fighter.get("slug")
        ):
            update[
                "slug"
            ] = cito_slug

    updated = 0

    for fighter_id, update in updates.items():
        (
            supabase
            .table("fighters")
            .update(update)
            .eq("id", fighter_id)
            .execute()
        )

        updated += 1

    print(
        "FightIQ ranking enrichment "
        f"complete: {updated} fighters"
    )

    print(
        f"Forced matches: "
        f"{len(forced_matches)}"
    )

    if forced_matches:
        print(
            "Forced match names:",
            forced_matches
        )

    print(
        f"Unmatched: {len(unmatched)}"
    )

    if unmatched:
        print(
            "Unmatched sample:",
            unmatched[:20]
        )

    print(
        f"Ambiguous: {len(ambiguous)}"
    )

    if ambiguous:
        print(
            "Ambiguous sample:",
            ambiguous[:20]
        )


if __name__ == "__main__":
    main()
