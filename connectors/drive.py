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
                description="Uploads a text note or document file directly to user's Google Drive, optionally in a new or existing folder (requires confirmation).",
                parameters={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "Target filename e.g. 'MeetingNotes.txt' or 'Invoice.pdf'"},
                        "content": {"type": "string", "description": "Text content to save into the file"},
                        "folder_name": {"type": "string", "description": "Optional folder name to place the file in. Will be created if it does not exist."},
                        "folder_id": {"type": "string", "description": "Optional folder ID"}
                    },
                    "required": ["filename"]
                },
                kind="write",
                needs_confirmation=True
            ),
            ToolSpec(
                name="search_drive_files",
                description="Searches and lists files or folders in Google Drive by name, folder, or file type.",
                parameters={
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search keyword or filename e.g. 'Project Summary', 'invoice'"},
                        "folder_name": {"type": "string", "description": "Optional folder name to search within"},
                        "mime_type": {"type": "string", "description": "Optional filter: 'pdf', 'document', 'spreadsheet', 'folder'"},
                        "limit": {"type": "integer", "description": "Max files to return (default 10)"}
                    }
                },
                kind="read",
                needs_confirmation=False
            ),
            ToolSpec(
                name="read_drive_document",
                description="Reads and extracts text from a file or document on Google Drive (Google Docs, PDFs, Word DOCX, text) for reading or summarizing.",
                parameters={
                    "type": "object",
                    "properties": {
                        "filename": {"type": "string", "description": "Filename to find and read from Google Drive"},
                        "file_id": {"type": "string", "description": "Optional Google Drive file ID if known"},
                        "max_chars": {"type": "integer", "description": "Max characters to extract (default 8000)"}
                    }
                },
                kind="read",
                needs_confirmation=False
            ),
            ToolSpec(
                name="create_drive_folder",
                description="Creates a new folder in Google Drive.",
                parameters={
                    "type": "object",
                    "properties": {
                        "folder_name": {"type": "string", "description": "Name of the new folder to create in Google Drive"},
                        "parent_folder_name": {"type": "string", "description": "Optional parent folder name"}
                    },
                    "required": ["folder_name"]
                },
                kind="write",
                needs_confirmation=False
            )
        ]

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        state = self.auth_state(user_or_chat_id)
        if state == "not_connected":
            return ToolResult(ok=False, error={"type": ErrorType.NOT_CONNECTED.value, "message": "Google Drive is not connected."})

        import google_services

        if tool_name == "upload_to_drive":
            fn = args.get("filename", "notes.txt")
            content = args.get("content", "")
            folder_name = args.get("folder_name")
            folder_id = args.get("folder_id")

            if not args.get("_confirmed", False):
                return ToolResult(
                    ok=True,
                    data={
                        "status": "needs_confirmation",
                        "action": "upload_file",
                        "filename": fn,
                        "content": content,
                        "folder_name": folder_name,
                        "folder_id": folder_id
                    }
                )

            try:
                res = await google_services.upload_file_to_drive(
                    user_or_chat_id,
                    filename=fn,
                    content=content,
                    folder_name=folder_name,
                    folder_id=folder_id
                )
                file_id = res.get("id") or "uploaded"
                return ToolResult(
                    ok=True,
                    data={"status": "success", "file_id": file_id, "filename": fn, "folder_name": folder_name, "link": res.get("webViewLink", "")},
                    ids=[str(file_id)]
                )
            except google_services.GoogleDriveQuotaExceededError:
                return ToolResult(
                    ok=False,
                    error={
                        "type": "storage_quota_exceeded",
                        "message": "Google Drive storage quota full ho chuka hai (100% space used). Kripya kuch space free karein."
                    }
                )
            except Exception as exc:
                return ToolResult(ok=False, error={"type": ErrorType.API_ERROR.value, "message": str(exc)})

        elif tool_name == "search_drive_files":
            try:
                files = await google_services.search_drive_files(
                    user_or_chat_id,
                    query=args.get("query"),
                    folder_name=args.get("folder_name"),
                    mime_type=args.get("mime_type"),
                    limit=args.get("limit", 10)
                )
                return ToolResult(
                    ok=True,
                    data={"status": "success", "files": files, "count": len(files)},
                    ids=[str(f.get("id")) for f in files if f.get("id")]
                )
            except Exception as exc:
                return ToolResult(ok=False, error={"type": ErrorType.API_ERROR.value, "message": str(exc)})

        elif tool_name == "read_drive_document":
            try:
                res = await google_services.read_drive_document(
                    user_or_chat_id,
                    file_id=args.get("file_id"),
                    filename=args.get("filename"),
                    max_chars=args.get("max_chars", 8000)
                )
                if not res.get("ok", True):
                    return ToolResult(ok=False, error={"type": ErrorType.API_ERROR.value, "message": res.get("error", "File not found")})
                return ToolResult(
                    ok=True,
                    data=res,
                    ids=[str(res.get("file_id"))] if res.get("file_id") else []
                )
            except Exception as exc:
                return ToolResult(ok=False, error={"type": ErrorType.API_ERROR.value, "message": str(exc)})

        elif tool_name == "create_drive_folder":
            try:
                folder_name = args.get("folder_name", "New Folder")
                fid = await google_services.get_or_create_drive_folder(user_or_chat_id, folder_name)
                return ToolResult(
                    ok=True,
                    data={"status": "success", "folder_id": fid, "folder_name": folder_name},
                    ids=[str(fid)] if fid else []
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
                return ToolResult(
                    ok=True,
                    data={
                        "status": "needs_confirmation",
                        "action": "upload_file",
                        "filename": args.get("filename"),
                        "folder_name": args.get("folder_name")
                    }
                )
            return ToolResult(
                ok=True,
                data={"status": "success", "file_id": "mock_drive_001", "filename": args.get("filename"), "folder_name": args.get("folder_name")},
                ids=["mock_drive_001"]
            )

        elif tool_name == "search_drive_files":
            files = DRIVE_FIXTURES["files"]
            return ToolResult(ok=True, data={"status": "success", "files": files, "count": len(files)}, ids=["file_doc_01"])

        elif tool_name == "read_drive_document":
            return ToolResult(
                ok=True,
                data={
                    "ok": True,
                    "file_id": "file_doc_01",
                    "name": args.get("filename") or "Project_Summary.pdf",
                    "mime_type": "application/pdf",
                    "text": "Project Summary: Launch scheduled for Q3. Key focus on AI automation and reliability.",
                    "total_chars": 84,
                    "truncated": False
                },
                ids=["file_doc_01"]
            )

        elif tool_name == "create_drive_folder":
            fn = args.get("folder_name", "New Folder")
            return ToolResult(ok=True, data={"status": "success", "folder_id": "mock_folder_001", "folder_name": fn}, ids=["mock_folder_001"])

        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": "Unknown mock tool"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        return HealthResult(ok=True, connected=(self._forced_state == "connected"), message="Mock Drive OK")
