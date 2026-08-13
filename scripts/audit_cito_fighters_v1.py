import os
import json
import requests

CITO_API_KEY = os.environ["CITO_API_KEY"]

URL = "https://api.citoapi.com/api/v1/ufc/fighters?page=1&limit=50"

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

print("\n===== TOP LEVEL =====")
if isinstance(data, dict):
    print("Keys:", list(data.keys()))
else:
    print("Type:", type(data).__name__)

print("\n===== RAW RESPONSE =====")
print(json.dumps(data, indent=2, ensure_ascii=False))
