"""
Security and lifecycle tests for Google OAuth2, HMAC state integrity, token encryption, and URL sanitization.
"""
import time
import pytest
import google_auth


def test_clean_public_base_url_sanitization(monkeypatch):
    # Strips query strings copied from chatgpt or browser
    malformed = "https://peculiar-ai-backend.onrender.com?utm_source=chatgpt.com/google/callback"
    cleaned = google_auth.clean_public_base_url(malformed)
    assert cleaned == "https://peculiar-ai-backend.onrender.com"

    # Strips trailing slashes
    trailing = "https://peculiar-ai-backend.onrender.com///"
    assert google_auth.clean_public_base_url(trailing) == "https://peculiar-ai-backend.onrender.com"

    # Callback URL construction with explicit public base url
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://peculiar-ai-backend.onrender.com")
    assert google_auth.callback_url() == "https://peculiar-ai-backend.onrender.com/google/callback"


def test_oauth_state_hmac_integrity():
    chat_id = 123456789
    state = google_auth.create_state(chat_id, source="web", return_url="https://myapp.com/dashboard")
    assert "." in state

    # Valid state verifies correctly
    payload = google_auth.verify_state_payload(state)
    assert payload["user_id"] == chat_id
    assert payload["source"] == "web"
    assert payload["return_url"] == "https://myapp.com/dashboard"
    assert google_auth.verify_state(state) == chat_id

    # Tampered state payload fails
    tampered_payload = "eyAidXNlcl9pZCI6IDk5OSB9." + state.split(".")[1]
    with pytest.raises(ValueError, match="signature is invalid"):
        google_auth.verify_state_payload(tampered_payload)

    # Tampered signature fails
    tampered_sig = state[:-4] + "ffff"
    with pytest.raises(ValueError, match="signature is invalid"):
        google_auth.verify_state_payload(tampered_sig)


def test_oauth_state_expiration():
    chat_id = 987654321
    # Create state with expired timestamp
    state = google_auth.create_state(chat_id)
    # Monkey-patch verify_state_payload logic with an artificially old state
    old_data = {"user_id": chat_id, "created_at": int(time.time()) - 1000, "source": "tg"}
    import json, base64
    raw = json.dumps(old_data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    payload = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
    sig = google_auth._state_signature(payload)
    old_state = f"{payload}.{sig}"

    with pytest.raises(ValueError, match="OAuth link expired"):
        google_auth.verify_state_payload(old_state)


def test_fernet_token_encryption_and_isolation():
    raw_token = "1//04AbCdEfGhIjKlMnOpQrStUvWxYz_1234567890"

    # Encrypt token
    encrypted = google_auth.encrypt_token(raw_token)
    assert encrypted != raw_token
    assert isinstance(encrypted, str)

    # Decrypt token
    decrypted = google_auth.decrypt_token(encrypted)
    assert decrypted == raw_token

    # Invalid ciphertext fails cleanly
    with pytest.raises(ValueError, match="decryption failed"):
        google_auth.decrypt_token("invalid_encrypted_ciphertext_string")

    # Empty token fails validation
    with pytest.raises(ValueError, match="Cannot encrypt an empty token"):
        google_auth.encrypt_token("")


def test_github_oauth_state_hmac_integrity():
    import github_auth
    chat_id = 998877665
    state = github_auth.create_state(chat_id, source="tg")
    assert "." in state

    payload = github_auth.verify_state_payload(state)
    assert payload["user_id"] == chat_id
    assert payload["source"] == "tg"

    # Tampered signature fails
    tampered_sig = state[:-4] + "0000"
    with pytest.raises(ValueError, match="signature is invalid"):
        github_auth.verify_state_payload(tampered_sig)


def test_github_auth_url_construction():
    import github_auth
    from unittest.mock import patch

    with patch.dict("os.environ", {"GITHUB_CLIENT_ID": "mock_gh_client_123"}):
        url = github_auth.build_github_auth_url(123456, source="tg")
        assert "https://github.com/login/oauth/authorize" in url
        assert "client_id=mock_gh_client_123" in url
        assert "scope=repo+read%3Auser" in url or "scope=repo%20read%3Auser" in url
        assert "state=" in url


@pytest.mark.asyncio
async def test_github_callback_endpoint():
    from unittest.mock import patch, AsyncMock
    from fastapi.testclient import TestClient
    from main import app
    import github_auth

    client = TestClient(app)
    chat_id = 77112233
    state = github_auth.create_state(chat_id, source="tg")

    mock_token_data = {"access_token": "gho_mock_access_token_12345", "scope": "repo,read:user"}
    mock_profile_data = {"login": "testdev", "html_url": "https://github.com/testdev", "public_repos": 10}

    with patch("github_auth.exchange_github_code", new_callable=AsyncMock, return_value=mock_token_data), \
         patch("github_auth.get_github_user_profile", new_callable=AsyncMock, return_value=mock_profile_data), \
         patch("main.save_user_integration") as mock_save:

        res = client.get(f"/auth/github/callback?code=mock_code&state={state}")
        assert res.status_code == 200
        assert "GitHub connected successfully" in res.text
        assert "@testdev" in res.text
        mock_save.assert_called_once()



@pytest.mark.asyncio
async def test_oauth_originating_chat_id_routing():
    """
    Verifies that when a tool generates an OAuth URL for a user whose chat ID
    maps to a different canonical ID, the auth URL encodes the originating delivery
    chat ID so callback notifications reach the active chat session.
    """
    from agent.tools import execute_tool
    import google_auth

    delivery_chat = 1234828592
    canonical_chat = 1758056012

    # Mock get_valid_google_connection_by_chat_id to return disconnected
    from unittest.mock import patch, AsyncMock
    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock, return_value=(False, None)):
        res = await execute_tool(
            chat_id=canonical_chat,
            tool_name="connect_integration",
            arguments={"service": "google"},
            user_context={"delivery_chat_id": delivery_chat, "originating_chat_id": delivery_chat}
        )
        assert res["status"] == "success"
        auth_url = res["auth_url"]
        assert "state=" in auth_url

        # Extract and verify the state parameter
        from urllib.parse import urlparse, parse_qs
        qs = parse_qs(urlparse(auth_url).query)
        state = qs["state"][0]
        state_data = google_auth.verify_state_payload(state)
        # MUST contain the active delivery chat ID, NOT the canonical ID!
        assert state_data["user_id"] == delivery_chat


@pytest.mark.asyncio
async def test_google_callback_routes_notification_to_originating_chat():
    """
    Verifies that when Google OAuth callback fires, the Telegram confirmation message
    is dispatched to the originating chat ID (1234828592), not diverted to a different account.
    """
    from unittest.mock import patch, AsyncMock, MagicMock
    from fastapi.testclient import TestClient
    from main import app
    import google_auth

    delivery_chat = 1234828592
    canonical_chat = 1758056012
    state = google_auth.create_state(delivery_chat, source="tg")

    mock_tokens = {
        "access_token": "ya29.mock_access_token",
        "refresh_token": "1//mock_refresh_token",
        "scope": "openid email"
    }

    client = TestClient(app)

    captured_tg_posts = []

    class MockAsyncClient:
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def post(self, url, json=None, **kwargs):
            captured_tg_posts.append({"url": url, "json": json})
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = {"ok": True}
            return resp

    with patch("main.exchange_code", new_callable=AsyncMock, return_value=mock_tokens),          patch("main.get_google_email", new_callable=AsyncMock, return_value="neerav@test.com"),          patch("main.has_consent", return_value=True),          patch("httpx.AsyncClient", return_value=MockAsyncClient()),          patch.dict("os.environ", {"TELEGRAM_BOT_TOKEN": "mock_tg_token_123"}):

        res = client.get(f"/google/callback?code=mock_code_123&state={state}")
        assert res.status_code == 200
        assert "Google connected successfully" in res.text

        # Verify that Telegram message was sent to delivery_chat
        assert len(captured_tg_posts) >= 1
        tg_post = captured_tg_posts[0]
        assert tg_post["json"]["chat_id"] == delivery_chat
        assert "Google account successfully connect ho gaya" in tg_post["json"]["text"]
