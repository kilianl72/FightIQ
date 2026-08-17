#!/usr/bin/env python3
"""Synchronise les rankings UFC Cito sans remise à zéro préalable.

Le plan complet est calculé avant la première écriture. Si la source ou la base
échoue pendant cette phase, les rankings précédents restent inchangés. Les noms
non résolus sont signalés mais n'empêchent pas les mises à jour sûres ; dans ce
cas, le nettoyage des anciens classés est reporté pour éviter une perte de data.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from typing import Any
from urllib.request import Request, urlopen

try:
    from supabase import create_client
except ImportError:  # Permet les tests unitaires sans dépendance Supabase.
    create_client = None


API_URL = "https://api.citoapi.com/api/v1/ufc/rankings"
PAGE_SIZE = 1000
MIN_RANKING_ROWS = 10
RANKING_FIELDS = (
    "ufc_rank",
    "p4p_rank",
    "champion_status",
    "interim_champion",
)

NAME_ALIASES = {
    "michael venom page": "michael page",
    "jose miguel delgado": "jose delgado",
}

FORCED_UFCSTATS_IDS = {
    "jean silva": "52ef95b5860fb28c",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_name(value: Any) -> str:
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value.lower().strip())
    return re.sub(r"\s+", " ", value).strip()


def normalize_slug(value: Any) -> str:
    return normalize_name(value).replace(" ", "-")


def fetch_rankings(api_key: str) -> list[dict[str, Any]]:
    request = Request(
        API_URL,
        headers={
            "x-api-key": api_key,
            "User-Agent": "FightIQ-ranking-sync/2.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload.get("success"):
        raise RuntimeError("Cito API returned success=false")
    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Cito ranking response")
    if len(data) < MIN_RANKING_ROWS:
        raise RuntimeError(
            f"Cito ranking snapshot is unexpectedly small: {len(data)} rows"
        )
    return data


def fetch_all_fighters(sb: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    fields = (
        "id,fightiq_id,display_name,first_name,last_name,slug,cito_slug,"
        "ufcstats_id,cito_id,current_division,ufc_rank,p4p_rank,"
        "champion_status,interim_champion"
    )
    while True:
        batch = (
            sb.table("fighters")
            .select(fields)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        ).data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def add_index_value(
    index: dict[str, list[dict[str, Any]]],
    key: str,
    fighter: dict[str, Any],
) -> None:
    if key:
        index.setdefault(key, []).append(fighter)


def build_indexes(fighters: list[dict[str, Any]]) -> dict[str, Any]:
    names: dict[str, list[dict[str, Any]]] = {}
    slugs: dict[str, list[dict[str, Any]]] = {}
    ufcstats: dict[str, dict[str, Any]] = {}
    cito: dict[str, dict[str, Any]] = {}
    for fighter in fighters:
        full_name = " ".join(
            part
            for part in (fighter.get("first_name"), fighter.get("last_name"))
            if part
        )
        add_index_value(names, normalize_name(fighter.get("display_name")), fighter)
        add_index_value(names, normalize_name(full_name), fighter)
        add_index_value(slugs, normalize_slug(fighter.get("slug")), fighter)
        add_index_value(slugs, normalize_slug(fighter.get("cito_slug")), fighter)
        if fighter.get("ufcstats_id"):
            ufcstats[str(fighter["ufcstats_id"])] = fighter
        if fighter.get("cito_id"):
            cito[str(fighter["cito_id"])] = fighter
    return {"names": names, "slugs": slugs, "ufcstats": ufcstats, "cito": cito}


def unique_index_match(
    index: dict[str, list[dict[str, Any]]], key: str
) -> tuple[dict[str, Any] | None, str]:
    matches = index.get(key, []) if key else []
    unique = {str(item.get("id")): item for item in matches}
    if len(unique) == 1:
        return next(iter(unique.values())), "matched"
    if len(unique) > 1:
        return None, "ambiguous"
    return None, "unmatched"


def resolve_fighter(
    item: dict[str, Any], indexes: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    fighter_name = item.get("fighterName")
    normalized_name = normalize_name(fighter_name)

    reported_ufcstats = item.get("ufcStatsId")
    if reported_ufcstats and str(reported_ufcstats) in indexes["ufcstats"]:
        return indexes["ufcstats"][str(reported_ufcstats)], "ufcstats_id"

    reported_cito = item.get("fighterId") or item.get("citoId")
    if reported_cito and str(reported_cito) in indexes["cito"]:
        return indexes["cito"][str(reported_cito)], "cito_id"

    forced_id = FORCED_UFCSTATS_IDS.get(normalized_name)
    if forced_id and forced_id in indexes["ufcstats"]:
        return indexes["ufcstats"][forced_id], "forced_ufcstats_id"

    slug = normalize_slug(item.get("fighterSlug"))
    slug_match, slug_status = unique_index_match(indexes["slugs"], slug)
    if slug_match:
        return slug_match, "slug"
    if slug_status == "ambiguous":
        return None, "ambiguous_slug"

    normalized_name = normalize_name(NAME_ALIASES.get(normalized_name, normalized_name))
    name_match, name_status = unique_index_match(indexes["names"], normalized_name)
    if name_match:
        return name_match, "name"
    if name_status == "ambiguous":
        return None, "ambiguous_name"
    return None, "unmatched"


def is_p4p_division(item: dict[str, Any]) -> bool:
    combined = normalize_name(
        f"{item.get('division') or ''} {item.get('normalizedDivision') or ''}"
    )
    return (
        "pound for pound" in combined
        or "p4p" in combined
        or "poundforpound" in combined.replace(" ", "")
    )


def parse_rank(item: dict[str, Any]) -> int | None:
    rank = item.get("rank")
    if isinstance(rank, int):
        return rank
    if isinstance(rank, str) and rank.isdigit():
        return int(rank)
    return None


def empty_ranking_state() -> dict[str, Any]:
    return {
        "ufc_rank": None,
        "p4p_rank": None,
        "champion_status": None,
        "interim_champion": False,
    }


def apply_ranking_item(target: dict[str, Any], item: dict[str, Any]) -> None:
    rank = parse_rank(item)
    if is_p4p_division(item):
        if rank is not None:
            current = target.get("p4p_rank")
            if current is None or rank < current:
                target["p4p_rank"] = rank
        return

    division = item.get("division") or item.get("normalizedDivision")
    if division:
        target["current_division"] = division
    if rank is not None:
        target["ufc_rank"] = rank

    rank_text = str(item.get("rankText") or "").upper()
    if item.get("isChampion") or rank_text in {"C", "IC"}:
        status = item.get("championStatus")
        if not status:
            status = "Interim Champion" if rank_text == "IC" else "Champion"
        target["champion_status"] = status
        target["interim_champion"] = (
            "interim" in normalize_name(status) or rank_text == "IC"
        )


def plan_rankings(
    rankings: list[dict[str, Any]],
    fighters: list[dict[str, Any]],
    now: str,
) -> tuple[list[tuple[Any, dict[str, Any]]], dict[str, Any]]:
    if len(rankings) < MIN_RANKING_ROWS:
        raise RuntimeError(f"Ranking snapshot too small: {len(rankings)} rows")

    indexes = build_indexes(fighters)
    fighters_by_id = {fighter["id"]: fighter for fighter in fighters}
    desired: dict[Any, dict[str, Any]] = {}
    unmatched: list[str] = []
    ambiguous: list[str] = []
    match_counts: dict[str, int] = {}

    for item in rankings:
        fighter, match_type = resolve_fighter(item, indexes)
        fighter_name = str(item.get("fighterName") or "")
        if not fighter:
            if match_type.startswith("ambiguous"):
                ambiguous.append(fighter_name)
            else:
                unmatched.append(fighter_name)
            continue
        match_counts[match_type] = match_counts.get(match_type, 0) + 1
        target = desired.setdefault(fighter["id"], empty_ranking_state())
        apply_ranking_item(target, item)
        cito_slug = item.get("fighterSlug")
        if cito_slug and not fighter.get("slug"):
            target["slug"] = cito_slug

    if not desired:
        raise RuntimeError("No ranking fighter could be resolved; previous data preserved")

    current_resolved_fighters = len(desired)
    previous_ranked_fighters = sum(
        1
        for fighter in fighters
        if any(fighter.get(field) for field in RANKING_FIELDS)
    )
    coverage_ratio = (
        current_resolved_fighters / previous_ranked_fighters
        if previous_ranked_fighters
        else 1.0
    )
    source_fully_resolved = not unmatched and not ambiguous
    cleanup_coverage_safe = coverage_ratio >= 0.75
    stale_cleanup_skipped = not (
        source_fully_resolved and cleanup_coverage_safe
    )
    stale_fighters_cleared = 0
    if not stale_cleanup_skipped:
        resolved_ids = set(desired)
        for fighter in fighters:
            has_old_ranking = any(fighter.get(field) for field in RANKING_FIELDS)
            if has_old_ranking and fighter["id"] not in resolved_ids:
                desired[fighter["id"]] = empty_ranking_state()
                stale_fighters_cleared += 1

    updates: list[tuple[Any, dict[str, Any]]] = []
    for fighter_id, target in desired.items():
        current = fighters_by_id[fighter_id]
        patch = {
            field: value
            for field, value in target.items()
            if current.get(field) != value
        }
        if patch:
            patch["fightiq_updated_at"] = now
            updates.append((fighter_id, patch))

    report = {
        "source_rows": len(rankings),
        "resolved_fighters": current_resolved_fighters,
        "updates": len(updates),
        "unmatched": unmatched,
        "ambiguous": ambiguous,
        "match_counts": match_counts,
        "previous_ranked_fighters": previous_ranked_fighters,
        "ranking_coverage_ratio": round(coverage_ratio, 4),
        "stale_cleanup_skipped": stale_cleanup_skipped,
        "stale_fighters_cleared": stale_fighters_cleared,
    }
    return updates, report


def apply_updates(
    sb: Any,
    updates: list[tuple[Any, dict[str, Any]]],
    dry_run: bool,
) -> None:
    if dry_run:
        print("DRY RUN: previous rankings remain unchanged")
        return
    for fighter_id, patch in updates:
        sb.table("fighters").update(patch).eq("id", fighter_id).execute()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("DRY_RUN", "false").lower() == "true",
        help="calcule et valide sans écrire dans Supabase",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    supabase_url = os.environ.get("SUPABASE_URL")
    supabase_key = os.environ.get("SUPABASE_SECRET_KEY")
    cito_key = os.environ.get("CITO_API_KEY")
    if not supabase_url or not supabase_key or not cito_key:
        raise RuntimeError(
            "Missing SUPABASE_URL, SUPABASE_SECRET_KEY or CITO_API_KEY"
        )
    if create_client is None:
        raise RuntimeError("The supabase Python package is not installed")

    rankings = fetch_rankings(cito_key)
    sb = create_client(supabase_url, supabase_key)
    fighters = fetch_all_fighters(sb)
    updates, report = plan_rankings(rankings, fighters, utc_now())
    print("===== FIGHTIQ RANKING PLAN =====")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    apply_updates(sb, updates, args.dry_run)
    print("FIGHTIQ RANKING SYNC COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FIGHTIQ RANKING SYNC FAILED: {exc}")
        raise
