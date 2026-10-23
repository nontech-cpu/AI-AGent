"""
PeculiarAI — Notion OAuth 2.0 Web Flow
Generates 1-click authorization links for Notion workspaces,
verifies HMAC-signed state, and exchanges authorization codes for Notion integration tokens.
"""
import os
import time
import json
import hmac
import base64
import hashlib
from urllib.parse import urlencode, urlparse
from typing import Optional, Dict, Any

import httpx
from dotenv import load_dotenv

load_dotenv()

NOTION_CLIENT_ID = os.getenv("NOTION_CLIENT_ID", "").strip()
NOTION_CLIENT_SECRET = os.getenv("NOTION_CLIENT_SECRET", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

NOTION_AUTH_URL = "https://api.notion.com/v1/oauth/authorize"
NOTION_TOKEN_URL = "https://api.notion.com/v1/oauth/token"

STATE_MAX_AGE = 600  # 10 minutes


def is_notion_oauth_configured() -> bool:
    """Returns True if NOTION_CLIENT_ID and NOTION_CLIENT_SECRET are configured."""
    cid = os.getenv("NOTION_CLIENT_ID", "").strip() or NOTION_CLIENT_ID
    csec = os.getenv("NOTION_CLIENT_SECRET", "").strip() or NOTION_CLIENT_SECRET
    return bool(cid and csec)


def clean_public_base_url(url: Optional[str] = None) -> str:
    raw = (url or os.getenv("PUBLIC_BASE_URL", "") or PUBLIC_BASE_URL).strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    base_path = parsed.path.rstrip("/")
    for suff in ["/auth/notion/callback", "/notion/callback", "/callback"]:
        if base_path.endswith(suff):
            base_path = base_path[:-len(suff)]
            break

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    return f"{scheme}://{netloc}{base_path}".rstrip("/")


def notion_callback_url() -> str:
    base = clean_public_base_url()
    if not base:
        return "http://localhost:8001/auth/notion/callback"
    return f"{base}/auth/notion/callback"


def _state_signature(payload: str) -> str:
    secret = (WEBHOOK_SECRET or os.getenv("WEBHOOK_SECRET", "") or "peculiar_notion_oauth_fallback_secret").encode("utf-8")
    return hmac.new(
        secret,
        payload.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()


def create_state(user_id: int, source: str = "tg", return_url: Optional[str] = None) -> str:
    data = {
        "user_id": int(user_id),
        "created_at": int(time.time()),
        "source": str(source),
    }
    if return_url:
        data["return_url"] = str(return_url)

    raw = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = _state_signature(payload)
    return f"{payload}.{signature}"


def verify_state_payload(state: str) -> dict:
    if not state or "." not in state:
        raise ValueError("Invalid OAuth state.")

    payload, supplied_signature = state.rsplit(".", 1)
    expected_signature = _state_signature(payload)

    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("OAuth state signature is invalid.")

    padded_payload = payload + "=" * (-len(payload) % 4)
    try:
        data = json.loads(base64.urlsafe_b64decode(padded_payload.encode("ascii")))
        user_id = int(data["user_id"])
        created_at = int(data["created_at"])
    except Exception as exc:
        raise ValueError("OAuth state payload is invalid.") from exc

    age = int(time.time()) - created_at
    if age < 0 or age > STATE_MAX_AGE:
        raise ValueError("OAuth link expired. Request a new link.")

    return data


def build_notion_auth_url(user_id: int, source: str = "tg", return_url: Optional[str] = None) -> str:
    """Generate the official Notion 1-click authorization URL."""
    client_id = os.getenv("NOTION_CLIENT_ID", "").strip() or NOTION_CLIENT_ID
    if not client_id:
        raise RuntimeError("NOTION_CLIENT_ID is not configured in .env.")

    params = {
        "client_id": client_id,
        "response_type": "code",
        "owner": "user",
        "redirect_uri": notion_callback_url(),
        "state": create_state(user_id, source=source, return_url=return_url),
    }
    return f"{NOTION_AUTH_URL}?{urlencode(params)}"


async def exchange_notion_code(code: str) -> Dict[str, Any]:
    """Exchanges Notion authorization code for workspace access token."""
    client_id = os.getenv("NOTION_CLIENT_ID", "").strip() or NOTION_CLIENT_ID
    client_secret = os.getenv("NOTION_CLIENT_SECRET", "").strip() or NOTION_CLIENT_SECRET

    if not client_id or not client_secret:
        raise RuntimeError("Notion OAuth Client ID or Secret missing.")

    basic_auth = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
    headers = {
        "Authorization": f"Basic {basic_auth}",
        "Content-Type": "application/json",
        "Notion-Version": "2022-06-28",
    }
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": notion_callback_url(),
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(NOTION_TOKEN_URL, json=payload, headers=headers)
        if res.status_code != 200:
            raise RuntimeError(f"Notion token exchange failed ({res.status_code}): {res.text}")

        data = res.json()
        if "error" in data:
            raise RuntimeError(f"Notion error: {data.get('error')}")

        return data
