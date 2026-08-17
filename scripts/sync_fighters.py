#!/usr/bin/env python3
"""Synchronisation canonique et incrémentale des fiches combattants FightIQ.

Ce script remplace les anciens scripts UFCStats/Cito/consolidation/audit des
fiches combattants. Le ranking conserve son propre script et son propre workflow.
Il conserve les identités déjà présentes dans Supabase et ne modifie que les
fiches nouvelles ou dont les valeurs ont réellement changé.

État final autorisé pour un combattant canonique :
  * UFCStats + Cito ;
  * UFCStats seul ;
  * Cito seul avec preuve MMA suffisante.

Les profils Power Slap/non-MMA sont exclus de ``fighters``. Un profil Cito
ambigu n'est jamais inséré ni associé silencieusement : il est placé en
quarantaine dans le registre de résolution, sans bloquer les mises à jour sûres.
Un historique de combats compatible ne suffit jamais à fusionner deux alias :
il doit être corroboré par une date ou un lieu de naissance, ou par plusieurs
mensurations compatibles.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

try:
    from supabase import create_client
except ImportError:  # Permet d'exécuter les tests unitaires sans Supabase.
    create_client = None


UFC_DETAILS_URL = (
    "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/"
    "main/ufc_fighter_details.csv"
)
UFC_TOTT_URL = (
    "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/"
    "main/ufc_fighter_tott.csv"
)
UFC_FIGHT_RESULTS_URL = (
    "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/"
    "main/ufc_fight_results.csv"
)
UFC_EVENT_DETAILS_URL = (
    "https://raw.githubusercontent.com/Greco1899/scrape_ufc_stats/"
    "main/ufc_event_details.csv"
)
CITO_FIGHTERS_URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"
CITO_FIGHT_HISTORY_URL = "https://api.citoapi.com/api/v1/ufc/fighters/{slug}/fights"
DEFAULT_RESOLUTION_FILES = (
    "data/cito_identity_resolution_v5_6.json,"
    "data/cito_identity_overrides.json"
)
PAGE_SIZE = 1000

FIGHTER_FIELDS = (
    "id,fightiq_id,first_name,last_name,display_name,nickname,date_of_birth,"
    "height_cm,reach_cm,stance,current_weight_kg,ufcstats_id,ufc_profile_url,"
    "cito_id,cito_slug,slug,cito_status,is_active,current_division,"
    "champion_status,interim_champion,ufc_rank,p4p_rank,place_of_birth,"
    "trains_at,fighting_style,leg_reach_cm,octagon_debut,cito_profile_url,"
    "photo_url,photo_source,photo_original_source,photo_rights_status,"
    "body_image_url,cito_record_wins,cito_record_losses,cito_record_draws,"
    "cito_record_nc,cito_sig_strikes_landed,cito_sig_strikes_attempted,"
    "cito_striking_accuracy,cito_sig_strikes_landed_per_min,"
    "cito_sig_strikes_absorbed_per_min,cito_sig_strike_defense,"
    "cito_takedowns_landed,cito_takedowns_attempted,cito_takedown_accuracy,"
    "cito_takedown_defense,cito_takedown_avg_per_15,"
    "cito_submission_avg_per_15,cito_knockdown_avg,"
    "cito_average_fight_time_seconds,cito_strikes_head_pct,"
    "cito_strikes_body_pct,cito_strikes_leg_pct,cito_strikes_standing_pct,"
    "cito_strikes_clinch_pct,cito_strikes_ground_pct,cito_wins_ko_tko,"
    "cito_wins_submission,cito_wins_decision,cito_wins_ko_tko_pct,"
    "cito_wins_submission_pct,cito_wins_decision_pct,cito_stats_source,"
    "cito_stats_freshness,cito_stats_synced_at,source_updated_at,"
    "cito_synced_at,fightiq_updated_at"
)

SOURCE_FIELDS = "fightiq_id,source,source_id,source_name,is_primary,updated_at"
RESOLUTION_FIELDS = (
    "cito_id,name,first_name,last_name,nickname,slug,division,status,is_active,"
    "record_text,place_of_birth,height_inches,weight_lbs,reach_inches,stance,"
    "birth_date,photo_url,profile_url,stats_available,raw_json,last_seen_at,"
    "resolution_status,resolution_reason,matched_fightiq_id,"
    "matched_ufcstats_id,resolved_at,review_status,review_classification,"
    "admin_action,target_fightiq_id,target_ufcstats_id,manual_profile,"
    "admin_notes,reviewed_by,reviewed_at,action_applied_at"
)

REVIEW_FIELDS = (
    "review_status",
    "review_classification",
    "admin_action",
    "target_fightiq_id",
    "target_ufcstats_id",
    "manual_profile",
    "admin_notes",
    "reviewed_by",
    "reviewed_at",
    "action_applied_at",
)

TERMINAL_EXCLUSION_CLASSIFICATIONS = {
    "power_slap",
    "non_mma",
    "test_placeholder",
    "not_a_fighter",
    "other",
}

STABLE_CITO_FIELDS = {
    "first_name",
    "last_name",
    "nickname",
    "date_of_birth",
    "height_cm",
    "reach_cm",
    "stance",
    "current_weight_kg",
    "place_of_birth",
    "octagon_debut",
}

NON_MMA_MARKERS = (
    "power slap",
    "powerslap",
    "slap fighting championship",
)

MMA_MARKERS = (
    "mixed martial arts",
    "mixed-martial-arts",
    "ultimate fighting championship",
    "ufcstats",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def present(value: Any) -> bool:
    return value is not None and (
        not isinstance(value, str) or bool(value.strip())
    )


def clean(value: Any) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return None if value in {"", "--", "N/A", "None"} else value


def as_float(value: Any) -> float | None:
    if not present(value):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def as_int(value: Any) -> int | None:
    if not present(value):
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def inches_to_cm(value: Any) -> float | None:
    number = as_float(value)
    return round(number * 2.54, 2) if number is not None and number > 0 else None


def lbs_to_kg(value: Any) -> float | None:
    number = as_float(value)
    return (
        round(number * 0.45359237, 2)
        if number is not None and number > 0
        else None
    )


def normalize_name(value: Any) -> str:
    value = unicodedata.normalize("NFKD", str(value or ""))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = re.sub(r"[^a-z0-9]+", " ", value.lower().strip())
    return re.sub(r"\s+", " ", value).strip()


# Un pays seul est trop large pour confirmer l'identité d'une personne. Cette
# liste n'est utilisée que pour empêcher un pays identique de devenir une preuve
# biographique décisive ; il reste visible comme indice secondaire dans le
# rapport de quarantaine.
BROAD_PLACE_NAMES = {
    normalize_name(value)
    for value in (
        "Argentina",
        "Australia",
        "Austria",
        "Belgium",
        "Brazil",
        "Brasil",
        "Bulgaria",
        "Canada",
        "Chile",
        "China",
        "Colombia",
        "Croatia",
        "Cuba",
        "Czech Republic",
        "Czechia",
        "Denmark",
        "Ecuador",
        "England",
        "Finland",
        "France",
        "Georgia",
        "Germany",
        "Greece",
        "Hungary",
        "Iceland",
        "India",
        "Ireland",
        "Israel",
        "Italy",
        "Jamaica",
        "Japan",
        "Kazakhstan",
        "Kyrgyzstan",
        "Mexico",
        "Moldova",
        "Mongolia",
        "Morocco",
        "Netherlands",
        "New Zealand",
        "Nigeria",
        "Northern Ireland",
        "Norway",
        "Peru",
        "Philippines",
        "Poland",
        "Portugal",
        "Puerto Rico",
        "Romania",
        "Russia",
        "Russian Federation",
        "Scotland",
        "Serbia",
        "Slovakia",
        "South Africa",
        "South Korea",
        "Spain",
        "Sweden",
        "Switzerland",
        "Thailand",
        "Turkey",
        "Turkiye",
        "Ukraine",
        "United Arab Emirates",
        "United Kingdom",
        "United States",
        "United States of America",
        "USA",
        "US",
        "Uzbekistan",
        "Venezuela",
        "Wales",
    )
}


def identity_name_variants(
    display_name: Any = None,
    first_name: Any = None,
    last_name: Any = None,
    nickname: Any = None,
) -> set[str]:
    first = clean(first_name)
    last = clean(last_name)
    nick = clean(nickname)
    values = {
        normalize_name(display_name),
        normalize_name(" ".join(part for part in (first, last) if part)),
        normalize_name(" ".join(part for part in (last, first) if part)),
        normalize_name(nick),
        normalize_name(" ".join(part for part in (first, nick, last) if part)),
    }
    values.discard("")
    return values


def normalize_id(value: Any) -> str | None:
    value = clean(value)
    return value.strip() if value else None


def iso_date(value: Any) -> str | None:
    return str(value)[:10] if present(value) else None


def nested_value(obj: Any, *keys: str) -> Any:
    current = obj
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def stable_fingerprint(value: Any) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def semantic_cito_payload(profile: dict[str, Any]) -> dict[str, Any]:
    """Sous-ensemble déclenchant une réévaluation ou une mise à jour."""
    stats = profile.get("stats") or {}
    record = profile.get("record") or {}
    return {
        "id": profile.get("id"),
        "ufcStatsId": profile.get("ufcStatsId"),
        "name": profile.get("name"),
        "firstName": profile.get("firstName"),
        "lastName": profile.get("lastName"),
        "nickname": profile.get("nickname"),
        "slug": profile.get("slug"),
        "status": profile.get("status"),
        "isActive": profile.get("isActive"),
        "division": profile.get("division"),
        "birthDate": cito_birth_date(profile),
        "placeOfBirth": profile.get("placeOfBirth"),
        "bioFields": ((profile.get("raw") or {}).get("bioFields") or {}),
        "heightInches": profile.get("heightInches"),
        "reachInches": profile.get("reachInches"),
        "weightLbs": profile.get("weightLbs"),
        "legReachInches": profile.get("legReachInches"),
        "stance": profile.get("stance"),
        "trainsAt": profile.get("trainsAt"),
        "fightingStyle": profile.get("fightingStyle"),
        "octagonDebut": profile.get("octagonDebut"),
        "recordText": profile.get("recordText"),
        "record": record,
        "recordWins": profile.get("recordWins"),
        "recordLosses": profile.get("recordLosses"),
        "recordDraws": profile.get("recordDraws"),
        "recordNoContest": profile.get("recordNoContest"),
        "profileUrl": profile.get("profileUrl"),
        "updatedAt": profile.get("updatedAt"),
        "sourceUpdatedAt": profile.get("sourceUpdatedAt"),
        "lastSyncedAt": profile.get("lastSyncedAt"),
        "historySummary": profile.get("historySummary"),
        "stats": stats,
    }


def cito_fingerprint(profile: dict[str, Any]) -> str:
    return stable_fingerprint(semantic_cito_payload(profile))


def stored_cito_fingerprint(row: dict[str, Any] | None) -> str | None:
    raw = (row or {}).get("raw_json")
    return cito_fingerprint(raw) if isinstance(raw, dict) else None


def flatten_text(value: Any) -> str:
    parts: list[str] = []

    def walk(item: Any) -> None:
        if isinstance(item, dict):
            for key, child in item.items():
                parts.append(str(key))
                walk(child)
        elif isinstance(item, list):
            for child in item:
                walk(child)
        elif item is not None:
            parts.append(str(item))

    walk(value)
    return normalize_name(" ".join(parts))


def cito_birth_date(profile: dict[str, Any]) -> str | None:
    if present(profile.get("birthDate")):
        return iso_date(profile.get("birthDate"))
    graph = (((profile.get("raw") or {}).get("jsonLd") or {}).get("@graph") or [])
    for item in graph:
        if not isinstance(item, dict):
            continue
        entity = item.get("mainEntity")
        if isinstance(entity, dict) and present(entity.get("birthDate")):
            return iso_date(entity.get("birthDate"))
    return None


def raw_bio(profile: dict[str, Any]) -> dict[str, Any]:
    bio = ((profile.get("raw") or {}).get("bioFields") or {})
    if not isinstance(bio, dict):
        return {}
    return {normalize_name(key): value for key, value in bio.items()}


def bio_value(bio: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = bio.get(normalize_name(key))
        if present(value):
            return value
    return None


def parse_ufc_text_date(value: Any) -> str | None:
    if not present(value):
        return None
    for fmt in ("%b. %d, %Y", "%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(str(value).strip(), fmt).date().isoformat()
        except ValueError:
            continue
    return None


def cito_candidate(profile: dict[str, Any]) -> dict[str, Any]:
    stats = profile.get("stats") or {}
    availability = stats.get("dataAvailability") or {}
    record = profile.get("record") or {}
    bio = raw_bio(profile)
    hero = ((profile.get("raw") or {}).get("heroStats") or {})
    photo = (
        profile.get("proxiedImageUrl")
        or profile.get("headshotUrl")
        or profile.get("imageUrl")
    )
    return {
        "first_name": profile.get("firstName"),
        "last_name": profile.get("lastName"),
        "nickname": profile.get("nickname"),
        "date_of_birth": cito_birth_date(profile),
        "height_cm": inches_to_cm(
            profile.get("heightInches") or bio_value(bio, "height")
        ),
        "reach_cm": inches_to_cm(
            profile.get("reachInches") or bio_value(bio, "reach")
        ),
        "stance": profile.get("stance") or bio_value(bio, "stance"),
        "current_weight_kg": lbs_to_kg(
            profile.get("weightLbs") or bio_value(bio, "weight")
        ),
        "slug": profile.get("slug"),
        "cito_slug": profile.get("slug"),
        "cito_status": profile.get("status") or bio_value(bio, "status"),
        "is_active": profile.get("isActive"),
        "current_division": profile.get("division"),
        "place_of_birth": profile.get("placeOfBirth")
        or bio_value(bio, "place of birth"),
        "trains_at": profile.get("trainsAt") or bio_value(bio, "trains at"),
        "fighting_style": profile.get("fightingStyle")
        or bio_value(bio, "fighting style"),
        "leg_reach_cm": inches_to_cm(
            profile.get("legReachInches") or bio_value(bio, "leg reach")
        ),
        "octagon_debut": iso_date(profile.get("octagonDebut"))
        or parse_ufc_text_date(bio_value(bio, "octagon debut")),
        "cito_profile_url": profile.get("profileUrl") or profile.get("sourceUrl"),
        "photo_url": photo,
        "body_image_url": profile.get("bodyImageUrl"),
        "cito_record_wins": profile.get("recordWins", record.get("wins")),
        "cito_record_losses": profile.get("recordLosses", record.get("losses")),
        "cito_record_draws": profile.get("recordDraws", record.get("draws")),
        "cito_record_nc": profile.get("recordNoContest", record.get("noContest")),
        "cito_sig_strikes_landed": as_int(stats.get("significantStrikesLanded")),
        "cito_sig_strikes_attempted": as_int(
            stats.get("significantStrikesAttempted")
        ),
        "cito_striking_accuracy": as_float(stats.get("strikingAccuracy")),
        "cito_sig_strikes_landed_per_min": as_float(
            stats.get("sigStrikesLandedPerMin")
        ),
        "cito_sig_strikes_absorbed_per_min": as_float(
            stats.get("sigStrikesAbsorbedPerMin")
        ),
        "cito_sig_strike_defense": as_float(stats.get("sigStrikeDefense")),
        "cito_takedowns_landed": as_int(stats.get("takedownsLanded")),
        "cito_takedowns_attempted": as_int(stats.get("takedownsAttempted")),
        "cito_takedown_accuracy": as_float(stats.get("takedownAccuracy")),
        "cito_takedown_defense": as_float(stats.get("takedownDefense")),
        "cito_takedown_avg_per_15": as_float(stats.get("takedownAvgPer15Min")),
        "cito_submission_avg_per_15": as_float(
            stats.get("submissionAvgPer15Min")
        ),
        "cito_knockdown_avg": as_float(stats.get("knockdownAvg")),
        "cito_average_fight_time_seconds": as_int(
            stats.get("averageFightTimeSeconds")
        ),
        "cito_strikes_head_pct": as_float(
            nested_value(stats, "sigStrikesByTarget", "head", "percent")
        ),
        "cito_strikes_body_pct": as_float(
            nested_value(stats, "sigStrikesByTarget", "body", "percent")
        ),
        "cito_strikes_leg_pct": as_float(
            nested_value(stats, "sigStrikesByTarget", "leg", "percent")
        ),
        "cito_strikes_standing_pct": as_float(
            nested_value(stats, "sigStrikesByPosition", "standing", "percent")
        ),
        "cito_strikes_clinch_pct": as_float(
            nested_value(stats, "sigStrikesByPosition", "clinch", "percent")
        ),
        "cito_strikes_ground_pct": as_float(
            nested_value(stats, "sigStrikesByPosition", "ground", "percent")
        ),
        "cito_wins_ko_tko": as_int(
            nested_value(stats, "winsByMethod", "ko-tko", "count")
            or hero.get("wins by knockout")
        ),
        "cito_wins_submission": as_int(
            nested_value(stats, "winsByMethod", "sub", "count")
            or hero.get("wins by submission")
        ),
        "cito_wins_decision": as_int(
            nested_value(stats, "winsByMethod", "dec", "count")
        ),
        "cito_wins_ko_tko_pct": as_float(
            nested_value(stats, "winsByMethod", "ko-tko", "percent")
        ),
        "cito_wins_submission_pct": as_float(
            nested_value(stats, "winsByMethod", "sub", "percent")
        ),
        "cito_wins_decision_pct": as_float(
            nested_value(stats, "winsByMethod", "dec", "percent")
        ),
        "cito_stats_source": stats.get("source"),
        "cito_stats_freshness": availability.get("dataFreshness")
        or profile.get("dataFreshness"),
        "cito_stats_synced_at": stats.get("lastSyncedAt")
        or profile.get("lastSyncedAt"),
    }


def cito_resolution_row(
    profile: dict[str, Any],
    now: str,
    status: str,
    reason: str,
    fightiq_id: str | None = None,
    ufcstats_id: str | None = None,
) -> dict[str, Any]:
    candidate = cito_candidate(profile)
    return {
        "cito_id": profile.get("id"),
        "name": profile.get("name"),
        "first_name": profile.get("firstName"),
        "last_name": profile.get("lastName"),
        "nickname": profile.get("nickname"),
        "slug": profile.get("slug"),
        "division": profile.get("division"),
        "status": profile.get("status"),
        "is_active": profile.get("isActive"),
        "record_text": profile.get("recordText"),
        "place_of_birth": candidate.get("place_of_birth"),
        "height_inches": as_float(profile.get("heightInches")),
        "weight_lbs": as_float(profile.get("weightLbs")),
        "reach_inches": as_float(profile.get("reachInches")),
        "stance": profile.get("stance"),
        "birth_date": cito_birth_date(profile),
        "photo_url": profile.get("proxiedImageUrl"),
        "profile_url": profile.get("profileUrl"),
        "stats_available": bool(profile.get("stats")),
        "raw_json": profile,
        "last_seen_at": now,
        "resolution_status": status,
        "resolution_reason": reason,
        "matched_fightiq_id": fightiq_id,
        "matched_ufcstats_id": ufcstats_id,
        "resolved_at": now,
        "review_status": "pending" if status == "quarantined" else "applied",
    }


def parse_ufc_date(value: Any) -> str | None:
    value = clean(value)
    if not value:
        return None
    try:
        return datetime.strptime(value, "%b %d, %Y").date().isoformat()
    except ValueError:
        return None


def ufc_height_to_cm(value: Any) -> float | None:
    value = clean(value)
    if not value:
        return None
    match = re.match(r"^(\d+)'\s*(\d+)\"$", value)
    if not match:
        return None
    feet, inches = map(int, match.groups())
    return round((feet * 12 + inches) * 2.54, 2)


def ufc_inches_to_cm(value: Any) -> float | None:
    value = clean(value)
    if not value:
        return None
    match = re.search(r"([\d.]+)", value)
    return round(float(match.group(1)) * 2.54, 2) if match else None


def ufc_lbs_to_kg(value: Any) -> float | None:
    value = clean(value)
    if not value:
        return None
    match = re.search(r"([\d.]+)", value)
    return round(float(match.group(1)) * 0.45359237, 2) if match else None


def extract_ufcstats_id(url: Any) -> str | None:
    url = clean(url)
    if not url:
        return None
    match = re.search(r"/fighter-details/([A-Za-z0-9]+)", url)
    return match.group(1) if match else None


def download_csv(url: str) -> list[dict[str, str]]:
    request = Request(url, headers={"User-Agent": "FightIQ-unified-sync/1.0"})
    with urlopen(request, timeout=90) as response:
        text = response.read().decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def build_ufc_records(
    details_rows: list[dict[str, str]],
    tott_rows: list[dict[str, str]],
) -> list[dict[str, Any]]:
    details_by_id = {}
    for row in details_rows:
        source_id = extract_ufcstats_id(row.get("URL"))
        if source_id:
            details_by_id[source_id] = row

    records = []
    for row in tott_rows:
        profile_url = clean(row.get("URL"))
        source_id = extract_ufcstats_id(profile_url)
        if not source_id:
            continue
        details = details_by_id.get(source_id, {})
        first_name = clean(details.get("FIRST"))
        last_name = clean(details.get("LAST"))
        display_name = clean(row.get("FIGHTER")) or " ".join(
            part for part in (first_name, last_name) if part
        ).strip()
        if not display_name:
            continue
        records.append(
            {
                "ufcstats_id": source_id,
                "first_name": first_name,
                "last_name": last_name,
                "display_name": display_name,
                "nickname": clean(details.get("NICKNAME")),
                "date_of_birth": parse_ufc_date(row.get("DOB")),
                "height_cm": ufc_height_to_cm(row.get("HEIGHT")),
                "reach_cm": ufc_inches_to_cm(row.get("REACH")),
                "stance": clean(row.get("STANCE")),
                "current_weight_kg": ufc_lbs_to_kg(row.get("WEIGHT")),
                "ufc_profile_url": profile_url,
            }
        )
    return records


def normalize_history_date(value: Any) -> str | None:
    """Normalise les dates UFCStats/Cito sans inventer de fuseau horaire."""
    value = clean(value)
    if not value:
        return None
    iso = iso_date(value)
    if iso and re.fullmatch(r"\d{4}-\d{2}-\d{2}", iso):
        return iso
    for fmt in (
        "%B %d, %Y",
        "%b %d, %Y",
        "%b. %d, %Y",
        "%m/%d/%Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def normalize_bout_id(value: Any) -> str | None:
    """Retourne l'identifiant stable d'un combat quand la source le fournit."""
    value = clean(value)
    if not value:
        return None
    text = value.strip()
    url_match = re.search(r"/fight-details/([A-Za-z0-9_-]+)", text)
    if url_match:
        text = url_match.group(1)
    text = re.sub(r"^ufc[-_:]", "", text, flags=re.IGNORECASE)
    return text.lower() if re.fullmatch(r"[A-Za-z0-9_-]{6,}", text) else None


def nested_first(row: dict[str, Any], *paths: str) -> Any:
    for path in paths:
        current: Any = row
        for key in path.split("."):
            if not isinstance(current, dict) or key not in current:
                current = None
                break
            current = current[key]
        if present(current):
            return current
    return None


def entity_name(value: Any) -> str | None:
    if isinstance(value, str):
        return clean(value)
    if not isinstance(value, dict):
        return None
    return clean(
        nested_first(
            value,
            "name",
            "displayName",
            "fighterName",
            "fullName",
            "title",
            "slug",
        )
    )


def split_bout_names(value: Any) -> list[str]:
    value = clean(value)
    if not value:
        return []
    parts = re.split(r"\s+vs\.?\s+", value, maxsplit=1, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()] if len(parts) == 2 else []


def normalize_outcome(value: Any) -> str | None:
    value = normalize_name(value)
    if not value:
        return None
    if value in {"w", "win", "winner", "victory", "won"}:
        return "W"
    if value in {"l", "loss", "loser", "defeat", "lost"}:
        return "L"
    if value in {"d", "draw"}:
        return "D"
    if value in {"nc", "no contest", "no decision"}:
        return "NC"
    return None


def normalize_method(value: Any) -> str | None:
    value = normalize_name(value)
    if not value:
        return None
    value = value.replace("technical knockout", "ko tko")
    value = value.replace("knockout", "ko tko")
    return value


def extract_history_rows(payload: Any) -> list[dict[str, Any]]:
    """Accepte les enveloppes Cito actuelles et leurs variantes usuelles."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("fights", "fightHistory", "fight_history", "bouts", "history", "results", "data"):
        child = payload.get(key)
        rows = extract_history_rows(child)
        if rows:
            return rows
    row_markers = {
        "boutId",
        "fightId",
        "opponent",
        "opponentName",
        "eventName",
        "result",
    }
    return [payload] if row_markers & set(payload) else []


def embedded_cito_history(profile: dict[str, Any]) -> Any:
    for container in (profile, profile.get("raw") or {}):
        if not isinstance(container, dict):
            continue
        for key in ("fights", "fightHistory", "fight_history", "bouts"):
            if extract_history_rows(container.get(key)):
                return container.get(key)
    return None


def normalize_cito_fight_history(
    payload: Any,
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    """Réduit l'historique Cito aux preuves utiles au rapprochement d'identité."""
    wanted_names = profile_names(profile)
    normalized: list[dict[str, Any]] = []
    for row in extract_history_rows(payload):
        bout_id = normalize_bout_id(
            nested_first(
                row,
                "boutId",
                "bout_id",
                "fightId",
                "fight_id",
                "fightMetricId",
                "dataId",
                "url",
                "bout.id",
                "bout.dataId",
                "bout.url",
            )
        )
        event_value = nested_first(
            row,
            "eventName",
            "eventTitle",
            "event.name",
            "event.title",
            "event.slug",
            "event",
        )
        event_name = entity_name(event_value)
        event_id = normalize_bout_id(
            nested_first(
                row,
                "eventId",
                "eventDataId",
                "event.id",
                "event.dataId",
            )
        )
        event_date = normalize_history_date(
            nested_first(
                row,
                "eventDate",
                "fightDate",
                "boutDate",
                "date",
                "event.date",
                "event.startDate",
            )
        )

        opponent = entity_name(
            nested_first(
                row,
                "opponentName",
                "opponent",
                "opponent.name",
                "opponent.displayName",
            )
        )
        participants: list[str] = []
        for key in (
            "fighters",
            "participants",
            "competitors",
        ):
            values = row.get(key)
            if isinstance(values, list):
                participants.extend(
                    name for name in (entity_name(item) for item in values) if name
                )
        for key in (
            "fighter1",
            "fighter2",
            "redFighter",
            "blueFighter",
            "fighterA",
            "fighterB",
        ):
            name = entity_name(row.get(key))
            if name:
                participants.append(name)
        participants.extend(
            split_bout_names(
                nested_first(row, "bout", "boutName", "matchup", "title")
            )
        )
        if not opponent and len(participants) >= 2:
            scored_names = [
                (
                    max(
                        (
                            SequenceMatcher(
                                None, normalize_name(name), wanted
                            ).ratio()
                            for wanted in wanted_names
                        ),
                        default=0.0,
                    ),
                    name,
                )
                for name in participants
            ]
            scored_names.sort(reverse=True)
            if scored_names and scored_names[0][0] >= 0.60:
                chosen = scored_names[0][1]
                opponent = next(
                    (
                        name
                        for name in participants
                        if normalize_name(name) != normalize_name(chosen)
                    ),
                    None,
                )

        outcome = normalize_outcome(
            nested_first(
                row,
                "fighterResult",
                "result",
                "outcome",
                "resultCode",
            )
        )
        winner = entity_name(
            nested_first(row, "winnerName", "winner", "winner.name")
        )
        if not outcome and winner and wanted_names:
            outcome = (
                "W"
                if normalize_name(winner) in wanted_names
                else "L"
            )
        evidence = {
            "bout_id": bout_id,
            "event_id": event_id,
            "event_name": normalize_name(event_name),
            "event_date": event_date,
            "opponent": normalize_name(opponent),
            "outcome": outcome,
            "method": normalize_method(
                nested_first(row, "method", "resultMethod", "finish.method")
            ),
        }
        if evidence["bout_id"] or (
            evidence["opponent"]
            and (evidence["event_id"] or evidence["event_name"] or evidence["event_date"])
        ):
            normalized.append(evidence)
    return normalized


def build_ufc_fight_histories(
    fight_rows: list[dict[str, str]],
    event_rows: list[dict[str, str]],
    fighter_records: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """Indexe les résultats UFCStats par ID combattant sans deviner les homonymes.

    Les CSV de résultats identifient les participants par nom. Lorsqu'un nom
    UFCStats correspond à plusieurs IDs, son historique n'est attribué à aucun
    des homonymes : il restera une preuve à examiner manuellement.
    """
    event_dates = {
        normalize_name(row.get("EVENT")): normalize_history_date(row.get("DATE"))
        for row in event_rows
        if normalize_name(row.get("EVENT"))
    }
    ids_by_name: dict[str, set[str]] = defaultdict(set)
    for fighter in fighter_records:
        source_id = normalize_id(fighter.get("ufcstats_id"))
        display_name = normalize_name(fighter.get("display_name"))
        if source_id and display_name:
            ids_by_name[display_name].add(source_id)
    unique_id_by_name = {
        name: next(iter(source_ids))
        for name, source_ids in ids_by_name.items()
        if len(source_ids) == 1
    }

    histories: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in fight_rows:
        participants = split_bout_names(row.get("BOUT"))
        if len(participants) != 2:
            continue
        outcomes = [part.strip() for part in str(row.get("OUTCOME") or "").split("/")]
        event_name = normalize_name(row.get("EVENT"))
        bout_id = normalize_bout_id(row.get("URL"))
        for index, participant in enumerate(participants):
            source_id = unique_id_by_name.get(normalize_name(participant))
            if not source_id:
                continue
            histories[source_id].append(
                {
                    "bout_id": bout_id,
                    "event_id": None,
                    "event_name": event_name,
                    "event_date": event_dates.get(event_name),
                    "opponent": normalize_name(participants[1 - index]),
                    "outcome": normalize_outcome(
                        outcomes[index] if index < len(outcomes) else None
                    ),
                    "method": normalize_method(row.get("METHOD")),
                }
            )
    return dict(histories)


@dataclass
class FightHistoryFetch:
    bouts: list[dict[str, Any]] = field(default_factory=list)
    source: str = "unavailable"
    error: str | None = None


def fetch_cito_fight_history(
    profile: dict[str, Any], api_key: str
) -> FightHistoryFetch:
    embedded = embedded_cito_history(profile)
    if embedded is not None:
        return FightHistoryFetch(
            bouts=normalize_cito_fight_history(embedded, profile),
            source="cito_embedded",
        )
    slug = clean(profile.get("slug"))
    if not slug:
        return FightHistoryFetch(error="cito_profile_without_slug")
    url = CITO_FIGHT_HISTORY_URL.format(slug=quote(slug, safe=""))
    try:
        payload = fetch_json(url, api_key, timeout=60)
    except (HTTPError, URLError, TimeoutError, RuntimeError, ValueError) as exc:
        return FightHistoryFetch(
            source="cito_fights_endpoint",
            error=f"{type(exc).__name__}:{exc}",
        )
    return FightHistoryFetch(
        bouts=normalize_cito_fight_history(payload, profile),
        source="cito_fights_endpoint",
    )


def history_record(rows: list[dict[str, Any]]) -> Counter[str]:
    return Counter(
        row["outcome"]
        for row in rows
        if row.get("outcome") in {"W", "L", "D", "NC"}
    )


def fight_history_compatibility(
    cito_rows: list[dict[str, Any]],
    ufc_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Compare IDs de combat puis adversaire + événement/date.

    Deux palmarès identiques sans combats communs ne suffisent jamais. Les
    résultats servent uniquement à confirmer des combats déjà identifiés.
    """
    matches: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    used: set[int] = set()
    conflicts: list[str] = []

    for cito_bout in cito_rows:
        candidates: list[tuple[int, int, str]] = []
        for index, ufc_bout in enumerate(ufc_rows):
            if index in used:
                continue
            direct = (
                cito_bout.get("bout_id")
                and ufc_bout.get("bout_id")
                and cito_bout["bout_id"] == ufc_bout["bout_id"]
            )
            opponent_same = (
                cito_bout.get("opponent")
                and cito_bout.get("opponent") == ufc_bout.get("opponent")
            )
            event_same = bool(
                (
                    cito_bout.get("event_id")
                    and cito_bout.get("event_id") == ufc_bout.get("event_id")
                )
                or (
                    cito_bout.get("event_name")
                    and cito_bout.get("event_name") == ufc_bout.get("event_name")
                )
            )
            date_same = bool(
                cito_bout.get("event_date")
                and cito_bout.get("event_date") == ufc_bout.get("event_date")
            )
            if direct:
                candidates.append((3, index, "bout_id"))
            elif opponent_same and event_same:
                candidates.append((2, index, "opponent_event"))
            elif opponent_same and date_same:
                candidates.append((1, index, "opponent_date"))
        if not candidates:
            continue
        _, index, kind = max(candidates)
        used.add(index)
        ufc_bout = ufc_rows[index]
        if (
            cito_bout.get("opponent")
            and ufc_bout.get("opponent")
            and cito_bout["opponent"] != ufc_bout["opponent"]
        ):
            conflicts.append(f"opponent_conflict:{cito_bout.get('bout_id')}")
        if (
            cito_bout.get("outcome")
            and ufc_bout.get("outcome")
            and cito_bout["outcome"] != ufc_bout["outcome"]
        ):
            conflicts.append(f"outcome_conflict:{cito_bout.get('bout_id')}")
        matches.append((cito_bout, ufc_bout, kind))

    # Même ID de combat ou même événement/date avec un autre adversaire :
    # contradiction forte, même si aucune paire n'a été retenue ci-dessus.
    for cito_bout in cito_rows:
        for ufc_bout in ufc_rows:
            same_bout = (
                cito_bout.get("bout_id")
                and cito_bout.get("bout_id") == ufc_bout.get("bout_id")
            )
            same_slot = bool(
                cito_bout.get("event_date")
                and cito_bout.get("event_date") == ufc_bout.get("event_date")
                and cito_bout.get("event_name")
                and cito_bout.get("event_name") == ufc_bout.get("event_name")
            )
            if (same_bout or same_slot) and (
                cito_bout.get("opponent")
                and ufc_bout.get("opponent")
                and cito_bout["opponent"] != ufc_bout["opponent"]
            ):
                conflicts.append(
                    "same_bout_or_event_different_opponent:"
                    f"{cito_bout.get('bout_id') or cito_bout.get('event_name')}"
                )

    outcome_matches = sum(
        1
        for cito_bout, ufc_bout, _ in matches
        if cito_bout.get("outcome")
        and cito_bout.get("outcome") == ufc_bout.get("outcome")
    )
    direct_ids = sum(1 for _, _, kind in matches if kind == "bout_id")
    signature_matches = len(matches) - direct_ids
    cito_record = history_record(cito_rows)
    ufc_record = history_record(ufc_rows)
    if (
        len(cito_rows) == len(ufc_rows)
        and cito_record
        and cito_record == ufc_record
    ):
        record_relation = "exact"
    elif matches and not conflicts:
        record_relation = "compatible_subset"
    else:
        record_relation = "unknown"
    return {
        "matched_bouts": len(matches),
        "direct_bout_ids": direct_ids,
        "signature_matches": signature_matches,
        "outcome_matches": outcome_matches,
        "record_relation": record_relation,
        "cito_bouts": len(cito_rows),
        "ufcstats_bouts": len(ufc_rows),
        "conflicts": sorted(set(conflicts)),
    }


def profile_record(profile: dict[str, Any]) -> tuple[int, int, int, int] | None:
    record = profile.get("record") or {}
    values = (
        as_int(profile.get("recordWins", record.get("wins"))),
        as_int(profile.get("recordLosses", record.get("losses"))),
        as_int(profile.get("recordDraws", record.get("draws"))),
        as_int(profile.get("recordNoContest", record.get("noContest"))),
    )
    if values[0] is not None and values[1] is not None:
        return tuple(0 if value is None else value for value in values)  # type: ignore[return-value]
    record_text = clean(profile.get("recordText"))
    match = re.search(r"(\d+)\s*-\s*(\d+)(?:\s*-\s*(\d+))?", record_text or "")
    if not match:
        return None
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0), 0)


def fighter_record(fighter: dict[str, Any]) -> tuple[int, int, int, int] | None:
    wins = as_int(fighter.get("cito_record_wins"))
    losses = as_int(fighter.get("cito_record_losses"))
    if wins is None or losses is None:
        return None
    return (
        wins,
        losses,
        as_int(fighter.get("cito_record_draws")) or 0,
        as_int(fighter.get("cito_record_nc")) or 0,
    )


def best_name_similarity(
    profile: dict[str, Any], fighter_values: set[str]
) -> float:
    return max(
        (
            SequenceMatcher(None, wanted, known).ratio()
            for wanted in profile_names(profile)
            for known in fighter_values
        ),
        default=0.0,
    )


def fetch_json(url: str, api_key: str, timeout: int = 120) -> Any:
    request = Request(
        url,
        headers={
            "x-api-key": api_key,
            "User-Agent": "FightIQ-unified-sync/1.0",
            "Accept": "application/json",
        },
    )
    with urlopen(request, timeout=timeout) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        raise RuntimeError(f"Unexpected source API payload for {url}")
    if payload.get("success") is False:
        raise RuntimeError(f"Source API returned success=false for {url}")
    return payload.get("data", payload) or []


def fetch_all(sb: Any, table: str, fields: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        batch = (
            sb.table(table)
            .select(fields)
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        ).data or []
        rows.extend(batch)
        if len(batch) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


@dataclass
class ResolutionRegistry:
    links: dict[str, dict[str, Any]] = field(default_factory=dict)
    creates: dict[str, dict[str, Any]] = field(default_factory=dict)
    exclusions: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_resolution_registry(paths: str | None) -> ResolutionRegistry:
    registry = ResolutionRegistry()
    if not paths:
        return registry

    def clear_previous(cito_id: str) -> None:
        registry.links.pop(cito_id, None)
        registry.creates.pop(cito_id, None)
        registry.exclusions.pop(cito_id, None)

    for raw_path in str(paths).split(","):
        file_path = Path(raw_path.strip())
        if not raw_path.strip():
            continue
        if not file_path.exists():
            print(
                f"Resolution registry absent: {file_path}; "
                "continuing with other registries and DB mappings"
            )
            continue
        payload = json.loads(file_path.read_text(encoding="utf-8"))
        for item in payload.get("links") or []:
            if item.get("cito_id"):
                cito_id = str(item["cito_id"])
                clear_previous(cito_id)
                registry.links[cito_id] = item
        for item in payload.get("creates") or []:
            for cito_id in item.get("cito_ids") or [
                item.get("canonical_cito_id")
            ]:
                if cito_id:
                    cito_id = str(cito_id)
                    clear_previous(cito_id)
                    registry.creates[cito_id] = item
        for item in payload.get("exclusions") or []:
            if item.get("cito_id"):
                cito_id = str(item["cito_id"])
                clear_previous(cito_id)
                registry.exclusions[cito_id] = item
    return registry


@dataclass
class PlannedState:
    fighters: dict[str, dict[str, Any]]
    sources: dict[tuple[str, str], dict[str, Any]]
    resolutions: dict[str, dict[str, Any]]
    original_fighters: dict[str, dict[str, Any]]
    original_sources: dict[tuple[str, str], dict[str, Any]]
    original_resolutions: dict[str, dict[str, Any]]
    now: str

    @classmethod
    def from_rows(
        cls,
        fighters: list[dict[str, Any]],
        sources: list[dict[str, Any]],
        resolutions: list[dict[str, Any]],
        now: str,
    ) -> "PlannedState":
        by_fiq = {
            row["fightiq_id"]: deepcopy(row)
            for row in fighters
            if row.get("fightiq_id")
        }
        by_source = {
            (str(row.get("source")), str(row.get("source_id"))): deepcopy(row)
            for row in sources
            if row.get("source") and row.get("source_id")
        }
        by_cito = {
            str(row["cito_id"]): deepcopy(row)
            for row in resolutions
            if row.get("cito_id")
        }
        return cls(
            fighters=by_fiq,
            sources=by_source,
            resolutions=by_cito,
            original_fighters=deepcopy(by_fiq),
            original_sources=deepcopy(by_source),
            original_resolutions=deepcopy(by_cito),
            now=now,
        )

    def find_by_ufcstats(self, source_id: str | None) -> dict[str, Any] | None:
        if not source_id:
            return None
        mapping = self.sources.get(("ufcstats", source_id))
        if mapping:
            return self.fighters.get(mapping.get("fightiq_id"))
        for fighter in self.fighters.values():
            if fighter.get("ufcstats_id") == source_id:
                return fighter
        return None

    def find_by_cito(self, source_id: str | None) -> dict[str, Any] | None:
        if not source_id:
            return None
        mapping = self.sources.get(("cito", source_id))
        if mapping:
            return self.fighters.get(mapping.get("fightiq_id"))
        for fighter in self.fighters.values():
            if fighter.get("cito_id") == source_id:
                return fighter
        return None

    def add_fighter(self, record: dict[str, Any]) -> dict[str, Any]:
        fightiq_id = record["fightiq_id"]
        if fightiq_id in self.fighters:
            raise RuntimeError(f"Duplicate planned fightiq_id: {fightiq_id}")
        self.fighters[fightiq_id] = deepcopy(record)
        return self.fighters[fightiq_id]

    def ensure_source(
        self,
        fighter: dict[str, Any],
        source: str,
        source_id: str | None,
        source_name: str | None,
        is_primary: bool,
    ) -> None:
        if not source_id:
            return
        key = (source, source_id)
        existing = self.sources.get(key)
        if existing and existing.get("fightiq_id") != fighter.get("fightiq_id"):
            raise RuntimeError(
                f"Source identity conflict: {source}:{source_id} already maps to "
                f"{existing.get('fightiq_id')}, attempted {fighter.get('fightiq_id')}"
            )
        desired = {
            "fightiq_id": fighter["fightiq_id"],
            "source": source,
            "source_id": source_id,
            "source_name": source_name,
            "is_primary": bool(is_primary),
            "updated_at": self.now,
        }
        if existing:
            unchanged = all(
                existing.get(key_name) == desired.get(key_name)
                for key_name in ("fightiq_id", "source_name", "is_primary")
            )
            if unchanged:
                return
        self.sources[key] = desired

    def set_resolution(self, row: dict[str, Any]) -> None:
        cito_id = str(row["cito_id"])
        current = self.resolutions.get(cito_id)
        if current:
            for field_name in REVIEW_FIELDS:
                if field_name not in row and field_name in current:
                    row[field_name] = current.get(field_name)
            comparable = dict(row)
            comparable.pop("last_seen_at", None)
            comparable.pop("resolved_at", None)
            old_comparable = {key: current.get(key) for key in comparable}
            if old_comparable == comparable:
                return
        self.resolutions[cito_id] = row


def meaningful_diff(current: dict[str, Any], desired: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in desired.items()
        if key not in {"id", "fightiq_id"} and current.get(key) != value
    }


def backfill_existing_source_mappings(state: PlannedState) -> None:
    """Garantit que chaque ID primaire déjà stocké possède sa ligne de source."""
    for fighter in state.fighters.values():
        source_name = fighter.get("display_name")
        state.ensure_source(
            fighter,
            "ufcstats",
            normalize_id(fighter.get("ufcstats_id")),
            source_name,
            True,
        )
        state.ensure_source(
            fighter,
            "cito",
            normalize_id(fighter.get("cito_id")),
            source_name,
            True,
        )


def apply_ufcstats_plan(state: PlannedState, records: list[dict[str, Any]]) -> None:
    for source in records:
        source_id = source["ufcstats_id"]
        fighter = state.find_by_ufcstats(source_id)
        if not fighter:
            fighter = state.add_fighter(
                {
                    "fightiq_id": f"fiq_{source_id}",
                    **{key: value for key, value in source.items() if value is not None},
                    "source_updated_at": state.now,
                    "fightiq_updated_at": state.now,
                }
            )
        else:
            changes = {
                key: value
                for key, value in source.items()
                if value is not None and fighter.get(key) != value
            }
            if changes:
                fighter.update(changes)
                fighter["source_updated_at"] = state.now
                fighter["fightiq_updated_at"] = state.now
        if not fighter.get("ufcstats_id"):
            fighter["ufcstats_id"] = source_id
            fighter["fightiq_updated_at"] = state.now
        state.ensure_source(
            fighter,
            "ufcstats",
            source_id,
            source.get("display_name"),
            True,
        )


def fighter_name_sets(state: PlannedState) -> dict[str, set[str]]:
    """Construit une fois les noms/alias connus pour éviter un scan quadratique."""
    result: dict[str, set[str]] = {}
    for fighter in state.fighters.values():
        result[fighter["fightiq_id"]] = identity_name_variants(
            fighter.get("display_name"),
            fighter.get("first_name"),
            fighter.get("last_name"),
            fighter.get("nickname"),
        )
    for source in state.sources.values():
        fightiq_id = source.get("fightiq_id")
        if fightiq_id in result:
            result[fightiq_id].add(normalize_name(source.get("source_name")))
    for values in result.values():
        values.discard("")
    return result


def identity_index_fingerprint(
    state: PlannedState,
    ufc_histories: dict[str, list[dict[str, Any]]] | None = None,
) -> str:
    """Signature des identités pouvant modifier une décision de matching."""
    fighters = [
        {
            "fightiq_id": fighter.get("fightiq_id"),
            "ufcstats_id": fighter.get("ufcstats_id"),
            "cito_id": fighter.get("cito_id"),
            "display_name": normalize_name(fighter.get("display_name")),
            "first_name": normalize_name(fighter.get("first_name")),
            "last_name": normalize_name(fighter.get("last_name")),
            "nickname": normalize_name(fighter.get("nickname")),
            "date_of_birth": iso_date(fighter.get("date_of_birth")),
            "slug": normalize_name(fighter.get("slug")),
        }
        for fighter in sorted(
            state.fighters.values(),
            key=lambda item: str(item.get("fightiq_id")),
        )
    ]
    sources = [
        {
            "source": source.get("source"),
            "source_id": source.get("source_id"),
            "fightiq_id": source.get("fightiq_id"),
            "source_name": normalize_name(source.get("source_name")),
        }
        for source in sorted(
            state.sources.values(),
            key=lambda item: (
                str(item.get("source")),
                str(item.get("source_id")),
            ),
        )
    ]
    history_digest = None
    if ufc_histories is not None:
        history_digest = stable_fingerprint(
            {
                source_id: rows
                for source_id, rows in sorted(ufc_histories.items())
            }
        )
    return stable_fingerprint(
        {
            "fighters": fighters,
            "sources": sources,
            "ufc_history": history_digest,
        }
    )


def quarantine_metadata(row: dict[str, Any] | None) -> dict[str, Any]:
    raw = (row or {}).get("raw_json")
    if not isinstance(raw, dict):
        return {}
    metadata = raw.get("_fightiq_quarantine")
    return metadata if isinstance(metadata, dict) else {}


def quarantine_profile(
    state: PlannedState,
    profile: dict[str, Any],
    reason: str,
    identity_fingerprint: str,
    candidates: list[dict[str, Any]] | None = None,
    history_fetch: FightHistoryFetch | None = None,
) -> dict[str, Any]:
    """Conserve un profil douteux hors de la base canonique sans bloquer le run."""
    raw_json = deepcopy(profile)
    raw_json["_fightiq_quarantine"] = {
        "reason": reason,
        "required_decision": "link | create | exclude",
        "identity_index_fingerprint": identity_fingerprint,
        "candidates": candidates or [],
        "fight_history": {
            "source": history_fetch.source,
            "bouts": len(history_fetch.bouts),
            "error": history_fetch.error,
        }
        if history_fetch
        else None,
    }
    row = cito_resolution_row(
        profile,
        state.now,
        "quarantined",
        reason,
    )
    row["raw_json"] = raw_json
    row["review_status"] = "pending"
    state.set_resolution(row)
    return {
        "cito_id": profile.get("id"),
        "name": profile.get("name"),
        "reason": reason,
        "birth_date": cito_birth_date(profile),
        "record": profile.get("recordText"),
        "candidates": candidates or [],
        "required_decision": "link | create | exclude",
        "fight_history": raw_json["_fightiq_quarantine"].get("fight_history"),
    }


def fighter_names(
    fighter: dict[str, Any],
    state: PlannedState,
    cached: dict[str, set[str]] | None = None,
) -> set[str]:
    if cached is not None:
        return cached.get(fighter["fightiq_id"], set())
    values = identity_name_variants(
        fighter.get("display_name"),
        fighter.get("first_name"),
        fighter.get("last_name"),
        fighter.get("nickname"),
    )
    for source in state.sources.values():
        if source.get("fightiq_id") == fighter.get("fightiq_id"):
            values.add(normalize_name(source.get("source_name")))
    values.discard("")
    return values


def profile_names(profile: dict[str, Any]) -> set[str]:
    return identity_name_variants(
        profile.get("name"),
        profile.get("firstName"),
        profile.get("lastName"),
        profile.get("nickname"),
    )


def close_number(a: Any, b: Any, tolerance: float) -> bool:
    left, right = as_float(a), as_float(b)
    return (
        left is not None
        and right is not None
        and abs(left - right) <= tolerance
    )


def number_delta(a: Any, b: Any) -> float | None:
    left, right = as_float(a), as_float(b)
    if left is None or right is None:
        return None
    return abs(left - right)


def place_components(value: Any) -> set[str]:
    if not present(value):
        return set()
    components = {
        normalize_name(part)
        for part in re.split(r"[,;/|()\[\]]+", str(value))
    }
    components.discard("")
    return components


def place_compatibility(source_place: Any, canonical_place: Any) -> str:
    """Compare deux lieux sans faire d'un pays seul une preuve décisive."""
    source = normalize_name(source_place)
    canonical = normalize_name(canonical_place)
    if not source or not canonical:
        return "missing"
    if source == canonical:
        return "broad_only" if source in BROAD_PLACE_NAMES else "exact_specific"

    source_components = place_components(source_place)
    canonical_components = place_components(canonical_place)
    shared_components = (
        source_components & canonical_components
    ) - BROAD_PLACE_NAMES
    if shared_components:
        return "shared_specific_component"

    broad_tokens = {
        token
        for place in BROAD_PLACE_NAMES
        for token in place.split()
    }
    ignored_tokens = broad_tokens | {
        "city",
        "state",
        "province",
        "republic",
        "the",
        "of",
    }
    source_tokens = set(source.split()) - ignored_tokens
    canonical_tokens = set(canonical.split()) - ignored_tokens
    if any(len(token) >= 4 for token in source_tokens & canonical_tokens):
        return "shared_specific_component"

    if (
        source in BROAD_PLACE_NAMES
        and canonical in BROAD_PLACE_NAMES
        and source_tokens == canonical_tokens
    ):
        return "broad_only"
    return "incompatible"


def full_iso_date(value: Any) -> bool:
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(value or "")))


def biographical_compatibility(
    profile: dict[str, Any], fighter: dict[str, Any]
) -> dict[str, Any]:
    """Retourne les preuves indépendantes des noms et de l'historique."""
    candidate = cito_candidate(profile)
    source_dob = cito_birth_date(profile)
    canonical_dob = iso_date(fighter.get("date_of_birth"))
    dob_relation = "missing"
    if source_dob and canonical_dob:
        if source_dob == canonical_dob:
            dob_relation = (
                "exact" if full_iso_date(source_dob) else "partial_exact"
            )
        elif full_iso_date(source_dob) and full_iso_date(canonical_dob):
            dob_relation = "conflict"
        else:
            try:
                year_delta = abs(int(source_dob[:4]) - int(canonical_dob[:4]))
            except (TypeError, ValueError):
                year_delta = 99
            dob_relation = "year_near" if year_delta <= 1 else "conflict"

    source_place = candidate.get("place_of_birth")
    canonical_place = fighter.get("place_of_birth")
    place_relation = place_compatibility(source_place, canonical_place)

    measures = {
        "height": (
            candidate.get("height_cm"),
            fighter.get("height_cm"),
            2.5,
            7.5,
        ),
        "reach": (
            candidate.get("reach_cm"),
            fighter.get("reach_cm"),
            3.0,
            10.0,
        ),
        "weight": (
            candidate.get("current_weight_kg"),
            fighter.get("current_weight_kg"),
            3.0,
            None,
        ),
    }
    physical_matches: list[str] = []
    physical_conflicts: list[str] = []
    deltas: dict[str, float] = {}
    for field_name, (source_value, canonical_value, tolerance, conflict_at) in measures.items():
        delta = number_delta(source_value, canonical_value)
        if delta is None:
            continue
        deltas[field_name] = round(delta, 2)
        if delta <= tolerance:
            physical_matches.append(field_name)
        elif conflict_at is not None and delta > conflict_at:
            physical_conflicts.append(field_name)

    independent_signals: list[str] = []
    if dob_relation == "exact":
        independent_signals.append("date_of_birth")
    if place_relation in {"exact_specific", "shared_specific_component"}:
        independent_signals.append("place_of_birth")
    if len(physical_matches) >= 2:
        independent_signals.append("physical_profile")

    return {
        "dob_relation": dob_relation,
        "place_relation": place_relation,
        "physical_matches": physical_matches,
        "physical_conflicts": physical_conflicts,
        "physical_deltas": deltas,
        "independent_signals": independent_signals,
        "hard_conflict": (
            dob_relation == "conflict" or len(physical_conflicts) >= 2
        ),
        "source_values": {
            "date_of_birth": source_dob,
            "place_of_birth": source_place,
            "height_cm": candidate.get("height_cm"),
            "reach_cm": candidate.get("reach_cm"),
            "current_weight_kg": candidate.get("current_weight_kg"),
        },
        "canonical_values": {
            "date_of_birth": canonical_dob,
            "place_of_birth": canonical_place,
            "height_cm": fighter.get("height_cm"),
            "reach_cm": fighter.get("reach_cm"),
            "current_weight_kg": fighter.get("current_weight_kg"),
        },
    }


def decisive_history_match(
    compatibility: dict[str, Any],
    *,
    global_record_exact: bool = False,
    dob_exact: bool = False,
    place_compatible: bool = False,
    independent_bio_evidence: bool = False,
) -> bool:
    matched = int(compatibility.get("matched_bouts") or 0)
    outcomes = int(compatibility.get("outcome_matches") or 0)
    direct = int(compatibility.get("direct_bout_ids") or 0)
    if (
        compatibility.get("conflicts")
        or matched < 2
        or not independent_bio_evidence
    ):
        return False
    palmares_compatible = (
        outcomes >= min(2, matched)
        or compatibility.get("record_relation") == "exact"
        or global_record_exact
    )
    if not palmares_compatible:
        return False
    return (
        matched >= 3
        or direct >= 2
        or (matched >= 2 and (dob_exact or place_compatible))
    )


def score_identity_candidate(
    profile: dict[str, Any],
    fighter: dict[str, Any],
    state: PlannedState,
    cached_names: dict[str, set[str]] | None = None,
    ufc_histories: dict[str, list[dict[str, Any]]] | None = None,
    cito_history: list[dict[str, Any]] | None = None,
) -> tuple[int | None, list[str]]:
    known_names = fighter_names(fighter, state, cached_names)
    names_match = bool(profile_names(profile) & known_names)
    score = 55 if names_match else 0
    reasons = (
        ["exact_name_or_known_alias"]
        if names_match
        else ["different_name_possible_alias"]
    )

    biography = biographical_compatibility(profile, fighter)
    if biography["dob_relation"] == "conflict":
        return None, ["date_of_birth_conflict"]
    if len(biography["physical_conflicts"]) >= 2:
        return None, [
            "physical_profile_conflict",
            *(
                f"physical_conflict_{field_name}"
                for field_name in biography["physical_conflicts"]
            ),
        ]

    dob_exact = biography["dob_relation"] == "exact"
    if dob_exact:
        score += 55
        reasons.extend(["dob_exact", "independent_bio_date_of_birth"])
    elif biography["dob_relation"] in {"partial_exact", "year_near"}:
        score += 15
        reasons.append(f"dob_{biography['dob_relation']}_support_only")

    place_relation = biography["place_relation"]
    if place_relation == "exact_specific":
        score += 35
        reasons.extend(
            ["place_of_birth_exact", "independent_bio_place_of_birth"]
        )
    elif place_relation == "shared_specific_component":
        score += 25
        reasons.extend(
            [
                "place_of_birth_compatible",
                "independent_bio_place_of_birth",
            ]
        )
    elif place_relation == "broad_only":
        score += 5
        reasons.append("place_country_only_support_only")
    elif place_relation == "incompatible":
        reasons.append("place_of_birth_not_confirmed")

    cito_slug = normalize_name(profile.get("slug"))
    fighter_slugs = {
        normalize_name(fighter.get("slug")),
        normalize_name(fighter.get("cito_slug")),
    }
    fighter_slugs.discard("")
    if cito_slug and cito_slug in fighter_slugs:
        score += 45
        reasons.append("slug_exact")

    candidate = cito_candidate(profile)
    if "height" in biography["physical_matches"]:
        score += 10
        reasons.append("height_close")
    if "reach" in biography["physical_matches"]:
        score += 8
        reasons.append("reach_close")
    if "weight" in biography["physical_matches"]:
        score += 5
        reasons.append("weight_close")
    if "physical_profile" in biography["independent_signals"]:
        score += 20
        reasons.append(
            f"independent_bio_physical_{len(biography['physical_matches'])}"
        )
    if (
        normalize_name(candidate.get("stance"))
        and normalize_name(candidate.get("stance"))
        == normalize_name(fighter.get("stance"))
    ):
        score += 3
        reasons.append("stance_exact")
    if (
        normalize_name(profile.get("nickname"))
        and normalize_name(profile.get("nickname"))
        == normalize_name(fighter.get("nickname"))
    ):
        score += 10
        reasons.append("nickname_exact")

    source_id = normalize_id(fighter.get("ufcstats_id"))
    ufc_history = (ufc_histories or {}).get(source_id or "", [])
    compatibility = fight_history_compatibility(
        cito_history or [], ufc_history
    )
    if compatibility["conflicts"]:
        return None, [
            "fight_history_conflict",
            *compatibility["conflicts"][:3],
        ]

    source_record = profile_record(profile)
    canonical_record = fighter_record(fighter)
    global_record_exact = bool(
        source_record and canonical_record and source_record == canonical_record
    )
    if global_record_exact:
        score += 12
        reasons.append("global_record_exact_support_only")
    elif source_record and canonical_record:
        reasons.append("global_record_differs_possible_freshness")

    matched_bouts = compatibility["matched_bouts"]
    if matched_bouts:
        score += min(100, matched_bouts * 25)
        score += min(45, compatibility["direct_bout_ids"] * 15)
        score += min(20, compatibility["outcome_matches"] * 5)
        reasons.extend(
            [
                f"fight_history_matches_{matched_bouts}",
                f"fight_history_direct_ids_{compatibility['direct_bout_ids']}",
                f"fight_history_outcomes_{compatibility['outcome_matches']}",
                f"fight_history_record_{compatibility['record_relation']}",
            ]
        )
    history_decisive = decisive_history_match(
        compatibility,
        global_record_exact=global_record_exact,
        dob_exact=dob_exact,
        place_compatible=(
            "place_of_birth" in biography["independent_signals"]
        ),
        independent_bio_evidence=bool(biography["independent_signals"]),
    )
    if history_decisive:
        reasons.append("fight_history_decisive")
    elif matched_bouts >= 2 and not biography["independent_signals"]:
        reasons.append("fight_history_without_independent_bio")

    if not names_match and not history_decisive:
        return None, [
            "no_exact_name_or_known_alias",
            f"name_similarity_{best_name_similarity(profile, known_names):.2f}",
            *reasons[1:],
        ]
    return score, reasons


def automatic_identity_match(
    profile: dict[str, Any],
    state: PlannedState,
    ufc_histories: dict[str, list[dict[str, Any]]] | None = None,
    cito_history: list[dict[str, Any]] | None = None,
) -> tuple[dict[str, Any] | None, str, list[dict[str, Any]]]:
    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    rejected: list[dict[str, Any]] = []
    wanted_names = profile_names(profile)
    if not wanted_names:
        return None, "profile_without_name", []
    cached_names = fighter_name_sets(state)
    for fighter in state.fighters.values():
        known_names = fighter_names(fighter, state, cached_names)
        if not (wanted_names & known_names) and not cito_history:
            continue
        score, reasons = score_identity_candidate(
            profile,
            fighter,
            state,
            cached_names,
            ufc_histories,
            cito_history,
        )
        if score is not None:
            scored.append((score, fighter, reasons))
        elif (
            (
                "fight_history_conflict" in reasons
                or "date_of_birth_conflict" in reasons
                or "physical_profile_conflict" in reasons
                or any(
                    reason.startswith("fight_history_matches_")
                    for reason in reasons
                )
            )
            and (bool(wanted_names & known_names) or bool(cito_history))
        ):
            rejected.append(
                {
                    "fightiq_id": fighter.get("fightiq_id"),
                    "display_name": fighter.get("display_name"),
                    "rejected": True,
                    "reasons": reasons,
                    "biographical_evidence": biographical_compatibility(
                        profile, fighter
                    ),
                }
            )
    scored.sort(key=lambda item: item[0], reverse=True)
    evidence = [
        {
            "fightiq_id": fighter.get("fightiq_id"),
            "display_name": fighter.get("display_name"),
            "score": score,
            "reasons": reasons,
            "name_similarity": round(
                best_name_similarity(profile, cached_names.get(fighter["fightiq_id"], set())),
                3,
            ),
            "fight_history": fight_history_compatibility(
                cito_history or [],
                (ufc_histories or {}).get(
                    normalize_id(fighter.get("ufcstats_id")) or "", []
                ),
            ),
            "biographical_evidence": biographical_compatibility(
                profile, fighter
            ),
        }
        for score, fighter, reasons in scored[:5]
    ] + rejected[:5]
    if not scored:
        return None, "no_candidate", evidence
    best_score, best_fighter, best_reasons = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else None
    decisive = (
        (
            "exact_name_or_known_alias" in best_reasons
            and any(
                reason.startswith("independent_bio_")
                for reason in best_reasons
            )
        )
        or "fight_history_decisive" in best_reasons
    )
    if best_score < 90 or not decisive:
        return None, f"insufficient_score_{best_score}", evidence
    if second_score is not None and best_score - second_score < 20:
        return None, f"ambiguous_margin_{best_score}_{second_score}", evidence
    return best_fighter, "automatic_evidence_match", evidence


def non_mma_reason(profile: dict[str, Any]) -> str | None:
    text = flatten_text(profile)
    if any(marker in text for marker in NON_MMA_MARKERS):
        return "non_mma_power_slap"
    name = normalize_name(profile.get("name"))
    if re.search(r"\b(test|placeholder|dummy)\b", name):
        return "placeholder_test"
    return None


def has_documented_mma_evidence(profile: dict[str, Any]) -> tuple[bool, str]:
    if normalize_id(profile.get("ufcStatsId")):
        return True, "cito_ufcstats_id"
    profile_url = str(profile.get("profileUrl") or profile.get("sourceUrl") or "").lower()
    if "ufc.com/athlete/" in profile_url and "powerslap" not in profile_url:
        return True, "official_ufc_athlete_profile"
    stats = profile.get("stats") or {}
    source = normalize_name(stats.get("source"))
    if "ufcstats" in source or source == "ufc":
        return True, "mma_stats_source"
    text = flatten_text(profile)
    if any(marker in text for marker in MMA_MARKERS) and not non_mma_reason(profile):
        return True, "mma_marker_in_source_payload"
    mma_specific_keys = (
        "takedownsAttempted",
        "takedownsLanded",
        "submissionAvgPer15Min",
        "takedownDefense",
        "sigStrikesByPosition",
    )
    if any(present(stats.get(key)) for key in mma_specific_keys):
        return True, "mma_specific_statistics"
    return False, "no_documented_mma_evidence"


def make_cito_fighter(
    profile: dict[str, Any], fightiq_id: str, now: str, ufcstats_id: str | None
) -> dict[str, Any]:
    candidate = {key: value for key, value in cito_candidate(profile).items() if value is not None}
    display_name = clean(profile.get("name")) or " ".join(
        part
        for part in (candidate.get("first_name"), candidate.get("last_name"))
        if part
    ).strip()
    if not display_name:
        raise RuntimeError(f"Cannot create nameless Cito fighter {profile.get('id')}")
    record = {
        "fightiq_id": fightiq_id,
        "display_name": display_name,
        "ufcstats_id": ufcstats_id,
        "cito_id": profile.get("id"),
        **candidate,
        "photo_source": "cito" if candidate.get("photo_url") else None,
        "photo_original_source": "ufc" if candidate.get("photo_url") else None,
        "photo_rights_status": (
            "pending_confirmation" if candidate.get("photo_url") else None
        ),
        "source_updated_at": now,
        "cito_synced_at": now,
        "fightiq_updated_at": now,
    }
    return {key: value for key, value in record.items() if value is not None}


def update_fighter_from_cito(
    state: PlannedState, fighter: dict[str, Any], profile: dict[str, Any]
) -> None:
    candidate = cito_candidate(profile)
    desired: dict[str, Any] = {}
    for key, value in candidate.items():
        if value is None:
            continue
        if key in STABLE_CITO_FIELDS and present(fighter.get(key)):
            continue
        if fighter.get(key) != value:
            desired[key] = value
    cito_id = str(profile.get("id"))
    if not fighter.get("cito_id"):
        desired["cito_id"] = cito_id
    if candidate.get("photo_url") and fighter.get("photo_url") != candidate.get("photo_url"):
        desired.update(
            {
                "photo_source": "cito",
                "photo_original_source": "ufc",
                "photo_rights_status": "pending_confirmation",
            }
        )
    if desired:
        desired["cito_synced_at"] = state.now
        desired["fightiq_updated_at"] = state.now
        fighter.update(desired)


def process_create_override(
    state: PlannedState,
    item: dict[str, Any],
    profiles_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    canonical_id = str(item["canonical_cito_id"])
    fightiq_id = "fiq_cito_" + re.sub(r"[^A-Za-z0-9]", "", canonical_id)
    fighter = state.fighters.get(fightiq_id) or state.find_by_cito(canonical_id)
    canonical_profile = profiles_by_id.get(canonical_id)
    if not fighter:
        intended_name = normalize_name(item.get("display_name"))
        exact_name_matches = [
            candidate
            for candidate in state.fighters.values()
            if intended_name
            and intended_name == normalize_name(candidate.get("display_name"))
        ]
        if exact_name_matches:
            # Le nom seul ne prouve jamais une identité. On bloque une nouvelle
            # création potentiellement dupliquée sans fusionner un homonyme.
            raise RuntimeError(
                "Unsafe curated create: existing fighter name collision for "
                f"{item.get('display_name')} "
                f"({[row.get('fightiq_id') for row in exact_name_matches]})"
            )
    if not fighter:
        if not canonical_profile:
            raise RuntimeError(
                f"Resolution registry references absent canonical Cito profile {canonical_id}"
            )
        record = make_cito_fighter(canonical_profile, fightiq_id, state.now, None)
        if clean(item.get("display_name")):
            record["display_name"] = clean(item.get("display_name"))
        fighter = state.add_fighter(record)
    cito_ids = item.get("cito_ids") or [canonical_id]
    cito_names = item.get("cito_names") or [item.get("display_name")]
    for index, cito_id in enumerate(cito_ids):
        cito_id = str(cito_id)
        source_name = cito_names[index] if index < len(cito_names) else None
        state.ensure_source(
            fighter,
            "cito",
            cito_id,
            source_name,
            cito_id == canonical_id,
        )
        profile = profiles_by_id.get(cito_id)
        if profile:
            state.set_resolution(
                cito_resolution_row(
                    profile,
                    state.now,
                    "created_new_fighter",
                    item.get("resolution_reason") or "curated_create",
                    fighter["fightiq_id"],
                    fighter.get("ufcstats_id"),
                )
            )
    return fighter


def apply_manual_profile(
    profile: dict[str, Any], manual_profile: Any
) -> dict[str, Any]:
    """Applique uniquement les champs administrateur explicitement autorisés."""
    if not isinstance(manual_profile, dict):
        return profile
    merged = deepcopy(profile)
    direct_mapping = {
        "display_name": "name",
        "first_name": "firstName",
        "last_name": "lastName",
        "nickname": "nickname",
        "date_of_birth": "birthDate",
        "stance": "stance",
        "division": "division",
        "place_of_birth": "placeOfBirth",
        "profile_url": "profileUrl",
        "record_wins": "recordWins",
        "record_losses": "recordLosses",
        "record_draws": "recordDraws",
        "record_nc": "recordNoContest",
    }
    for manual_key, cito_key in direct_mapping.items():
        if present(manual_profile.get(manual_key)):
            merged[cito_key] = manual_profile[manual_key]
    if as_float(manual_profile.get("height_cm")) is not None:
        merged["heightInches"] = round(
            float(manual_profile["height_cm"]) / 2.54, 3
        )
    if as_float(manual_profile.get("reach_cm")) is not None:
        merged["reachInches"] = round(
            float(manual_profile["reach_cm"]) / 2.54, 3
        )
    if as_float(manual_profile.get("current_weight_kg")) is not None:
        merged["weightLbs"] = round(
            float(manual_profile["current_weight_kg"]) / 0.45359237, 3
        )
    return merged


@dataclass
class AdminDecision:
    handled: bool = False
    terminal: bool = False
    fighter: dict[str, Any] | None = None
    profile: dict[str, Any] | None = None
    reason: str | None = None


def reject_admin_decision(
    state: PlannedState,
    resolution: dict[str, Any],
    reason: str,
) -> AdminDecision:
    rejected = deepcopy(resolution)
    rejected.update(
        {
            "resolution_status": "quarantined",
            "resolution_reason": f"admin_review_rejected:{reason}",
            "review_status": "rejected",
            "resolved_at": state.now,
        }
    )
    state.set_resolution(rejected)
    return AdminDecision(handled=True, terminal=True, reason=reason)


def process_admin_decision(
    state: PlannedState,
    profile: dict[str, Any],
    resolution: dict[str, Any] | None,
) -> AdminDecision:
    """Traduit une décision approuvée dans l'interface admin en plan canonique."""
    if not resolution or resolution.get("review_status") != "approved":
        return AdminDecision(profile=profile)
    action = clean(resolution.get("admin_action"))
    classification = clean(resolution.get("review_classification"))
    if action not in {"link", "create", "exclude", "needs_info"}:
        return reject_admin_decision(state, resolution, "invalid_action")
    if action == "needs_info":
        pending = deepcopy(resolution)
        pending["review_status"] = "needs_info"
        pending["resolved_at"] = state.now
        state.set_resolution(pending)
        return AdminDecision(
            handled=True,
            terminal=True,
            profile=profile,
            reason="admin_needs_info",
        )

    effective_profile = apply_manual_profile(
        profile, resolution.get("manual_profile")
    )
    if action == "exclude":
        if classification not in TERMINAL_EXCLUSION_CLASSIFICATIONS:
            return reject_admin_decision(
                state, resolution, "invalid_exclusion_classification"
            )
        if classification == "other" and not clean(resolution.get("admin_notes")):
            return reject_admin_decision(
                state, resolution, "other_classification_requires_notes"
            )
        row = cito_resolution_row(
            effective_profile,
            state.now,
            "excluded",
            f"admin_classification:{classification}",
        )
        row.update(
            {
                field_name: resolution.get(field_name)
                for field_name in REVIEW_FIELDS
                if field_name in resolution
            }
        )
        row["review_status"] = "applied"
        row["action_applied_at"] = state.now
        state.set_resolution(row)
        return AdminDecision(
            handled=True,
            terminal=True,
            profile=effective_profile,
            reason=f"admin_classification:{classification}",
        )

    target_fiq = normalize_id(resolution.get("target_fightiq_id"))
    target_ufc = normalize_id(resolution.get("target_ufcstats_id"))
    by_fiq = state.fighters.get(target_fiq) if target_fiq else None
    by_ufc = state.find_by_ufcstats(target_ufc) if target_ufc else None
    if by_fiq and by_ufc and by_fiq.get("fightiq_id") != by_ufc.get("fightiq_id"):
        return reject_admin_decision(state, resolution, "target_ids_conflict")
    fighter = by_fiq or by_ufc

    if action == "link":
        if classification not in {
            "ufc_fighter",
            "mma_fighter_non_ufc",
            "duplicate_cito_profile",
        }:
            return reject_admin_decision(
                state, resolution, "link_requires_fighter_classification"
            )
        if not fighter:
            return reject_admin_decision(state, resolution, "link_target_missing")
        return AdminDecision(
            handled=True,
            fighter=fighter,
            profile=effective_profile,
            reason="admin_verified_link",
        )

    if classification not in {"ufc_fighter", "mma_fighter_non_ufc"}:
        return reject_admin_decision(
            state, resolution, "create_requires_mma_classification"
        )
    if fighter:
        return AdminDecision(
            handled=True,
            fighter=fighter,
            profile=effective_profile,
            reason="admin_create_reused_existing_target",
        )
    wanted_names = profile_names(effective_profile)
    name_cache = fighter_name_sets(state)
    name_collisions = [
        candidate.get("fightiq_id")
        for candidate in state.fighters.values()
        if wanted_names
        & name_cache.get(str(candidate.get("fightiq_id")), set())
    ]
    if name_collisions:
        return reject_admin_decision(
            state,
            resolution,
            "create_name_collision_use_link:"
            + ",".join(str(value) for value in name_collisions[:5]),
        )
    cito_id = str(profile["id"])
    fightiq_id = (
        f"fiq_{target_ufc}"
        if target_ufc
        else "fiq_cito_" + re.sub(r"[^A-Za-z0-9]", "", cito_id)
    )
    if fightiq_id in state.fighters:
        return reject_admin_decision(
            state, resolution, "deterministic_fightiq_id_collision"
        )
    fighter = state.add_fighter(
        make_cito_fighter(
            effective_profile,
            fightiq_id,
            state.now,
            target_ufc,
        )
    )
    if target_ufc:
        state.ensure_source(
            fighter,
            "ufcstats",
            target_ufc,
            fighter.get("display_name"),
            True,
        )
    return AdminDecision(
        handled=True,
        fighter=fighter,
        profile=effective_profile,
        reason=f"admin_verified_create:{classification}",
    )


def mark_admin_decision_applied(
    row: dict[str, Any],
    resolution: dict[str, Any] | None,
    now: str,
) -> dict[str, Any]:
    if not resolution or resolution.get("review_status") != "approved":
        return row
    row.update(
        {
            field_name: resolution.get(field_name)
            for field_name in REVIEW_FIELDS
            if field_name in resolution
        }
    )
    row["review_status"] = "applied"
    row["action_applied_at"] = now
    return row


def plan_cito_profiles(
    state: PlannedState,
    profiles: list[dict[str, Any]],
    registry: ResolutionRegistry,
    ufc_histories: dict[str, list[dict[str, Any]]] | None = None,
    history_loader: Callable[[dict[str, Any]], FightHistoryFetch] | None = None,
) -> list[dict[str, Any]]:
    profiles_by_id = {
        str(profile["id"]): profile for profile in profiles if profile.get("id")
    }
    quarantine_report: list[dict[str, Any]] = []
    identity_fingerprint = identity_index_fingerprint(state, ufc_histories)

    for profile in profiles:
        cito_id = normalize_id(profile.get("id"))
        if not cito_id:
            raise RuntimeError(
                f"Cito source contract failure: profile without id ({profile.get('name')})"
            )

        reported_ufcstats = normalize_id(profile.get("ufcStatsId"))
        mapped = state.find_by_cito(cito_id)
        direct = state.find_by_ufcstats(reported_ufcstats)

        if mapped and direct and mapped["fightiq_id"] != direct["fightiq_id"]:
            raise RuntimeError(
                "Source identity conflict for Cito "
                f"{cito_id}: existing mapping={mapped['fightiq_id']}, "
                f"reported UFCStats mapping={direct['fightiq_id']}"
            )

        fighter = mapped or direct
        reason = "existing_cito_mapping" if mapped else "exact_ufcstats_id"

        resolution = state.resolutions.get(cito_id)
        admin_applied = False
        if fighter and resolution and resolution.get("review_status") == "approved":
            action = clean(resolution.get("admin_action"))
            target_fiq = normalize_id(resolution.get("target_fightiq_id"))
            target_ufc = normalize_id(resolution.get("target_ufcstats_id"))
            target = (
                state.fighters.get(target_fiq)
                if target_fiq
                else state.find_by_ufcstats(target_ufc)
            )
            classification = clean(resolution.get("review_classification"))
            if (
                action != "link"
                or classification
                not in {
                    "ufc_fighter",
                    "mma_fighter_non_ufc",
                    "duplicate_cito_profile",
                }
                or not target
                or (
                target.get("fightiq_id") != fighter.get("fightiq_id")
                )
            ):
                reject_admin_decision(
                    state,
                    resolution,
                    "profile_already_mapped_admin_target_or_action_invalid",
                )
                quarantine_report.append(
                    {
                        "cito_id": cito_id,
                        "name": profile.get("name"),
                        "reason": "admin_review_rejected:profile_already_mapped",
                        "review_status": "rejected",
                    }
                )
                continue
            profile = apply_manual_profile(
                profile, resolution.get("manual_profile")
            )
            reason = "admin_verified_existing_mapping"
            admin_applied = True
        if not fighter:
            admin_decision = process_admin_decision(state, profile, resolution)
            if admin_decision.profile is not None:
                profile = admin_decision.profile
            if admin_decision.terminal:
                current = state.resolutions.get(cito_id) or resolution or {}
                if current.get("resolution_status") == "quarantined":
                    quarantine_report.append(
                        {
                            "cito_id": cito_id,
                            "name": profile.get("name"),
                            "reason": current.get("resolution_reason"),
                            "review_status": current.get("review_status"),
                            "required_decision": "correct_admin_review",
                        }
                    )
                continue
            if admin_decision.fighter:
                fighter = admin_decision.fighter
                reason = admin_decision.reason or "admin_verified_identity"
                admin_applied = True

        if not fighter and resolution and resolution.get("matched_fightiq_id"):
            candidate = state.fighters.get(resolution["matched_fightiq_id"])
            if candidate and resolution.get("resolution_status") in {
                "linked_existing_fighter",
                "created_new_fighter",
            }:
                fighter = candidate
                reason = "existing_resolution_record"

        if not fighter and cito_id in registry.links:
            item = registry.links[cito_id]
            fighter = state.find_by_ufcstats(normalize_id(item.get("ufcstats_id")))
            if not fighter:
                quarantine_report.append(
                    quarantine_profile(
                        state,
                        profile,
                        "curated_ufcstats_target_missing",
                        identity_fingerprint,
                        [
                            {
                                "expected_ufcstats_id": item.get("ufcstats_id"),
                                "resolution_reason": item.get("resolution_reason"),
                            }
                        ],
                    )
                )
                continue
            reason = item.get("resolution_reason") or "curated_link"

        if not fighter and cito_id in registry.creates:
            fighter = process_create_override(
                state, registry.creates[cito_id], profiles_by_id
            )
            reason = registry.creates[cito_id].get("resolution_reason") or "curated_create"

        if not fighter and cito_id in registry.exclusions:
            item = registry.exclusions[cito_id]
            state.set_resolution(
                cito_resolution_row(
                    profile,
                    state.now,
                    "excluded",
                    item.get("resolution_reason") or "curated_exclusion",
                )
            )
            continue

        if not fighter and resolution and resolution.get("resolution_status") == "excluded":
            if (
                resolution.get("review_status") == "applied"
                and resolution.get("admin_action") == "exclude"
            ):
                continue
            if stored_cito_fingerprint(resolution) == cito_fingerprint(profile):
                continue

        if not fighter and resolution and resolution.get("resolution_status") == "quarantined":
            metadata = quarantine_metadata(resolution)
            unchanged_profile = (
                stored_cito_fingerprint(resolution) == cito_fingerprint(profile)
            )
            unchanged_identity_index = (
                metadata.get("identity_index_fingerprint") == identity_fingerprint
            )
            if unchanged_profile and unchanged_identity_index:
                quarantine_report.append(
                    {
                        "cito_id": cito_id,
                        "name": profile.get("name"),
                        "reason": resolution.get("resolution_reason"),
                        "unchanged_quarantine": True,
                    }
                )
                continue

        evidence: list[dict[str, Any]] = []
        history_fetch = FightHistoryFetch()
        if not fighter:
            exclusion = non_mma_reason(profile)
            if exclusion:
                state.set_resolution(
                    cito_resolution_row(
                        profile, state.now, "excluded", exclusion
                    )
                )
                continue
            if history_loader is not None:
                history_fetch = history_loader(profile)
            else:
                embedded = embedded_cito_history(profile)
                if embedded is not None:
                    history_fetch = FightHistoryFetch(
                        bouts=normalize_cito_fight_history(embedded, profile),
                        source="cito_embedded",
                    )
            fighter, reason, evidence = automatic_identity_match(
                profile,
                state,
                ufc_histories,
                history_fetch.bouts,
            )

        if not fighter and reported_ufcstats:
            fighter = state.add_fighter(
                make_cito_fighter(
                    profile,
                    f"fiq_{reported_ufcstats}",
                    state.now,
                    reported_ufcstats,
                )
            )
            state.ensure_source(
                fighter,
                "ufcstats",
                reported_ufcstats,
                profile.get("name"),
                True,
            )
            reason = "new_fighter_with_cito_ufcstats_id"

        if not fighter:
            has_mma, mma_reason = has_documented_mma_evidence(profile)
            if has_mma and mma_reason in {
                "official_ufc_athlete_profile",
                "mma_stats_source",
            }:
                fightiq_id = "fiq_cito_" + re.sub(r"[^A-Za-z0-9]", "", cito_id)
                fighter = state.add_fighter(
                    make_cito_fighter(profile, fightiq_id, state.now, None)
                )
                reason = mma_reason
            else:
                quarantine_reason = reason or mma_reason
                if has_mma:
                    quarantine_reason = f"manual_mma_confirmation:{mma_reason}"
                quarantine_report.append(
                    quarantine_profile(
                        state,
                        profile,
                        quarantine_reason,
                        identity_fingerprint,
                        evidence,
                        history_fetch,
                    )
                )
                continue

        if reported_ufcstats and fighter.get("ufcstats_id") not in {
            None,
            reported_ufcstats,
        }:
            raise RuntimeError(
                "Fighter UFCStats conflict for Cito "
                f"{cito_id}: fighter={fighter.get('fightiq_id')}, "
                f"current={fighter.get('ufcstats_id')}, "
                f"reported={reported_ufcstats}"
            )

        if reported_ufcstats and not fighter.get("ufcstats_id"):
            fighter["ufcstats_id"] = reported_ufcstats
            fighter["fightiq_updated_at"] = state.now

        primary_profile = not bool(fighter.get("cito_id")) or (
            fighter.get("cito_id") == cito_id
        )
        state.ensure_source(
            fighter,
            "cito",
            cito_id,
            profile.get("name"),
            primary_profile,
        )
        if reported_ufcstats:
            state.ensure_source(
                fighter,
                "ufcstats",
                reported_ufcstats,
                fighter.get("display_name"),
                True,
            )
        # Une identité Cito secondaire est conservée comme alias de source, mais
        # ne doit jamais écraser les données du profil Cito primaire.
        if primary_profile:
            update_fighter_from_cito(state, fighter, profile)

        if not reported_ufcstats or resolution or reason not in {
            "existing_cito_mapping",
            "exact_ufcstats_id",
        }:
            status = (
                "created_new_fighter"
                if fighter["fightiq_id"] not in state.original_fighters
                and not fighter.get("ufcstats_id")
                else "linked_existing_fighter"
            )
            resolution_row = cito_resolution_row(
                profile,
                state.now,
                status,
                reason,
                fighter["fightiq_id"],
                fighter.get("ufcstats_id"),
            )
            if admin_applied:
                resolution_row = mark_admin_decision_applied(
                    resolution_row, resolution, state.now
                )
            state.set_resolution(resolution_row)

    # Une ancienne ligne NULL disparue de Cito devient une quarantaine explicite.
    returned_ids = set(profiles_by_id)
    for cito_id, resolution in list(state.resolutions.items()):
        if not resolution.get("resolution_status") and cito_id not in returned_ids:
            quarantined = deepcopy(resolution)
            quarantined.update(
                {
                    "resolution_status": "quarantined",
                    "resolution_reason": "profile_no_longer_returned_by_cito",
                    "resolved_at": state.now,
                }
            )
            state.set_resolution(quarantined)
            quarantine_report.append(
                {
                    "cito_id": cito_id,
                    "name": resolution.get("name"),
                    "reason": "profile_no_longer_returned_by_cito",
                    "required_decision": "link | create | exclude",
                }
            )
    return quarantine_report


def duplicate_values(rows: Iterable[dict[str, Any]], key: str) -> dict[Any, int]:
    counts = Counter(row.get(key) for row in rows if row.get(key) is not None)
    return {value: count for value, count in counts.items() if count > 1}


def validate_state(state: PlannedState, minimum_fighters: int) -> dict[str, Any]:
    fighters = list(state.fighters.values())
    sources = list(state.sources.values())
    resolutions = list(state.resolutions.values())
    fightiq_ids = set(state.fighters)
    source_by_fiq: dict[str, set[str]] = defaultdict(set)
    for source in sources:
        source_by_fiq[str(source.get("fightiq_id"))].add(str(source.get("source")))

    errors: list[str] = []
    if len(fighters) < minimum_fighters:
        errors.append(
            f"fighter count shrank unexpectedly: {len(fighters)} < {minimum_fighters}"
        )
    for key in ("fightiq_id", "ufcstats_id", "cito_id"):
        duplicates = duplicate_values(fighters, key)
        if duplicates:
            errors.append(f"duplicate {key}: {list(duplicates)[:10]}")
    orphan_sources = [
        source for source in sources if source.get("fightiq_id") not in fightiq_ids
    ]
    if orphan_sources:
        errors.append(f"orphan source mappings: {len(orphan_sources)}")
    unresolved = [row for row in resolutions if not row.get("resolution_status")]
    if unresolved:
        errors.append(f"unresolved Cito rows: {len(unresolved)}")
    quarantined = [
        row
        for row in resolutions
        if row.get("resolution_status") == "quarantined"
    ]

    excluded_ids = {
        str(row.get("cito_id"))
        for row in resolutions
        if row.get("resolution_status") == "excluded"
    }
    mapped_exclusions = [
        source
        for source in sources
        if source.get("source") == "cito" and str(source.get("source_id")) in excluded_ids
    ]
    primary_exclusions = [
        fighter
        for fighter in fighters
        if str(fighter.get("cito_id")) in excluded_ids
    ]
    if mapped_exclusions or primary_exclusions:
        errors.append("excluded/non-MMA Cito identity is present in canonical fighters")

    without_source = [
        fighter
        for fighter in fighters
        if not ({"ufcstats", "cito"} & source_by_fiq.get(fighter["fightiq_id"], set()))
    ]
    if without_source:
        errors.append(f"fighters without canonical source mapping: {len(without_source)}")

    both = sum(
        1 for fighter in fighters if fighter.get("ufcstats_id") and fighter.get("cito_id")
    )
    ufc_only = sum(
        1 for fighter in fighters if fighter.get("ufcstats_id") and not fighter.get("cito_id")
    )
    cito_only = sum(
        1 for fighter in fighters if fighter.get("cito_id") and not fighter.get("ufcstats_id")
    )
    neither = len(fighters) - both - ufc_only - cito_only
    if neither:
        errors.append(f"fighters outside the three canonical categories: {neither}")

    report = {
        "fighters_total": len(fighters),
        "ufcstats_cito": both,
        "ufcstats_only": ufc_only,
        "cito_only": cito_only,
        "unresolved": len(unresolved),
        "quarantined": len(quarantined),
        "excluded_source_profiles": len(excluded_ids),
        "source_mappings": len(sources),
        "errors": errors,
    }
    if errors:
        raise RuntimeError("Canonical validation failed: " + " | ".join(errors))
    return report


def build_admin_quarantine_report(state: PlannedState) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    for row in state.resolutions.values():
        if row.get("resolution_status") != "quarantined":
            continue
        metadata = quarantine_metadata(row)
        profiles.append(
            {
                "cito_id": row.get("cito_id"),
                "name": row.get("name"),
                "first_name": row.get("first_name"),
                "last_name": row.get("last_name"),
                "nickname": row.get("nickname"),
                "slug": row.get("slug"),
                "birth_date": row.get("birth_date"),
                "record_text": row.get("record_text"),
                "division": row.get("division"),
                "profile_url": row.get("profile_url"),
                "reason": row.get("resolution_reason"),
                "review_status": row.get("review_status") or "pending",
                "review_classification": row.get("review_classification"),
                "admin_action": row.get("admin_action"),
                "target_fightiq_id": row.get("target_fightiq_id"),
                "target_ufcstats_id": row.get("target_ufcstats_id"),
                "manual_profile": row.get("manual_profile") or {},
                "admin_notes": row.get("admin_notes"),
                "last_seen_at": row.get("last_seen_at"),
                "candidates": metadata.get("candidates") or [],
                "fight_history": metadata.get("fight_history"),
            }
        )
    profiles.sort(
        key=lambda item: (
            str(item.get("review_status")),
            normalize_name(item.get("name")),
            str(item.get("cito_id")),
        )
    )
    return {
        "schema_version": 1,
        "generated_at": state.now,
        "count": len(profiles),
        "allowed_actions": {
            "link": "Associer à un fightiq_id/ufcstats_id existant",
            "create": "Créer une identité MMA vérifiée sans doublon",
            "exclude": "Classer hors fighters et ne plus reproposer",
            "needs_info": "Conserver en attente d'informations",
        },
        "allowed_classifications": [
            "ufc_fighter",
            "mma_fighter_non_ufc",
            "duplicate_cito_profile",
            "power_slap",
            "non_mma",
            "test_placeholder",
            "not_a_fighter",
            "other",
        ],
        "profiles": profiles,
    }


def write_admin_quarantine_report(
    path: str | Path,
    state: PlannedState,
) -> dict[str, Any]:
    report = build_admin_quarantine_report(state)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    return report


def changed_records(
    original: dict[Any, dict[str, Any]], final: dict[Any, dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[tuple[Any, dict[str, Any]]]]:
    inserted: list[dict[str, Any]] = []
    updated: list[tuple[Any, dict[str, Any]]] = []
    for key, row in final.items():
        if key not in original:
            inserted.append(row)
            continue
        diff = meaningful_diff(original[key], row)
        if diff:
            updated.append((key, diff))
    return inserted, updated


def chunks(items: list[dict[str, Any]], size: int = 250) -> Iterable[list[dict[str, Any]]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def apply_plan(sb: Any, state: PlannedState, dry_run: bool) -> dict[str, int]:
    fighter_inserts, fighter_updates = changed_records(
        state.original_fighters, state.fighters
    )
    source_inserts, source_updates = changed_records(
        state.original_sources, state.sources
    )
    resolution_inserts, resolution_updates = changed_records(
        state.original_resolutions, state.resolutions
    )
    summary = {
        "fighter_inserts": len(fighter_inserts),
        "fighter_updates": len(fighter_updates),
        "source_upserts": len(source_inserts) + len(source_updates),
        "resolution_upserts": len(resolution_inserts) + len(resolution_updates),
    }
    print("===== WRITE PLAN =====")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if dry_run:
        print("DRY RUN: no Supabase rows changed")
        return summary

    resolution_rows = resolution_inserts + [
        state.resolutions[key] for key, _ in resolution_updates
    ]
    quarantine_rows = [
        row
        for row in resolution_rows
        if row.get("resolution_status") == "quarantined"
    ]
    other_resolution_rows = [
        row
        for row in resolution_rows
        if row.get("resolution_status") != "quarantined"
    ]

    # La première écriture vérifie que le schéma accepte la quarantaine. Si ce
    # statut est refusé, aucune fiche combattant n'a encore été modifiée.
    for batch in chunks(quarantine_rows):
        sb.table("cito_unmatched_fighters").upsert(
            batch, on_conflict="cito_id"
        ).execute()

    for record in fighter_inserts:
        sb.table("fighters").insert(
            {key: value for key, value in record.items() if key != "id"}
        ).execute()
    for fightiq_id, patch in fighter_updates:
        sb.table("fighters").update(patch).eq("fightiq_id", fightiq_id).execute()

    source_rows = source_inserts + [state.sources[key] for key, _ in source_updates]
    for batch in chunks(source_rows):
        sb.table("fighter_source_ids").upsert(
            batch, on_conflict="source,source_id"
        ).execute()

    for batch in chunks(other_resolution_rows):
        sb.table("cito_unmatched_fighters").upsert(
            batch, on_conflict="cito_id"
        ).execute()
    return summary


def load_database_state(sb: Any, now: str) -> PlannedState:
    fighters = fetch_all(sb, "fighters", FIGHTER_FIELDS)
    sources = fetch_all(sb, "fighter_source_ids", SOURCE_FIELDS)
    resolutions = fetch_all(sb, "cito_unmatched_fighters", RESOLUTION_FIELDS)
    return PlannedState.from_rows(fighters, sources, resolutions, now)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        default=os.environ.get("DRY_RUN", "false").lower() == "true",
        help="planifie et valide sans écrire dans Supabase",
    )
    parser.add_argument(
        "--resolution-file",
        default=os.environ.get(
            "CITO_RESOLUTION_FILES",
            os.environ.get("CITO_RESOLUTION_FILE", DEFAULT_RESOLUTION_FILES),
        ),
        help="registres JSON séparés par une virgule, le dernier est prioritaire",
    )
    parser.add_argument(
        "--report-file",
        default=os.environ.get(
            "FIGHTIQ_QUARANTINE_REPORT",
            "reports/fighter_identity_quarantine.json",
        ),
        help="rapport JSON destiné à l'administration et à l'artefact GitHub Actions",
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

    # Toutes les sources sont téléchargées avant la moindre écriture.
    print("Fetching UFCStats source snapshots...")
    ufc_details = download_csv(UFC_DETAILS_URL)
    ufc_tott = download_csv(UFC_TOTT_URL)
    ufc_fight_results = download_csv(UFC_FIGHT_RESULTS_URL)
    ufc_events = download_csv(UFC_EVENT_DETAILS_URL)
    ufc_records = build_ufc_records(ufc_details, ufc_tott)
    ufc_histories = build_ufc_fight_histories(
        ufc_fight_results,
        ufc_events,
        ufc_records,
    )
    print(f"UFCStats fighters received: {len(ufc_records)}")
    print(
        "UFCStats identity histories indexed: "
        f"{len(ufc_histories)} fighters / "
        f"{sum(len(rows) for rows in ufc_histories.values())} fighter-bouts"
    )
    print("Fetching Cito fighter profiles...")
    cito_profiles = fetch_json(CITO_FIGHTERS_URL, cito_key, timeout=150)
    print(f"Cito fighters received: {len(cito_profiles)}")

    sb = create_client(supabase_url, supabase_key)
    now = utc_now()
    state = load_database_state(sb, now)
    minimum_fighters = len(state.fighters)
    registry = load_resolution_registry(args.resolution_file)

    history_cache: dict[str, FightHistoryFetch] = {}

    def load_history(profile: dict[str, Any]) -> FightHistoryFetch:
        cito_id = str(profile.get("id") or profile.get("slug") or "")
        if cito_id not in history_cache:
            history_cache[cito_id] = fetch_cito_fight_history(profile, cito_key)
        return history_cache[cito_id]

    backfill_existing_source_mappings(state)
    apply_ufcstats_plan(state, ufc_records)
    quarantine_report = plan_cito_profiles(
        state,
        cito_profiles,
        registry,
        ufc_histories,
        load_history,
    )
    admin_report = write_admin_quarantine_report(args.report_file, state)
    print(
        f"Admin quarantine report: {args.report_file} "
        f"({admin_report['count']} profiles)"
    )

    if quarantine_report:
        print("===== IDENTITY QUARANTINE =====")
        print(
            json.dumps(
                {
                    "count": len(quarantine_report),
                    "fighter_profiles": quarantine_report,
                },
                ensure_ascii=False,
                indent=2,
                default=str,
            )
        )
        print("Quarantined profiles stay outside fighters; safe updates continue")

    planned_report = validate_state(state, minimum_fighters)
    print("===== PLANNED CANONICAL STATE =====")
    print(json.dumps(planned_report, ensure_ascii=False, indent=2))
    apply_plan(sb, state, args.dry_run)

    if not args.dry_run:
        final_state = load_database_state(sb, utc_now())
        final_report = validate_state(final_state, minimum_fighters)
        print("===== FINAL DATABASE STATE =====")
        print(json.dumps(final_report, ensure_ascii=False, indent=2))
    print("FIGHTIQ UNIFIED FIGHTER SYNC COMPLETE")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"FIGHTIQ SYNC FAILED: {exc}", file=sys.stderr)
        raise
