"""
Security and lifecycle tests for Google OAuth2, HMAC state integrity, token encryption, and URL sanitization.
"""
import time
import pytest
import google_auth


def test_clean_public_base_url_sanitization():
    # Strips query strings copied from chatgpt or browser
    malformed = "https://peculiar-ai-backend.onrender.com?utm_source=chatgpt.com/google/callback"
    cleaned = google_auth.clean_public_base_url(malformed)
    assert cleaned == "https://peculiar-ai-backend.onrender.com"

    # Strips trailing slashes
    trailing = "https://peculiar-ai-backend.onrender.com///"
    assert google_auth.clean_public_base_url(trailing) == "https://peculiar-ai-backend.onrender.com"

    # Callback URL construction
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
