"""
PeculiarAI — Gmail Connector (Universal Connector Contract)
Wraps Gmail operations through Google OAuth with deterministic mock support and standard error taxonomy.
"""
import os
import time
from typing import Dict, Any, List, Optional, Literal

import db
from connectors.base import Connector, ToolSpec, ToolResult, HealthResult, ErrorType

GMAIL_FIXTURES = {
    "account": "user@example.com",
    "messages": [
        {"id": "msg_001", "to": "ananya@example.com", "subject": "Module Update", "body": "Hi Ananya, the module is ready."}
    ]
}

class GmailConnector(Connector):
    name = "gmail"
    aliases = ["gmail", "email", "mail", "inbox", "reply", "draft", "bhejo"]
    display_name = "Gmail"

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
        return f"Gmail connect karne ke liye is link par jakar authorize karein: {base_url}/connect.html"

    def tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="send_email",
                description="Drafts and prepares an email to be sent via Gmail (requires human confirmation before sending).",
                parameters={
                    "type": "object",
                    "properties": {
                        "to": {"type": "string", "description": "Recipient email address"},
                        "subject": {"type": "string", "description": "Subject of the email"},
                        "body": {"type": "string", "description": "Body content of the email"}
                    },
                    "required": ["to", "subject", "body"]
                },
                kind="write",
                needs_confirmation=True
            )
        ]

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        state = self.auth_state(user_or_chat_id)
        if state == "not_connected":
            return ToolResult(
                ok=False,
                error={
                    "type": ErrorType.NOT_CONNECTED.value,
                    "message": "Google/Gmail is not connected.",
                    "retryable": False
                }
            )

        if tool_name == "send_email":
            to = args.get("to", "")
            subject = args.get("subject", "")
            body = args.get("body", "")
            if not to or not subject or not body:
                return ToolResult(
                    ok=False,
                    error={
                        "type": ErrorType.INVALID_ARGS.value,
                        "message": "Missing required fields for email (to, subject, body).",
                        "retryable": False
                    }
                )

            # Two-phase confirmation interception
            if not args.get("_confirmed", False):
                return ToolResult(
                    ok=True,
                    data={
                        "status": "needs_confirmation",
                        "action": "send_email",
                        "to": to,
                        "subject": subject,
                        "body": body
                    }
                )

            # Direct dispatch after user confirmation
            try:
                import google_services
                msg_id = await google_services.send_email(user_or_chat_id, to, subject, body)
                return ToolResult(
                    ok=True,
                    data={"status": "success", "message_id": msg_id, "to": to, "subject": subject},
                    ids=[str(msg_id)]
                )
            except Exception as exc:
                err_str = str(exc)
                err_type = ErrorType.AUTH_EXPIRED.value if "refresh" in err_str.lower() or "token" in err_str.lower() else ErrorType.API_ERROR.value
                return ToolResult(
                    ok=False,
                    error={"type": err_type, "message": f"Gmail dispatch error: {err_str}", "retryable": True}
                )

        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": f"Unknown tool '{tool_name}'"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        state = self.auth_state(user_or_chat_id)
        if state != "connected":
            return HealthResult(ok=True, connected=False, message="Gmail is not connected.")
        try:
            import google_services
            access, acc = await google_services.access_token_for_chat(user_or_chat_id)
            return HealthResult(ok=True, connected=True, message=f"Connected to Gmail as {acc}")
        except Exception as exc:
            return HealthResult(ok=False, connected=False, message=f"Gmail health check error: {str(exc)}")

    def fixtures(self) -> Dict[str, Any]:
        return GMAIL_FIXTURES

    def mock(self) -> "Connector":
        return MockGmailConnector()


class MockGmailConnector(GmailConnector):
    def __init__(self, forced_state: Literal["connected", "not_connected", "expired", "partial"] = "connected"):
        self._forced_state = forced_state

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        return self._forced_state

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if self._forced_state == "not_connected":
            return ToolResult(ok=False, error={"type": ErrorType.NOT_CONNECTED.value, "message": "Gmail is not connected."})
        if self._forced_state == "expired":
            return ToolResult(ok=False, error={"type": ErrorType.AUTH_EXPIRED.value, "message": "Google token expired."})

        if tool_name == "send_email":
            if not args.get("_confirmed", False):
                return ToolResult(
                    ok=True,
                    data={"status": "needs_confirmation", "action": "send_email", "to": args.get("to"), "subject": args.get("subject"), "body": args.get("body")}
                )
            return ToolResult(
                ok=True,
                data={"status": "success", "message_id": "mock_msg_999", "to": args.get("to")},
                ids=["mock_msg_999"]
            )

        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": "Unknown mock tool"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        return HealthResult(ok=True, connected=(self._forced_state == "connected"), message="Mock Gmail OK")
