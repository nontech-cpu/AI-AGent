
import os
import time
import json
import hmac
import base64
import hashlib
from urllib.parse import urlencode, urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from dotenv import load_dotenv

load_dotenv()

GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
GOOGLE_TOKEN_KEY = os.getenv("GOOGLE_TOKEN_KEY", "").strip()
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "").strip()
PUBLIC_BASE_URL = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

GOOGLE_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/drive.file",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/gmail.send",
]

STATE_MAX_AGE = 600  # 10 minutes


def clean_public_base_url(url: str | None = None) -> str:
    """Normalize and sanitize the public base URL.

    Strips any accidental query parameters (e.g. ?utm_source=chatgpt.com),
    fragments (#...), or trailing slashes copied from browser address bars.
    """
    raw = (url or os.getenv("PUBLIC_BASE_URL", "") or PUBLIC_BASE_URL).strip()
    if not raw:
        return ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    parsed = urlparse(raw)
    base_path = parsed.path.rstrip("/")
    if base_path.endswith("/google/callback"):
        base_path = base_path[:-len("/google/callback")]
    elif base_path.endswith("/callback"):
        base_path = base_path[:-len("/callback")]

    scheme = parsed.scheme or "https"
    netloc = parsed.netloc
    return f"{scheme}://{netloc}{base_path}".rstrip("/")


def _check_config():
    missing = []

    if not GOOGLE_CLIENT_ID:
        missing.append("GOOGLE_CLIENT_ID")
    if not GOOGLE_CLIENT_SECRET:
        missing.append("GOOGLE_CLIENT_SECRET")
    if not GOOGLE_TOKEN_KEY:
        missing.append("GOOGLE_TOKEN_KEY")
    if not WEBHOOK_SECRET:
        missing.append("WEBHOOK_SECRET")
    if not clean_public_base_url():
        missing.append("PUBLIC_BASE_URL")

    if missing:
        raise RuntimeError(
            "Missing environment variables: " + ", ".join(missing)
        )


def _fernet():
    _check_config()

    try:
        return Fernet(GOOGLE_TOKEN_KEY.encode("utf-8"))
    except Exception as exc:
        raise RuntimeError(
            "GOOGLE_TOKEN_KEY must be a valid Fernet key. "
            "Generate one using: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        ) from exc


def encrypt_token(token: str) -> str:
    """Encrypt a Google refresh token before saving it in the database."""
    if not token:
        raise ValueError("Cannot encrypt an empty token.")

    return _fernet().encrypt(token.encode("utf-8")).decode("utf-8")


def decrypt_token(encrypted_token: str) -> str:
    """Decrypt a refresh token retrieved from the database."""
    try:
        return _fernet().decrypt(
            encrypted_token.encode("utf-8")
        ).decode("utf-8")
    except InvalidToken as exc:
        raise ValueError(
            "Token decryption failed. Check GOOGLE_TOKEN_KEY."
        ) from exc


def callback_url() -> str:
    """Must exactly match the Google Cloud OAuth redirect URI."""
    _check_config()
    clean_base = clean_public_base_url()
    return f"{clean_base}/google/callback"


def _state_signature(payload: str) -> str:
    return hmac.new(
        WEBHOOK_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def create_state(user_id: int, source: str = "tg", return_url: str = None) -> str:
    """Create a signed OAuth state containing the Telegram user ID and optional metadata."""
    _check_config()

    data = {
        "user_id": int(user_id),
        "created_at": int(time.time()),
        "source": str(source),
    }
    if return_url:
        data["return_url"] = str(return_url)

    raw = json.dumps(
        data, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")

    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    signature = _state_signature(payload)

    return f"{payload}.{signature}"


def verify_state_payload(state: str) -> dict:
    """Validate OAuth state signature and timestamp, and return the decoded payload dictionary."""
    if not state or "." not in state:
        raise ValueError("Invalid OAuth state.")

    payload, supplied_signature = state.rsplit(".", 1)
    expected_signature = _state_signature(payload)

    if not hmac.compare_digest(supplied_signature, expected_signature):
        raise ValueError("OAuth state signature is invalid.")

    padded_payload = payload + "=" * (-len(payload) % 4)

    try:
        data = json.loads(
            base64.urlsafe_b64decode(padded_payload.encode("ascii"))
        )
        user_id = int(data["user_id"])
        created_at = int(data["created_at"])
    except Exception as exc:
        raise ValueError("OAuth state payload is invalid.") from exc

    age = int(time.time()) - created_at

    if age < 0 or age > STATE_MAX_AGE:
        raise ValueError("OAuth link expired. Request a new link.")

    return data


def verify_state(state: str) -> int:
    """Validate the OAuth state and return its Telegram user ID."""
    return verify_state_payload(state)["user_id"]


def build_auth_url(user_id: int, source: str = "tg", return_url: str = None) -> str:
    """Generate the Google consent URL for a user."""
    _check_config()

    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": callback_url(),
        "response_type": "code",
        "scope": " ".join(GOOGLE_SCOPES),
        "access_type": "offline",
        "prompt": "consent",
        "include_granted_scopes": "true",
        "state": create_state(user_id, source=source, return_url=return_url),
    }

    return f"{GOOGLE_AUTH_URL}?{urlencode(params)}"


async def exchange_code(code: str) -> dict:
    """Exchange the callback authorization code for Google tokens."""
    _check_config()

    if not code:
        raise ValueError("Google did not provide an authorization code.")

    payload = {
        "code": code,
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "redirect_uri": callback_url(),
        "grant_type": "authorization_code",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data=payload)
        response.raise_for_status()
        tokens = response.json()

    if not tokens.get("access_token"):
        raise RuntimeError("Google did not return an access token.")

    return tokens


async def get_google_email(access_token: str) -> str:
    """Fetch the email address of the connected Google account."""
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        profile = response.json()

    email = profile.get("email")

    if not email:
        raise RuntimeError("Could not retrieve the Google account email.")

    return email


async def refresh_access_token(refresh_token: str) -> dict:
    """Get a fresh access token using a stored refresh token."""
    _check_config()

    if not refresh_token:
        raise ValueError("Google refresh token is missing.")

    payload = {
        "client_id": GOOGLE_CLIENT_ID,
        "client_secret": GOOGLE_CLIENT_SECRET,
        "refresh_token": refresh_token,
        "grant_type": "refresh_token",
    }

    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.post(GOOGLE_TOKEN_URL, data=payload)
        response.raise_for_status()
        tokens = response.json()

    if not tokens.get("access_token"):
        raise RuntimeError("Google did not return a refreshed access token.")

    return tokens
