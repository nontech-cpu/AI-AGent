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
        return "Notion connect karne ke liye apna Notion integration token (`secret_...` ya `ntn_...`) chat mein bhej dijiye."

    def tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="create_notion_page",
                description="Appends or creates a new page in the connected Notion workspace.",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Title of the Notion page"},
                        "content": {"type": "string", "description": "Markdown or text body content"}
                    },
                    "required": ["title", "content"]
                },
                kind="write",
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

        if tool_name == "create_notion_page":
            title = args.get("title", "")
            content = args.get("content", "")
            if not title or not content:
                return ToolResult(
                    ok=False,
                    error={"type": ErrorType.INVALID_ARGS.value, "message": "Missing 'title' or 'content' for Notion page."}
                )

            token_row = db.get_user_integration(user_or_chat_id, "notion")
            api_key = token_row.get("api_key") if token_row else None
            res = await integrations.create_notion_page(api_key, title, content)
            if res.get("status") == "success":
                pg_url = res.get("url") or ""
                return ToolResult(
                    ok=True,
                    data={"status": "success", "title": title, "url": pg_url},
                    urls=[pg_url] if pg_url else []
                )
            else:
                return ToolResult(
                    ok=False,
                    error={"type": ErrorType.API_ERROR.value, "message": res.get("message", "Notion creation failed.")}
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
