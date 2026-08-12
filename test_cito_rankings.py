import json
import os
from urllib.request import Request, urlopen

API_URL = "https://api.citoapi.com/api/v1/ufc/rankings"

api_key = os.environ.get("CITO_API_KEY")

if not api_key:
    raise RuntimeError("Missing CITO_API_KEY")

request = Request(
    API_URL,
    headers={
        "x-api-key": api_key,
        "User-Agent": "FightIQ/1.0",
        "Accept": "application/json",
    },
)

with urlopen(request, timeout=30) as response:
    payload = json.loads(response.read().decode("utf-8"))

print("=== CITO UFC RANKINGS TEST ===")
print("HTTP OK")

if isinstance(payload, dict):
    print("Top-level keys:", list(payload.keys()))
elif isinstance(payload, list):
    print("Top-level type: list")
    print("Number of items:", len(payload))

print()
print("=== RESPONSE SAMPLE ===")
print(json.dumps(payload, indent=2, ensure_ascii=False)[:15000])
