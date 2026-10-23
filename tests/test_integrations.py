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


@pytest.mark.asyncio
async def test_get_github_file_autonomous():
    """Verify get_github_file can inspect and read repository files like README.md."""
    import base64
    from integrations import get_github_file
    from unittest.mock import MagicMock

    mock_content = "# Gumming Sheet Price Calculator\nPHP + MySQL rate calculator."
    encoded = base64.b64encode(mock_content.encode("utf-8")).decode("utf-8")

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "type": "file",
        "name": "README.md",
        "path": "README.md",
        "size": len(mock_content),
        "sha": "abc123sha",
        "encoding": "base64",
        "content": encoded,
        "html_url": "https://github.com/nontech-cpu/php-project/blob/main/README.md"
    }

    with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
        ok, res = await get_github_file(username="nontech-cpu", repo="php-project", file_path="README.md")
        assert ok is True
        assert res["exists"] is True
        assert res["name"] == "README.md"
        assert "Gumming Sheet Price Calculator" in res["content"]


@pytest.mark.asyncio
async def test_execute_tool_get_github_file():
    """Verify execute_tool executes get_github_file and resolves repository correctly."""
    from agent.tools import execute_tool

    mock_file_data = {
        "exists": True,
        "type": "file",
        "name": "README.md",
        "path": "README.md",
        "size": 100,
        "repo": "nontech-cpu/php-project",
        "content": "# Test Readme Content",
        "html_url": "https://github.com/nontech-cpu/php-project/blob/main/README.md"
    }

    with patch("integrations.get_github_file", new_callable=AsyncMock, return_value=(True, mock_file_data)), \
         patch("db.get_user_integration", return_value={"api_key": "public_access", "config_data": '{"username": "nontech-cpu"}'}):
        res = await execute_tool(
            1234828592,
            "get_github_file",
            {"repo": "php-project", "path": "README.md"}
        )
        assert res["status"] == "success"
        assert res["exists"] is True
        assert res["name"] == "README.md"
        assert "Test Readme Content" in res["content"]

