"""
PeculiarAI — GitHub OAuth 2.0 Web Flow
Generates 1-click authorization links requesting repo & user permissions,
verifies HMAC-signed state, and exchanges authorization codes for access tokens.
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

GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "").strip()
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

GITHUB_AUTH_URL = "https://github.com/login/oauth/authorize"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"

# Scopes: repo (full control of repositories: read, write, commit), read:user (profile info)
GITHUB_SCOPES = ["repo", "read:user"]

STATE_MAX_AGE = 600  # 10 minutes


def is_github_oauth_configured() -> bool:
    """Returns True if GITHUB_CLIENT_ID and GITHUB_CLIENT_SECRET are configured."""
    cid = GITHUB_CLIENT_ID or os.getenv("GITHUB_CLIENT_ID", "").strip()
    csec = GITHUB_CLIENT_SECRET or os.getenv("GITHUB_CLIENT_SECRET", "").strip()
    return bool(cid and csec)


def clean_public_base_url(url: Optional[str] = None) -> str:
    raw = (url or os.getenv("PUBLIC_BASE_URL", "") or PUBLIC_BASE_URL).strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    base_path = parsed.path.rstrip("/")
    for suff in ["/auth/github/callback", "/github/callback", "/callback"]:
        if base_path.endswith(suff):
            base_path = base_path[:-len(suff)]
            break

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    return f"{scheme}://{netloc}{base_path}".rstrip("/")


def github_callback_url() -> str:
    base = clean_public_base_url()
    if not base:
        return "http://localhost:8001/auth/github/callback"
    return f"{base}/auth/github/callback"


def _state_signature(payload: str) -> str:
    secret = (WEBHOOK_SECRET or os.getenv("WEBHOOK_SECRET", "") or "peculiar_github_oauth_fallback_secret").encode("utf-8")
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


def build_github_auth_url(user_id: int, source: str = "tg", return_url: Optional[str] = None) -> str:
    """Generate the GitHub 1-click authorization URL requesting repo write permissions."""
    client_id = GITHUB_CLIENT_ID or os.getenv("GITHUB_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError("GITHUB_CLIENT_ID is not configured in .env.")

    params = {
        "client_id": client_id,
        "redirect_uri": github_callback_url(),
        "scope": " ".join(GITHUB_SCOPES),
        "state": create_state(user_id, source=source, return_url=return_url),
        "allow_signup": "true",
    }
    return f"{GITHUB_AUTH_URL}?{urlencode(params)}"


async def exchange_github_code(code: str) -> Dict[str, Any]:
    """Exchanges GitHub authorization code for access token."""
    client_id = GITHUB_CLIENT_ID or os.getenv("GITHUB_CLIENT_ID", "").strip()
    client_secret = GITHUB_CLIENT_SECRET or os.getenv("GITHUB_CLIENT_SECRET", "").strip()

    if not client_id or not client_secret:
        raise RuntimeError("GitHub OAuth Client ID or Secret missing.")

    payload = {
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "redirect_uri": github_callback_url(),
    }
    headers = {
        "Accept": "application/json",
        "User-Agent": "PeculiarAI",
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        res = await client.post(GITHUB_TOKEN_URL, data=payload, headers=headers)
        if res.status_code != 200:
            raise RuntimeError(f"GitHub token exchange failed: {res.text}")

        data = res.json()
        if "error" in data:
            raise RuntimeError(f"GitHub error: {data.get('error_description') or data['error']}")

        return data


async def get_github_user_profile(access_token: str) -> Dict[str, Any]:
    """Fetches user profile details for the authenticated GitHub user."""
    headers = {
        "Authorization": f"Bearer {access_token.strip()}",
        "User-Agent": "PeculiarAI",
        "Accept": "application/vnd.github.v3+json",
    }
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(GITHUB_USER_URL, headers=headers)
        if res.status_code == 200:
            return res.json()
        raise RuntimeError(f"Failed to fetch GitHub profile: {res.status_code}")
