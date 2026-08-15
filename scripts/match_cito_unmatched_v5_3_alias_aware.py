import json
import os
import re
import unicodedata
from difflib import SequenceMatcher

from supabase import create_client


# V5.3 - conservative alias-aware matching.
# AUDIT ONLY: this script never modifies public.fighters.

MANUAL_ALIASES = {
    # Cito display name -> known UFCStats identity
    "kevin souza": "edimilson souza",
    "kimbo slice": "kevin ferguson",
    "rampage jackson": "quinton jackson",
    "mirko cro cop": "mirko filipovic",
    "tank abbott": "david abbott",
    "minotauro nogueira": "antonio rodrigo nogueira",
}

FIRST_NAME_GROUPS = [
    {"alexandra", "aleksandra"},
    {"ben", "benny", "benjamin"},
    {"brad", "bradley"},
    {"chris", "christopher"},
    {"constantinos", "costas", "kostas"},
    {"dave", "david"},
    {"ed", "eddie", "edward"},
    {"jeff", "jeffrey"},
    {"jim", "jimmy", "james"},
    {"joe", "joseph"},
    {"jon", "john"},
    {"josh", "joshua"},
    {"lucasz", "lukasz", "lukas"},
    {"manny", "manvel"},
    {"max", "maxim", "maximilian"},
    {"nico", "nicholas"},
    {"phil", "philip"},
    {"rich", "richard", "rick", "ricky"},
    {"rob", "robbie", "robert"},
    {"steve", "steven"},
    {"zach", "zachary"},
]

TITLE_SUFFIXES = {"jr", "junior", "ii", "iii", "iv"}


def normalize(value):
    if not value:
        return ""
    value = unicodedata.normalize("NFKD", str(value))
    value = "".join(ch for ch in value if not unicodedata.combining(ch))
    value = value.lower().strip()
    value = value.replace("’", "'")
    value = re.sub(r"[^a-z0-9]+", " ", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def tokens(value):
    return [x for x in normalize(value).split() if x]


def strip_suffixes(value):
    parts = tokens(value)
    while parts and parts[-1] in TITLE_SUFFIXES:
        parts.pop()
    return " ".join(parts)


def equivalent_first_name(a, b):
    a = normalize(a)
    b = normalize(b)
    if not a or not b:
        return False
    if a == b:
        return True
    for group in FIRST_NAME_GROUPS:
        if a in group and b in group:
            return True
    return False


def split_name(value):
    p = tokens(value)
    if not p:
        return "", ""
    if len(p) == 1:
        return p[0], ""
    return p[0], " ".join(p[1:])


def fetch_all(sb, table, fields):
    rows = []
    start = 0
    size = 1000
    while True:
        batch = (
            sb.table(table)
            .select(fields)
            .range(start, start + size - 1)
            .execute()
        ).data or []
        rows.extend(batch)
        if len(batch) < size:
            break
        start += size
    return rows


def cm_to_in(v):
    try:
        return float(v) / 2.54
    except (TypeError, ValueError):
        return None


def kg_to_lb(v):
    try:
        return float(v) / 0.45359237
    except (TypeError, ValueError):
        return None


def close_num(a, b, tolerance):
    try:
        return abs(float(a) - float(b)) <= tolerance
    except (TypeError, ValueError):
        return False


def fighter_name_variants(f):
    variants = set()
    for value in [
        f.get("display_name"),
        " ".join(x for x in [f.get("first_name"), f.get("last_name")] if x),
        f.get("nickname"),
    ]:
        n = normalize(value)
        if n:
            variants.add(n)
            variants.add(strip_suffixes(n))
    return {v for v in variants if v}


def cito_name_variants(c):
    variants = set()
    for value in [
        c.get("name"),
        " ".join(x for x in [c.get("first_name"), c.get("last_name")] if x),
        c.get("nickname"),
    ]:
        n = normalize(value)
        if n:
            variants.add(n)
            variants.add(strip_suffixes(n))
    return {v for v in variants if v}


def name_features(c, f):
    cvars = cito_name_variants(c)
    fvars = fighter_name_variants(f)

    best_ratio = 0.0
    exact = False
    suffixless_exact = False
    token_overlap = 0.0

    for a in cvars:
        for b in fvars:
            best_ratio = max(best_ratio, SequenceMatcher(None, a, b).ratio())
            if a == b:
                exact = True
            if strip_suffixes(a) == strip_suffixes(b):
                suffixless_exact = True
            at = set(tokens(a))
            bt = set(tokens(b))
            if at and bt:
                token_overlap = max(token_overlap, len(at & bt) / max(len(at), len(bt)))

    # First-name alias + same surname.
    alias_first_same_last = False
    c_first, c_last = split_name(c.get("name"))
    f_first, f_last = split_name(f.get("display_name"))
    if c_last and f_last:
        alias_first_same_last = (
            equivalent_first_name(c_first, f_first)
            and normalize(c_last) == normalize(f_last)
        )

    return {
        "best_ratio": best_ratio,
        "exact": exact,
        "suffixless_exact": suffixless_exact,
        "token_overlap": token_overlap,
        "alias_first_same_last": alias_first_same_last,
    }


def score_match(c, f):
    cn = normalize(c.get("name"))
    fn = normalize(f.get("display_name"))

    manual_target = MANUAL_ALIASES.get(cn)
    manual_alias = bool(manual_target and fn == manual_target)

    feat = name_features(c, f)
    score = feat["best_ratio"] * 65
    reasons = [f'name={feat["best_ratio"]:.3f}']

    if manual_alias:
        score += 100
        reasons.append("manual_alias")

    if feat["exact"]:
        score += 28
        reasons.append("exact_name")

    if feat["suffixless_exact"] and not feat["exact"]:
        score += 18
        reasons.append("suffixless_exact")

    if feat["alias_first_same_last"]:
        score += 20
        reasons.append("first_name_alias_same_last")

    if feat["token_overlap"] >= 0.75:
        score += 8
        reasons.append("strong_token_overlap")

    # DOB is the strongest structured validation.
    if c.get("birth_date") and f.get("date_of_birth"):
        if str(c["birth_date"])[:10] == str(f["date_of_birth"])[:10]:
            score += 35
            reasons.append("dob_match")
        else:
            score -= 45
            reasons.append("dob_conflict")

    # Physical validation.
    fi = cm_to_in(f.get("height_cm"))
    if c.get("height_inches") is not None and fi is not None:
        if close_num(c["height_inches"], fi, 1.0):
            score += 9
            reasons.append("height_match")
        elif abs(float(c["height_inches"]) - fi) > 3:
            score -= 10
            reasons.append("height_conflict")

    ri = cm_to_in(f.get("reach_cm"))
    if c.get("reach_inches") is not None and ri is not None:
        if close_num(c["reach_inches"], ri, 1.5):
            score += 8
            reasons.append("reach_match")
        elif abs(float(c["reach_inches"]) - ri) > 4:
            score -= 6
            reasons.append("reach_conflict")

    wl = kg_to_lb(f.get("current_weight_kg"))
    if c.get("weight_lbs") is not None and wl is not None:
        if close_num(c["weight_lbs"], wl, 8):
            score += 5
            reasons.append("weight_match")

    if c.get("stance") and f.get("stance"):
        if normalize(c["stance"]) == normalize(f["stance"]):
            score += 3
            reasons.append("stance_match")

    if c.get("division") and f.get("current_division"):
        cdiv = normalize(c["division"])
        fdiv = normalize(f["current_division"])
        if cdiv == fdiv:
            score += 4
            reasons.append("division_match")

    return round(score, 2), reasons


def classify(best_score, gap, reasons):
    if "dob_conflict" in reasons and "manual_alias" not in reasons:
        return "REVIEW"

    if "manual_alias" in reasons:
        return "CERTAIN"

    strong_identity = any(
        r in reasons
        for r in ["exact_name", "suffixless_exact", "first_name_alias_same_last"]
    )

    structured = sum(
        r in reasons
        for r in ["dob_match", "height_match", "reach_match", "weight_match", "stance_match", "division_match"]
    )

    if best_score >= 115 and gap >= 20 and strong_identity:
        return "CERTAIN"
    if best_score >= 98 and gap >= 12 and (strong_identity or structured >= 2):
        return "HIGH"
    if best_score >= 78:
        return "REVIEW"
    return "UNRESOLVED"


def main():
    url = os.environ["SUPABASE_URL"]
    key = os.environ["SUPABASE_SECRET_KEY"]
    sb = create_client(url, key)

    unmatched = fetch_all(
        sb,
        "cito_unmatched_fighters",
        "cito_id,name,first_name,last_name,nickname,slug,division,status,is_active,"
        "record_text,place_of_birth,height_inches,weight_lbs,reach_inches,stance,birth_date,"
        "photo_url,profile_url,stats_available"
    )

    fighters = fetch_all(
        sb,
        "fighters",
        "id,fightiq_id,ufcstats_id,display_name,first_name,last_name,nickname,date_of_birth,"
        "height_cm,reach_cm,current_weight_kg,stance,current_division,is_active,cito_id"
    )

    print("===== V5.3 ALIAS-AWARE CITO MATCHING =====")
    print(f"Cito unmatched rows: {len(unmatched)}")
    print(f"FightIQ fighters: {len(fighters)}")

    summary = {"CERTAIN": 0, "HIGH": 0, "REVIEW": 0, "UNRESOLVED": 0}
    results = []

    for c in unmatched:
        ranked = []
        for f in fighters:
            s, reasons = score_match(c, f)
            ranked.append((s, f, reasons))

        ranked.sort(key=lambda x: x[0], reverse=True)
        top = ranked[:5]
        best_score, best, reasons = top[0]
        second = top[1][0] if len(top) > 1 else 0
        gap = round(best_score - second, 2)
        bucket = classify(best_score, gap, reasons)
        summary[bucket] += 1

        results.append({
            "bucket": bucket,
            "cito": {
                "cito_id": c.get("cito_id"),
                "name": c.get("name"),
                "nickname": c.get("nickname"),
                "birth_date": c.get("birth_date"),
                "division": c.get("division"),
                "is_active": c.get("is_active"),
            },
            "best_match": {
                "score": best_score,
                "gap_to_second": gap,
                "reasons": reasons,
                "fightiq_id": best.get("fightiq_id"),
                "ufcstats_id": best.get("ufcstats_id"),
                "display_name": best.get("display_name"),
                "nickname": best.get("nickname"),
                "date_of_birth": best.get("date_of_birth"),
                "division": best.get("current_division"),
            },
            "top5": [
                {
                    "score": s,
                    "display_name": f.get("display_name"),
                    "ufcstats_id": f.get("ufcstats_id"),
                    "nickname": f.get("nickname"),
                    "date_of_birth": f.get("date_of_birth"),
                    "division": f.get("current_division"),
                    "reasons": r,
                }
                for s, f, r in top
            ],
        })

    order = {"CERTAIN": 0, "HIGH": 1, "REVIEW": 2, "UNRESOLVED": 3}
    results.sort(key=lambda x: (order[x["bucket"]], -x["best_match"]["score"]))

    print("\n===== SUMMARY =====")
    for k, v in summary.items():
        print(f"{k}: {v}")

    for bucket in ["CERTAIN", "HIGH", "REVIEW"]:
        print(f"\n===== {bucket} =====")
        for r in [x for x in results if x["bucket"] == bucket][:100]:
            print(
                f'{r["cito"]["name"]} -> {r["best_match"]["display_name"]} '
                f'score={r["best_match"]["score"]} '
                f'gap={r["best_match"]["gap_to_second"]} '
                f'reasons={",".join(r["best_match"]["reasons"])}'
            )

    with open("cito_unmatched_matching_v5_3_report.json", "w", encoding="utf-8") as fh:
        json.dump({"summary": summary, "results": results}, fh, ensure_ascii=False, indent=2)

    print("\nAUDIT ONLY: no rows were modified.")
    print("Report written to cito_unmatched_matching_v5_3_report.json")


if __name__ == "__main__":
    main()
