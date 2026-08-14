import json
import os
from collections import Counter, defaultdict
from urllib.request import Request, urlopen

CITO_URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"

KEYWORDS = (
    "birth", "dob", "country", "nation", "place", "rank", "p4p",
    "bio", "description", "image", "photo", "headshot", "body",
    "reach", "height", "weight", "stance", "style", "train",
    "record", "win", "loss", "draw", "contest", "debut",
    "status", "active", "champion", "division", "social",
    "twitter", "instagram", "facebook", "fight", "finish",
    "knockout", "submission", "round", "stat", "accuracy",
    "defense", "strike", "takedown", "grapple"
)

def fetch_cito(api_key):
    req = Request(
        CITO_URL,
        headers={
            "x-api-key": api_key,
            "User-Agent": "FightIQ/3.0",
            "Accept": "application/json",
        },
    )
    with urlopen(req, timeout=120) as r:
        payload = json.loads(r.read().decode("utf-8"))

    if not payload.get("success"):
        raise RuntimeError("Cito API returned success=false")

    data = payload.get("data")
    if not isinstance(data, list):
        raise RuntimeError("Unexpected Cito fighters response")

    return data, payload.get("meta", {})

def present(value):
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True

def walk(value, path="", out=None):
    if out is None:
        out = []

    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}" if path else key
            out.append((child_path, child))
            walk(child, child_path, out)
    elif isinstance(value, list):
        list_path = f"{path}[]" if path else "[]"
        for child in value:
            out.append((list_path, child))
            walk(child, list_path, out)

    return out

def relevant(path):
    p = path.lower()
    return any(keyword in p for keyword in KEYWORDS)

def type_name(value):
    if value is None: return "null"
    if isinstance(value, bool): return "bool"
    if isinstance(value, int): return "int"
    if isinstance(value, float): return "float"
    if isinstance(value, str): return "str"
    if isinstance(value, list): return "list"
    if isinstance(value, dict): return "dict"
    return type(value).__name__

def main():
    api_key = os.environ.get("CITO_API_KEY")
    if not api_key:
        raise RuntimeError("Missing CITO_API_KEY")

    print("Fetching all Cito fighters in ONE API call...")
    fighters, meta = fetch_cito(api_key)
    total = len(fighters)

    occurrences = Counter()
    populated = Counter()
    types = defaultdict(Counter)
    examples = defaultdict(list)

    for fighter in fighters:
        seen_paths = set()
        for path, value in walk(fighter):
            if path not in seen_paths:
                occurrences[path] += 1
                seen_paths.add(path)

            types[path][type_name(value)] += 1

            if present(value):
                populated[path] += 1
                if len(examples[path]) < 3 and not isinstance(value, (dict, list)):
                    examples[path].append({
                        "fighter": fighter.get("name"),
                        "value": value,
                    })

    all_paths = sorted(occurrences)
    relevant_paths = [p for p in all_paths if relevant(p)]

    print("\n===== V3 DEEP FIELD DISCOVERY =====")
    print(f"Cito fighters: {total}")
    print(f"Meta total: {meta.get('total')}")
    print(f"Unique JSON paths discovered: {len(all_paths)}")
    print(f"Relevant paths discovered: {len(relevant_paths)}")

    print("\n===== RELEVANT FIELD PATHS =====")
    for path in sorted(relevant_paths, key=lambda p: (-populated[p], p.lower())):
        count = populated[path]
        pct = round(count / total * 100, 1) if total else 0
        print(f"{path}: {count}/{total} ({pct}%) types={dict(types[path])}")
        if examples[path]:
            print("  examples:", examples[path])

    groups = {
        "DOB": ("birth", "dob"),
        "COUNTRY_NATIONALITY": ("country", "nation"),
        "RANKING": ("rank", "p4p", "champion"),
        "BIO": ("bio", "description"),
        "PHOTOS": ("image", "photo", "headshot", "body"),
        "PHYSICAL": ("height", "weight", "reach", "stance"),
        "TRAINING_STYLE": ("train", "style"),
        "RECORD_FINISHES": ("record", "win", "loss", "draw", "knockout", "submission", "finish"),
        "FIGHT_STATS": ("strike", "takedown", "accuracy", "defense", "grapple", "stat"),
        "SOCIAL": ("social", "twitter", "instagram", "facebook"),
    }

    grouped = {}
    for group_name, terms in groups.items():
        paths = [p for p in all_paths if any(term in p.lower() for term in terms)]
        grouped[group_name] = []
        for path in sorted(paths, key=lambda p: (-populated[p], p.lower())):
            grouped[group_name].append({
                "path": path,
                "count": populated[path],
                "percent": round(populated[path] / total * 100, 1) if total else 0,
                "types": dict(types[path]),
                "examples": examples[path],
            })

    report = {
        "meta": meta,
        "fighters": total,
        "unique_paths": len(all_paths),
        "relevant_paths": len(relevant_paths),
        "groups": grouped,
        "all_paths": [
            {
                "path": path,
                "present_count": populated[path],
                "percent": round(populated[path] / total * 100, 1) if total else 0,
                "types": dict(types[path]),
                "examples": examples[path],
            }
            for path in all_paths
        ],
    }

    with open("cito_deep_fields_v3_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\nReport written to cito_deep_fields_v3_report.json")
    print("DISCOVERY ONLY: no Supabase data was modified.")

if __name__ == "__main__":
    main()
