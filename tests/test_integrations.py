import pytest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient
from main import app
from crypto import encrypt_aes256_gcm, decrypt_any
from integrations import validate_notion_token, validate_github_token


def test_integration_token_encryption():
    """Verifies AES-256-GCM encryption and decryption roundtrip for integration tokens."""
    token = "secret_mock_notion_key_xyz9876543210"
    encrypted = encrypt_aes256_gcm(token)
    assert encrypted.startswith("aes256gcm$")
    assert encrypted != token

    decrypted = decrypt_any(encrypted)
    assert decrypted == token


@pytest.mark.asyncio
async def test_validate_notion_token_prefix():
    """validate_notion_token must reject tokens not starting with 'secret_'."""
    is_valid, msg = await validate_notion_token("invalid_token_format")
    assert is_valid is False
    assert "secret_" in msg


def test_web_integrations_endpoints():
    """Test web integration status, save, and disconnect endpoints with mocked session."""
    client = TestClient(app)

    with patch("main._resolve_web_session", return_value=777888):
        with patch("main.ensure_user"):
            # 1. Status
            with patch("main.get_user_integrations_summary", return_value={
                "notion": {"connected": False, "details": "Not connected"},
                "github": {"connected": False, "details": "Not connected"}
            }):
                res = client.post("/web/integrations/status", json={"verification_id": "test_tok"})
                assert res.status_code == 200
                data = res.json()
                assert "integrations" in data
                assert data["integrations"]["notion"]["connected"] is False

            # 2. Save Notion
            with patch("main.validate_notion_token", new_callable=AsyncMock) as mock_val:
                mock_val.return_value = (True, "Test Workspace")
                with patch("main.save_user_integration", return_value=True):
                    res = client.post("/web/integrations/save", json={
                        "verification_id": "test_tok",
                        "service": "notion",
                        "api_key": "secret_valid_key_123"
                    })
                    assert res.status_code == 200
                    saved_data = res.json()
                    assert saved_data["connected"] is True
                    assert saved_data["name"] == "Test Workspace"

            # 3. Disconnect Notion
            with patch("main.delete_user_integration", return_value=True):
                res = client.post("/web/integrations/disconnect", json={
                    "verification_id": "test_tok",
                    "service": "notion"
                })
                assert res.status_code == 200
                assert res.json()["disconnected"] is True
