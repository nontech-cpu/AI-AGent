"""
PeculiarAI — Notion Connector (Universal Connector Contract)
Handles creating and updating Notion workspace pages with deterministic mock support.
"""
import os
import json
import httpx
from typing import Dict, Any, List, Optional, Literal

import db
import integrations
from connectors.base import Connector, ToolSpec, ToolResult, HealthResult, ErrorType

NOTION_FIXTURES = {
    "workspace": "Peculiar Workspace",
    "pages": [
        {"id": "notion_pg_101", "title": "Meeting Notes", "url": "https://notion.so/peculiar/Meeting-Notes-101"}
    ]
}

class NotionConnector(Connector):
    name = "notion"
    aliases = ["notion", "page", "notes", "database", "workspace", "document"]
    display_name = "Notion"

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        row = db.get_user_integration(user_or_chat_id, "notion")
        if not row:
            return "not_connected"
        key = row.get("api_key")
        if key and (key.startswith("secret_") or key.startswith("ntn_")):
            return "connected"
        return "not_connected"

    def connect_instructions(self, user_or_chat_id: int) -> str:
        from notion_auth import is_notion_oauth_configured, build_notion_auth_url
        if is_notion_oauth_configured():
            try:
                url = build_notion_auth_url(int(user_or_chat_id), source="tg")
                return f"Apna Notion workspace 1-click mein connect karne ke liye is link par authorize karein:\n👉 {url}"
            except Exception:
                pass
        frontend_connect = os.getenv("FRONTEND_CONNECT_URL", "https://bucolic-dieffenbachia-c20bd0.netlify.app/connect.html")
        return f"Apna Notion workspace connect karne ke liye is secure link par jayein:\n👉 {frontend_connect}"

    def tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="create_notion_page",
                description="Creates a new dedicated documentation page in Notion. Supports headings, bullet points, and code block formatting.",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Title or heading for the new page (e.g. 'FastAPI Quick Notes')."},
                        "content": {"type": "string", "description": "The markdown content. Can contain code blocks (```python ... ```), headings (#), bullets (-), or numbered lists."},
                        "target_section": {"type": ["string", "null"], "description": "Optional name of existing page or section to append into. Leave null to create new page."},
                        "formatting": {"type": ["string", "null"], "description": "Optional formatting guidance: 'bullets', 'headings_and_bullets', or 'text'."}
                    },
                    "required": ["title", "content"]
                },
                kind="write",
                needs_confirmation=False
            ),
            ToolSpec(
                name="update_notion_page",
                description="Append or add new content, code blocks, sections, or notes to an existing Notion page (e.g. adding a Python code block, updating notes). Use this whenever the user wants to add to, format, or update an existing page.",
                parameters={
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "description": "The markdown content or code block (using ```language ... ```) to add to the page."},
                        "page_title_or_id": {"type": ["string", "null"], "description": "Title, ID, or Notion URL of the page to update. If null, updates the most recent active Notion page."},
                        "section_title": {"type": ["string", "null"], "description": "Optional section heading to place above the new content."},
                        "formatting": {"type": ["string", "null"], "description": "Optional formatting guidance e.g. 'code', 'bullets', 'text'."}
                    },
                    "required": ["content"]
                },
                kind="write",
                needs_confirmation=False
            ),
            ToolSpec(
                name="list_notion_pages",
                description="Lists or searches existing pages and notes in the user's connected Notion workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": ["string", "null"], "description": "Optional search term to filter pages by title."}
                    }
                },
                kind="read",
                needs_confirmation=False
            )
        ]

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        state = self.auth_state(user_or_chat_id)
        if state == "not_connected":
            return ToolResult(
                ok=False,
                error={
                    "type": ErrorType.NOT_CONNECTED.value,
                    "message": "Notion is not connected. Connect your Notion workspace token first.",
                    "retryable": False
                }
            )

        if tool_name == "list_notion_pages":
            query = args.get("query")
            token_row = db.get_user_integration(user_or_chat_id, "notion")
            api_key = token_row.get("api_key") if isinstance(token_row, dict) else (token_row if isinstance(token_row, str) else None)
            if not api_key:
                api_key = os.getenv("NOTION_API_KEY")

            pages = await integrations.list_notion_pages(token=api_key, query=query)
            formatted_lines = []
            for p in pages:
                formatted_lines.append(f"• **{p['title']}** - {p['url']}")
            pages_summary = "\n".join(formatted_lines) if formatted_lines else "Koi existing pages nahi mili."
            return ToolResult(
                ok=True,
                data={
                    "status": "success",
                    "count": len(pages),
                    "pages": pages,
                    "summary": pages_summary,
                    "message": f"Found {len(pages)} pages in your Notion workspace."
                },
                urls=[p["url"] for p in pages if p.get("url")]
            )

        if tool_name == "create_notion_page":
            title = args.get("title", "")
            content = args.get("content", "")
            target_section = args.get("target_section")
            formatting = args.get("formatting")

            if not title or not content:
                return ToolResult(
                    ok=False,
                    error={"type": ErrorType.INVALID_ARGS.value, "message": "Missing 'title' or 'content' for Notion page."}
                )

            token_row = db.get_user_integration(user_or_chat_id, "notion")
            api_key = token_row.get("api_key") if isinstance(token_row, dict) else (token_row if isinstance(token_row, str) else None)
            if not api_key:
                api_key = os.getenv("NOTION_API_KEY")

            ok, msg, pg_url = await integrations.create_or_append_notion_note(
                token=api_key,
                title=title,
                content=content,
                target_section=target_section,
                formatting=formatting
            )
            if ok:
                return ToolResult(
                    ok=True,
                    data={"status": "success", "title": title, "url": pg_url or "", "message": msg},
                    urls=[pg_url] if pg_url else []
                )
            else:
                return ToolResult(
                    ok=False,
                    error={"type": ErrorType.API_ERROR.value, "message": msg}
                )

        if tool_name in ("update_notion_page", "append_notion_page"):
            content = args.get("content", "")
            page_title_or_id = args.get("page_title_or_id") or args.get("target_section")
            section_title = args.get("section_title")
            formatting = args.get("formatting")

            if not content:
                return ToolResult(
                    ok=False,
                    error={"type": ErrorType.INVALID_ARGS.value, "message": "Missing 'content' to append to Notion page."}
                )

            token_row = db.get_user_integration(user_or_chat_id, "notion")
            api_key = token_row.get("api_key") if isinstance(token_row, dict) else (token_row if isinstance(token_row, str) else None)
            if not api_key:
                api_key = os.getenv("NOTION_API_KEY")

            if not page_title_or_id:
                try:
                    from state.active_context import get_active_context
                    act_ctx = get_active_context(user_or_chat_id)
                    if act_ctx and act_ctx.get("connector", "").lower() == "notion":
                        page_title_or_id = act_ctx.get("url") or act_ctx.get("topic")
                except Exception:
                    pass

            ok, msg, pg_url = await integrations.append_to_notion_page(
                token=api_key,
                content=content,
                page_id_or_title=page_title_or_id,
                section_title=section_title,
                formatting=formatting
            )
            if ok:
                return ToolResult(
                    ok=True,
                    data={"status": "success", "url": pg_url or "", "message": msg},
                    urls=[pg_url] if pg_url else []
                )
            else:
                return ToolResult(
                    ok=False,
                    error={"type": ErrorType.API_ERROR.value, "message": msg}
                )

        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": f"Unknown tool '{tool_name}'"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        state = self.auth_state(user_or_chat_id)
        if state != "connected":
            return HealthResult(ok=True, connected=False, message="Notion is not connected.")
        row = db.get_user_integration(user_or_chat_id, "notion")
        key = row.get("api_key") if row else None
        is_ok, ws_name = await integrations.validate_notion_token(key)
        return HealthResult(
            ok=is_ok,
            connected=is_ok,
            message=f"Connected to Notion workspace: {ws_name}" if is_ok else "Notion token invalid/expired."
        )

    def fixtures(self) -> Dict[str, Any]:
        return NOTION_FIXTURES

    def mock(self) -> "Connector":
        return MockNotionConnector()


class MockNotionConnector(NotionConnector):
    def __init__(self, forced_state: Literal["connected", "not_connected", "expired", "partial"] = "connected"):
        self._forced_state = forced_state

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        return self._forced_state

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if self._forced_state == "not_connected":
            return ToolResult(ok=False, error={"type": ErrorType.NOT_CONNECTED.value, "message": "Notion is not connected."})
        if self._forced_state == "expired":
            return ToolResult(ok=False, error={"type": ErrorType.AUTH_EXPIRED.value, "message": "Notion token expired."})

        if tool_name == "list_notion_pages":
            pages = NOTION_FIXTURES.get("pages", [])
            return ToolResult(
                ok=True,
                data={"status": "success", "count": len(pages), "pages": pages, "workspace": NOTION_FIXTURES.get("workspace", "Mock Notion")},
                ids=[p["id"] for p in pages],
                urls=[p["url"] for p in pages]
            )

        if tool_name == "create_notion_page":
            pg = NOTION_FIXTURES["pages"][0]
            return ToolResult(
                ok=True,
                data={"status": "success", "title": args.get("title"), "url": pg["url"]},
                ids=[pg["id"]],
                urls=[pg["url"]]
            )
        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": "Unknown mock tool"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        return HealthResult(ok=True, connected=(self._forced_state == "connected"), message="Mock Notion OK")
