"""
Tests for FastAPI HTTP endpoints:
- Root & health check
- Web verification flows
- Google OAuth diagnostics
- Telegram webhook authorization and deduplication
- Response security headers
"""
import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
import main


def test_root_endpoint(client: TestClient):
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"
    assert "app" in data
    # Verify security headers
    assert response.headers.get("x-content-type-options") == "nosniff"
    assert response.headers.get("x-frame-options") == "SAMEORIGIN"


def test_health_endpoint(client: TestClient):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_google_diagnostic_endpoint(client: TestClient):
    response = client.get("/web/google/diagnostic")
    assert response.status_code == 200
    data = response.json()
    assert data.get("status") == "ok"
    assert "https://peculiar-ai-backend.onrender.com/google/callback" in data.get("redirect_uri", "")


def test_web_verification_start_validation(client: TestClient):
    # Invalid phone format
    res_bad = client.post("/web/verification/start", json={"phone": "invalid-phone"})
    assert res_bad.status_code == 400

    # Valid phone format
    res_good = client.post("/web/verification/start", json={"phone": "+919876543210"})
    assert res_good.status_code == 200
    data = res_good.json()
    assert "verification_id" in data
    assert "telegram_url" in data


def test_telegram_webhook_unauthorized(client: TestClient):
    # Missing secret header
    res_missing = client.post("/telegram/webhook", json={"update_id": 100})
    assert res_missing.status_code == 403

    # Incorrect secret header
    res_invalid = client.post(
        "/telegram/webhook",
        json={"update_id": 101},
        headers={"x-telegram-bot-api-secret-token": "wrong_secret_token"},
    )
    assert res_invalid.status_code == 403


def test_telegram_webhook_deduplication(client: TestClient):
    secret = main.SECRET
    update_payload = {
        "update_id": 999999,
        "message": {
            "chat": {"id": 12345678},
            "text": "ping",
        },
    }
    headers = {"x-telegram-bot-api-secret-token": secret}

    with patch("main.handle_user_text", new_callable=AsyncMock) as mock_handle:
        # First delivery
        res1 = client.post("/telegram/webhook", json=update_payload, headers=headers)
        assert res1.status_code == 200
        assert mock_handle.call_count == 1

        # Second delivery (duplicate Telegram retry)
        res2 = client.post("/telegram/webhook", json=update_payload, headers=headers)
        assert res2.status_code == 200
        # Should NOT process again
        assert mock_handle.call_count == 1
