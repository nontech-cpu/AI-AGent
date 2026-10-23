"""
Comprehensive tests for Notion OAuth 2.0 Web Flow,
Structured Block Parsing (Headings, Bullets, Checklists),
and Intelligent Section Appending (Default vs Existing Sections).
"""
import pytest
import time
import json
from unittest.mock import AsyncMock, patch, MagicMock

import notion_auth
from integrations import parse_content_to_notion_blocks, create_or_append_notion_note, create_notion_page
from connectors.notion import NotionConnector
from connectors.registry import get_connector


def test_notion_auth_url_and_state():
    with patch.dict("os.environ", {
        "NOTION_CLIENT_ID": "mock_notion_client_123",
        "NOTION_CLIENT_SECRET": "mock_notion_secret_456",
        "PUBLIC_BASE_URL": "https://peculiar-ai-backend.onrender.com"
    }):
        assert notion_auth.is_notion_oauth_configured() is True
        url = notion_auth.build_notion_auth_url(1234828592, source="tg")
        assert "https://api.notion.com/v1/oauth/authorize" in url
        assert "client_id=mock_notion_client_123" in url
        assert "response_type=code" in url
        assert "state=" in url

        # Extract state and verify
        state = url.split("state=")[1].split("&")[0]
        data = notion_auth.verify_state_payload(state)
        assert data["user_id"] == 1234828592
        assert data["source"] == "tg"


def test_notion_state_tampering_rejected():
    with patch.dict("os.environ", {"WEBHOOK_SECRET": "test_secret"}):
        state = notion_auth.create_state(999, source="web")
        data = notion_auth.verify_state_payload(state)
        assert data["user_id"] == 999

        # Tampered state
        tampered = state[:-4] + "abcd"
        with pytest.raises(ValueError, match="signature is invalid"):
            notion_auth.verify_state_payload(tampered)


def test_parse_content_to_notion_blocks_headings_and_bullets():
    raw_text = """
# Main Strategy Title
## Sprint Goals
- Complete GitHub OAuth
- Connect Notion sections
• Review Google Meet
1. First step
2. Second step
[ ] Check error logs
[x] Done task
---
General notes paragraph here.
"""
    blocks = parse_content_to_notion_blocks(raw_text)
    types = [b["type"] for b in blocks]

    assert "heading_1" in types
    assert "heading_2" in types
    assert "bulleted_list_item" in types
    assert "numbered_list_item" in types
    assert "to_do" in types
    assert "divider" in types
    assert "paragraph" in types

    # Verify heading content
    h1_block = next(b for b in blocks if b["type"] == "heading_1")
    assert h1_block["heading_1"]["rich_text"][0]["text"]["content"] == "Main Strategy Title"

    # Verify bullet content
    bullet_blocks = [b for b in blocks if b["type"] == "bulleted_list_item"]
    assert len(bullet_blocks) == 3
    assert bullet_blocks[0]["bulleted_list_item"]["rich_text"][0]["text"]["content"] == "Complete GitHub OAuth"

    # Verify to_do checked status
    todo_blocks = [b for b in blocks if b["type"] == "to_do"]
    assert todo_blocks[0]["to_do"]["checked"] is False
    assert todo_blocks[1]["to_do"]["checked"] is True


def test_parse_content_force_bullets():
    plain_lines = "Feature A\nFeature B\nFeature C"
    blocks = parse_content_to_notion_blocks(plain_lines, default_formatting="bullets")
    assert len(blocks) == 3
    assert all(b["type"] == "bulleted_list_item" for b in blocks)


@pytest.mark.asyncio
async def test_notion_appends_to_default_notes_section():
    """When user does not specify target_section, it should find default Notes page and append."""
    mock_search_results = {
        "results": [
            {
                "id": "page_notes_101",
                "object": "page",
                "url": "https://notion.so/page-notes-101",
                "parent": {"type": "workspace"},
                "properties": {
                    "title": {"title": [{"plain_text": "PeculiarAI Notes"}]}
                }
            }
        ]
    }

    with patch("httpx.AsyncClient.post") as mock_post, \
         patch("httpx.AsyncClient.patch") as mock_patch:

        # Mock search response
        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = mock_search_results
        mock_post.return_value = mock_post_resp

        # Mock patch (append blocks) response
        mock_patch_resp = MagicMock()
        mock_patch_resp.status_code = 200
        mock_patch_resp.json.return_value = {"results": []}
        mock_patch.return_value = mock_patch_resp

        ok, msg, url = await create_or_append_notion_note(
            token="secret_mock_123",
            title="Meeting Action Items",
            content="- Item 1\n- Item 2",
            target_section=None
        )

        assert ok is True
        assert "PeculiarAI Notes" in msg
        assert url == "https://notion.so/page-notes-101"
        assert mock_patch.called
        patch_payload = mock_patch.call_args[1]["json"]
        assert any(b["type"] == "heading_2" for b in patch_payload["children"])


@pytest.mark.asyncio
async def test_notion_appends_to_explicit_existing_section():
    """When user explicitly specifies target_section, it appends to that page."""
    mock_search_results = {
        "results": [
            {
                "id": "page_project_202",
                "object": "page",
                "url": "https://notion.so/project-202",
                "parent": {"type": "workspace"},
                "properties": {
                    "title": {"title": [{"plain_text": "Project Roadmap"}]}
                }
            }
        ]
    }

    with patch("httpx.AsyncClient.post") as mock_post, \
         patch("httpx.AsyncClient.patch") as mock_patch:

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = mock_search_results
        mock_post.return_value = mock_post_resp

        mock_patch_resp = MagicMock()
        mock_patch_resp.status_code = 200
        mock_patch_resp.json.return_value = {"results": []}
        mock_patch.return_value = mock_patch_resp

        ok, msg, url = await create_or_append_notion_note(
            token="secret_mock_123",
            title="Sprint 3 Notes",
            content="# Sprint 3\n- Feature X",
            target_section="Project Roadmap"
        )

        assert ok is True
        assert "Project Roadmap" in msg
        assert url == "https://notion.so/project-202"
        assert mock_patch.called


@pytest.mark.asyncio
async def test_notion_connector_contract_execution():
    """Verifies NotionConnector.execute passes all arguments properly and returns ToolResult."""
    conn = get_connector("notion")
    assert conn is not None

    with patch("db.get_user_integration", return_value={"api_key": "secret_valid_token_123"}), \
         patch("integrations.create_or_append_notion_note", new_callable=AsyncMock) as mock_create:

        mock_create.return_value = (True, "Notes appended successfully!", "https://notion.so/test-url")

        res = await conn.execute(
            user_or_chat_id=1234828592,
            tool_name="create_notion_page",
            args={
                "title": "Testing Title",
                "content": "- Bullet point 1",
                "target_section": "existing notes",
                "formatting": "bullets"
            }
        )

        assert res.ok is True
        assert res.data["status"] == "success"
        assert res.data["url"] == "https://notion.so/test-url"
        mock_create.assert_called_once_with(
            token="secret_valid_token_123",
            title="Testing Title",
            content="- Bullet point 1",
            target_section="existing notes",
            formatting="bullets"
        )
