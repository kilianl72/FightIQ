import json, os
from urllib.request import Request, urlopen

URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"

def present(v):
    return v is not None and (not isinstance(v, str) or bool(v.strip()))

def dob(f):
    if present(f.get("birthDate")):
        return f["birthDate"]
    graph = (((f.get("raw") or {}).get("jsonLd") or {}).get("@graph") or [])
    for x in graph:
        if isinstance(x, dict):
            e = x.get("mainEntity")
            if isinstance(e, dict) and present(e.get("birthDate")):
                return e["birthDate"]

def main():
    req = Request(URL, headers={
        "x-api-key": os.environ["CITO_API_KEY"],
        "User-Agent": "FightIQ/4.0",
        "Accept": "application/json"
    })
    with urlopen(req, timeout=120) as r:
        data = json.loads(r.read().decode())["data"]

    active = [f for f in data if f.get("isActive") is True]
    n = len(active)

    fields = {
        "ufcStatsId": lambda f: f.get("ufcStatsId"),
        "nickname": lambda f: f.get("nickname"),
        "division": lambda f: f.get("division"),
        "record": lambda f: f.get("record") or f.get("recordText"),
        "placeOfBirth": lambda f: f.get("placeOfBirth"),
        "DOB (best path)": dob,
        "height": lambda f: f.get("heightInches"),
        "weight": lambda f: f.get("weightLbs"),
        "reach": lambda f: f.get("reachInches"),
        "legReach": lambda f: f.get("legReachInches"),
        "stance": lambda f: f.get("stance"),
        "octagonDebut": lambda f: f.get("octagonDebut"),
        "proxiedImageUrl": lambda f: f.get("proxiedImageUrl"),
        "stats object": lambda f: f.get("stats"),
    }

    print("===== V4 ACTIVE FIGHTERS COVERAGE =====")
    print("All Cito fighters:", len(data))
    print("Active fighters:", n)
    print("\n===== PROFILE / DATA COVERAGE =====")
    report = {}
    for name, getter in fields.items():
        c = sum(1 for f in active if present(getter(f)))
        report[name] = {"count": c, "percent": round(c/n*100, 1)}
        print(f"{name}: {c}/{n} ({c/n*100:.1f}%)")

    # Discover every populated stats path among active fighters instead of assuming key names.
    def walk(v, path="stats"):
        if isinstance(v, dict):
            for k, child in v.items():
                p = f"{path}.{k}"
                yield p, child
                yield from walk(child, p)
        elif isinstance(v, list):
            for child in v:
                yield from walk(child, path+"[]")

    counts = {}
    examples = {}
    for f in active:
        seen = set()
        for path, value in walk(f.get("stats") or {}):
            if path not in seen and present(value):
                counts[path] = counts.get(path, 0) + 1
                seen.add(path)
                if path not in examples and not isinstance(value, (dict, list)):
                    examples[path] = {"fighter": f.get("name"), "value": value}

    print("\n===== ALL ACTIVE-FIGHTER STATS PATHS =====")
    for path, c in sorted(counts.items(), key=lambda x: (-x[1], x[0])):
        print(f"{path}: {c}/{n} ({c/n*100:.1f}%) example={examples.get(path)}")

    out = {
        "all_cito_fighters": len(data),
        "active_fighters": n,
        "profile_coverage": report,
        "stats_paths": [
            {"path": p, "count": c, "percent": round(c/n*100,1), "example": examples.get(p)}
            for p,c in sorted(counts.items(), key=lambda x:(-x[1],x[0]))
        ]
    }
    with open("cito_active_coverage_v4_report.json","w",encoding="utf-8") as f:
        json.dump(out,f,ensure_ascii=False,indent=2)
    print("\nAUDIT ONLY: Supabase was not modified.")

if __name__ == "__main__":
    main()
