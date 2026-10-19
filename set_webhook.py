
import os
import re
import httpx
from dotenv import load_dotenv

load_dotenv()

token = os.getenv("TELEGRAM_BOT_TOKEN")
secret = os.getenv("WEBHOOK_SECRET")
base_url = "https://tightly-barbell-creamed.ngrok-free.dev"

if not token or not secret:
    raise SystemExit(
        "Missing TELEGRAM_BOT_TOKEN or WEBHOOK_SECRET in .env"
    )

if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", secret):
    raise SystemExit(
        "WEBHOOK_SECRET must contain only letters, numbers, _ or -"
    )

response = httpx.post(
    f"https://api.telegram.org/bot{token}/setWebhook",
    data={
        "url": f"{base_url}/telegram/webhook",
        "secret_token": secret,
    },
    timeout=20,
)

result = response.json()
print("Webhook setup:", result.get("description", "No description"))
print("Successful:", result.get("ok", False))
