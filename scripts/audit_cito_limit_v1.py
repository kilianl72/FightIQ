import os
import json
import requests

CITO_API_KEY = os.environ["CITO_API_KEY"]

URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=5000"

response = requests.get(
    URL,
    headers={
        "x-api-key": CITO_API_KEY,
        "Accept": "application/json",
    },
    timeout=30,
)

print("HTTP status:", response.status_code)
response.raise_for_status()

data = response.json()

fighters = data.get("data", []) if isinstance(data, dict) else []
meta = data.get("meta", {}) if isinstance(data, dict) else {}

print("\n===== V1 CITO LIMIT TEST =====")
print("Requested limit: 5000")
print("Returned fighters:", len(fighters))
print("Meta page:", meta.get("page"))
print("Meta limit:", meta.get("limit"))
print("Meta total:", meta.get("total"))
print("Meta totalPages:", meta.get("totalPages"))
print("Meta hasNextPage:", meta.get("hasNextPage"))

print("\n===== META RAW =====")
print(json.dumps(meta, indent=2, ensure_ascii=False))
