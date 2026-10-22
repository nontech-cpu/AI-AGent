"""
PeculiarAI — Google Drive Connector (Universal Connector Contract)
Handles file and document operations in Google Drive with deterministic mock support.
"""
import os
import json
from typing import Dict, Any, List, Optional, Literal

import db
from connectors.base import Connector, ToolSpec, ToolResult, HealthResult, ErrorType

DRIVE_FIXTURES = {
    "files": [
        {"id": "file_doc_01", "name": "Project_Summary.pdf", "mimeType": "application/pdf"}
    ]
}

class DriveConnector(Connector):
    name = "drive"
    aliases = ["drive", "google drive", "gdrive", "file", "doc", "folder", "upload"]
    display_name = "Google Drive"

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        try:
            with db.engine.connect() as conn:
                row = conn.execute(db.text("SELECT id FROM users WHERE telegram_chat_id = :cid"), {"cid": user_or_chat_id}).first()
            if not row:
                return "not_connected"
            token = db.get_google_token(int(row[0]))
            if not token or not token.get("refresh_token_enc"):
                return "not_connected"
            return "connected"
        except Exception:
            return "not_connected"

    def connect_instructions(self, user_or_chat_id: int) -> str:
        base_url = os.getenv("PUBLIC_BASE_URL", "https://tightly-barbell-creamed.ngrok-free.dev")
        return f"Google Drive connect karne ke liye is link par authorize karein: {base_url}/connect.html"

    def tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="upload_to_drive",
                description="Uploads a text or note file directly to user's Google Drive (requires confirmation).",
                parameters={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "Target filename e.g. 'MeetingNotes.txt'"},
                        "content": {"type": "string", "description": "Text content to save into the file"}
                    },
                    "required": ["filename", "content"]
                },
                kind="write",
                needs_confirmation=True
            )
        ]

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        state = self.auth_state(user_or_chat_id)
        if state == "not_connected":
            return ToolResult(ok=False, error={"type": ErrorType.NOT_CONNECTED.value, "message": "Google Drive is not connected."})

        if tool_name == "upload_to_drive":
            fn = args.get("filename", "notes.txt")
            content = args.get("content", "")
            if not args.get("_confirmed", False):
                return ToolResult(
                    ok=True,
                    data={"status": "needs_confirmation", "action": "upload_file", "filename": fn, "content": content}
                )

            try:
                import google_services
                res = await google_services.upload_text_file(user_or_chat_id, fn, content)
                file_id = res.get("id") or "uploaded"
                return ToolResult(
                    ok=True,
                    data={"status": "success", "file_id": file_id, "filename": fn},
                    ids=[str(file_id)]
                )
            except Exception as exc:
                return ToolResult(ok=False, error={"type": ErrorType.API_ERROR.value, "message": str(exc)})

        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": f"Unknown tool '{tool_name}'"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        state = self.auth_state(user_or_chat_id)
        if state != "connected":
            return HealthResult(ok=True, connected=False, message="Drive is not connected.")
        return HealthResult(ok=True, connected=True, message="Connected to Google Drive.")

    def fixtures(self) -> Dict[str, Any]:
        return DRIVE_FIXTURES

    def mock(self) -> "Connector":
        return MockDriveConnector()


class MockDriveConnector(DriveConnector):
    def __init__(self, forced_state: Literal["connected", "not_connected", "expired", "partial"] = "connected"):
        self._forced_state = forced_state

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        return self._forced_state

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if self._forced_state == "not_connected":
            return ToolResult(ok=False, error={"type": ErrorType.NOT_CONNECTED.value, "message": "Google Drive is not connected."})
        if self._forced_state == "expired":
            return ToolResult(ok=False, error={"type": ErrorType.AUTH_EXPIRED.value, "message": "Google Drive token expired."})

        if tool_name == "upload_to_drive":
            if not args.get("_confirmed", False):
                return ToolResult(ok=True, data={"status": "needs_confirmation", "action": "upload_file", "filename": args.get("filename")})
            return ToolResult(
                ok=True,
                data={"status": "success", "file_id": "mock_drive_001", "filename": args.get("filename")},
                ids=["mock_drive_001"]
            )
        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": "Unknown mock tool"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        return HealthResult(ok=True, connected=(self._forced_state == "connected"), message="Mock Drive OK")
