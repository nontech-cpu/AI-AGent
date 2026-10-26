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
import integrations
from integrations import parse_content_to_notion_blocks, create_or_append_notion_note, create_notion_page, list_notion_pages
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


@pytest.mark.asyncio
async def test_list_notion_pages_integration():
    """Verifies integrations.list_notion_pages parses pages correctly."""
    mock_search_data = {
        "results": [
            {
                "id": "page-111",
                "properties": {
                    "title": {"title": [{"plain_text": "DSA NOTES"}]}
                },
                "url": "https://notion.so/dsa-notes",
                "last_edited_time": "2026-10-23T20:00:00Z"
            },
            {
                "id": "page-222",
                "properties": {
                    "Name": {"title": [{"plain_text": "Project Roadmap"}]}
                },
                "url": "https://notion.so/project-roadmap",
                "last_edited_time": "2026-10-23T21:00:00Z"
            }
        ]
    }
    with patch("httpx.AsyncClient.post") as mock_post:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = mock_search_data
        mock_post.return_value = resp

        pages = await integrations.list_notion_pages("secret_mock_123")
        assert len(pages) == 2
        assert pages[0]["title"] == "DSA NOTES"
        assert pages[0]["url"] == "https://notion.so/dsa-notes"
        assert pages[1]["title"] == "Project Roadmap"


@pytest.mark.asyncio
async def test_notion_connector_list_pages_contract():
    """Verifies NotionConnector.execute handles list_notion_pages tool."""
    conn = get_connector("notion")
    with patch("db.get_user_integration", return_value={"api_key": "secret_mock_123"}), \
         patch("integrations.list_notion_pages", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = [{"id": "p1", "title": "My Notes", "url": "https://notion.so/p1"}]
        res = await conn.execute(1234828592, "list_notion_pages", {})
        assert res.ok is True
        assert res.data["count"] == 1
        assert "My Notes" in res.data["summary"]


def test_parse_content_markdown_table_to_notion_table():
    """Verifies markdown tables parse into native Notion table blocks with headers."""
    table_text = """
**2. Overfitting vs Underfitting**

| Overfitting | Underfitting |
| --- | --- |
| Training data ko closely learn karta hai | Patterns properly learn nahi karta |
| Training high, test low | Both training and test low |
"""
    blocks = parse_content_to_notion_blocks(table_text)
    types = [b["type"] for b in blocks]
    assert "heading_2" in types
    assert "table" in types

    table_block = next(b for b in blocks if b["type"] == "table")
    assert table_block["table"]["table_width"] == 2
    assert table_block["table"]["has_column_header"] is True
    assert len(table_block["table"]["children"]) == 3  # 1 header + 2 data rows

    header_cells = table_block["table"]["children"][0]["table_row"]["cells"]
    assert header_cells[0][0]["text"]["content"] == "Overfitting"
    assert header_cells[0][0]["annotations"]["bold"] is True


def test_parse_content_wrapped_bold_headings_and_inline_bold():
    """Verifies bold-wrapped titles become headings and inline bold in bullets does not leak asterisks."""
    content = """
**AI & ML Notes**

- **Features (X):** Input variables, jaise age, salary.
- **Target (y):** Output jo predict karna hai.
"""
    blocks = parse_content_to_notion_blocks(content)
    assert blocks[0]["type"] == "heading_1"
    assert blocks[0]["heading_1"]["rich_text"][0]["text"]["content"] == "AI & ML Notes"
    assert blocks[0]["heading_1"]["rich_text"][0]["annotations"]["bold"] is True

    bullet = blocks[1]
    assert bullet["type"] == "bulleted_list_item"
    rt = bullet["bulleted_list_item"]["rich_text"]
    assert rt[0]["text"]["content"] == "Features (X):"
    assert rt[0]["annotations"]["bold"] is True
    assert "Input variables" in rt[1]["text"]["content"]
    assert rt[1]["annotations"]["bold"] is False


@pytest.mark.asyncio
async def test_create_notion_page_explicit_new_creates_workspace_page():
    """When target_section='new' or 'alg se', it creates a new page under workspace parent, not DSA NOTES."""
    mock_search_results = {
        "results": [
            {
                "id": "dsa-page-id",
                "object": "page",
                "url": "https://notion.so/dsa-notes",
                "properties": {
                    "title": {"title": [{"plain_text": "DSA NOTES"}]}
                }
            }
        ]
    }
    with patch("httpx.AsyncClient.post") as mock_post, \
         patch("httpx.AsyncClient.patch") as mock_patch:

        def fake_post(url, **kwargs):
            resp = MagicMock()
            if "search" in url:
                resp.status_code = 200
                resp.json.return_value = mock_search_results
            elif "pages" in url:
                resp.status_code = 200
                resp.json.return_value = {"id": "new-page-id", "url": "https://notion.so/new-page-id"}
            return resp

        mock_post.side_effect = fake_post

        ok, msg, url = await create_or_append_notion_note(
            token="secret_mock_123",
            title="AI & ML Notes",
            content="# AI Notes\n- Point 1",
            target_section="new"
        )
        assert ok is True
        assert "New Notion page 'AI & ML Notes' successfully created" in msg
        assert url == "https://notion.so/new-page-id"
        # Ensure patch was NOT called to append to DSA NOTES
        assert not mock_patch.called

        # Ensure page creation used parent workspace: True
        create_payload = [call for call in mock_post.call_args_list if "pages" in call[0][0]][0][1]["json"]
        assert create_payload["parent"] == {"workspace": True}


@pytest.mark.asyncio
async def test_list_notion_pages_filters_archived_and_database_rows():
    """Verifies list_notion_pages excludes archived pages and internal database rows."""
    mock_search_data = {
        "results": [
            {
                "id": "p1",
                "parent": {"type": "workspace", "workspace": True},
                "archived": False,
                "in_trash": False,
                "properties": {"title": {"title": [{"plain_text": "DSA NOTES"}]}},
                "url": "https://notion.so/dsa"
            },
            {
                "id": "p2",
                "parent": {"type": "database_id", "database_id": "db123"},
                "archived": False,
                "in_trash": False,
                "properties": {"Name": {"title": [{"plain_text": "Internal Row"}]}},
                "url": "https://notion.so/internal"
            },
            {
                "id": "p3",
                "parent": {"type": "workspace", "workspace": True},
                "archived": True,
                "in_trash": False,
                "properties": {"title": {"title": [{"plain_text": "Old Deleted Page"}]}},
                "url": "https://notion.so/old"
            },
            {
                "id": "p4",
                "parent": {"type": "workspace", "workspace": True},
                "archived": False,
                "in_trash": False,
                "properties": {"title": {"title": [{"plain_text": "Welcome to Notion"}]}},
                "url": "https://notion.so/welcome"
            }
        ]
    }
    with patch("httpx.AsyncClient.post") as mock_post:
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = mock_search_data
        mock_post.return_value = resp

        pages = await list_notion_pages("secret_token")
        assert len(pages) == 2
        titles = [p["title"] for p in pages]
        assert "DSA NOTES" in titles
        assert "Welcome to Notion" in titles
        assert "Internal Row" not in titles
        assert "Old Deleted Page" not in titles


def test_parse_content_code_block_native_notion_block():
    """Verify that triple-backtick markdown blocks are converted into native Notion code blocks."""
    raw_text = """The page includes a heading and the code block:

```python
from fastapi import FastAPI
app = FastAPI()
```"""
    blocks = parse_content_to_notion_blocks(raw_text)
    types = [b["type"] for b in blocks]
    assert "paragraph" in types
    assert "code" in types

    code_block = next(b for b in blocks if b["type"] == "code")
    assert code_block["code"]["language"] == "python"
    content_str = code_block["code"]["rich_text"][0]["text"]["content"]
    assert "from fastapi import FastAPI" in content_str
    assert "app = FastAPI()" in content_str


@pytest.mark.asyncio
async def test_append_to_notion_page_with_code_block():
    """Verify append_to_notion_page patches the code block into the existing page."""
    mock_search_data = {
        "results": [
            {
                "id": "40596daf148381f7b811c38be69b57ef",
                "object": "page",
                "properties": {"title": {"title": [{"plain_text": "FastAPI Quick Notes"}]}},
                "url": "https://app.notion.com/p/FastAPI-Quick-Notes-40596daf148381f7b811c38be69b57ef"
            }
        ]
    }
    with patch("httpx.AsyncClient.post") as mock_post, \
         patch("httpx.AsyncClient.patch") as mock_patch:

        mock_post_resp = MagicMock()
        mock_post_resp.status_code = 200
        mock_post_resp.json.return_value = mock_search_data
        mock_post.return_value = mock_post_resp

        mock_patch_resp = MagicMock()
        mock_patch_resp.status_code = 200
        mock_patch_resp.json.return_value = {"results": []}
        mock_patch.return_value = mock_patch_resp

        from integrations import append_to_notion_page
        code_snippet = "```python\nfrom fastapi import FastAPI\napp = FastAPI()\n```"
        ok, msg, url = await append_to_notion_page(
            token="secret_token",
            content=code_snippet,
            page_id_or_title="https://app.notion.com/p/FastAPI-Quick-Notes-40596daf148381f7b811c38be69b57ef"
        )
        assert ok is True
        assert "FastAPI Quick Notes" in msg
        assert "40596daf148381f7b811c38be69b57ef" in url
        assert mock_patch.called
        patch_payload = mock_patch.call_args[1]["json"]
        assert any(b["type"] == "code" for b in patch_payload["children"])


