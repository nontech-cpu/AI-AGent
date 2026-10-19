import os
import httpx
from dotenv import load_dotenv

load_dotenv()

r = httpx.get(
    "https://api.groq.com/openai/v1/models",
    headers={"Authorization": f"Bearer {os.getenv('GROQ_API_KEY')}"},
)
r.raise_for_status()
for m in r.json()["data"]:
    print(m["id"])