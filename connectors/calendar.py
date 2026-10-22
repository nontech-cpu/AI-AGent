"""
PeculiarAI — Google Calendar Connector (Universal Connector Contract)
Handles calendar events, availability checks, Google Meet link generation with deterministic mock support.
"""
import os
import json
from datetime import datetime, date, timedelta
from typing import Dict, Any, List, Optional, Literal

import db
from timing import IST
from connectors.base import Connector, ToolSpec, ToolResult, HealthResult, ErrorType

CALENDAR_FIXTURES = {
    "events": [
        {
            "id": "cal_evt_101",
            "summary": "Team Sync",
            "start": "2026-10-27T10:00:00+05:30",
            "end": "2026-10-27T10:30:00+05:30",
            "meet_url": "https://meet.google.com/abc-defg-hij",
            "htmlLink": "https://calendar.google.com/event?eid=101"
        }
    ]
}

class CalendarConnector(Connector):
    name = "calendar"
    aliases = ["calendar", "cal", "meeting", "event", "invite", "meet", "schedule", "slot", "appointment"]
    display_name = "Google Calendar"

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
        return f"Google Calendar connect karne ke liye is link par authorize karein: {base_url}/connect.html"

    def tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="get_schedule",
                description="Get unified schedule including Google Calendar meetings, reminders, and commitments for a given date.",
                parameters={
                    "type": "object",
                    "properties": {
                        "target_date": {"type": "string", "description": "Date in YYYY-MM-DD format (e.g. '2026-10-23')"}
                    },
                    "required": ["target_date"]
                },
                kind="read"
            ),
            ToolSpec(
                name="check_availability",
                description="Check if user is free or has clashing meetings/commitments within a specific time window.",
                parameters={
                    "type": "object",
                    "properties": {
                        "start_iso": {"type": "string", "description": "Start ISO datetime with +05:30 timezone"},
                        "duration_minutes": {"type": "integer", "description": "Duration in minutes"}
                    },
                    "required": ["start_iso"]
                },
                kind="read"
            ),
            ToolSpec(
                name="create_meeting",
                description="Creates a Google Calendar meeting with a Google Meet link (requires human confirmation before creating).",
                parameters={
                    "type": "object",
                    "properties": {
                        "title": {"type": "string", "description": "Meeting title"},
                        "start_iso": {"type": "string", "description": "Start time in ISO 8601 format with +05:30 timezone"},
                        "duration_minutes": {"type": "integer", "description": "Duration in minutes (default 30)"},
                        "attendees": {"type": "array", "items": {"type": "string"}, "description": "List of attendee emails"}
                    },
                    "required": ["title", "start_iso"]
                },
                kind="write",
                needs_confirmation=True
            ),
            ToolSpec(
                name="delete_meeting",
                description="Deletes a scheduled meeting from Google Calendar (destructive, requires confirmation).",
                parameters={
                    "type": "object",
                    "properties": {
                        "event_id": {"type": "string", "description": "Google Calendar event ID to delete"}
                    },
                    "required": ["event_id"]
                },
                kind="destructive",
                needs_confirmation=True
            )
        ]

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if tool_name == "create_meeting":
            if not args.get("_confirmed", False):
                return ToolResult(
                    ok=True,
                    data={
                        "status": "needs_confirmation",
                        "action": "create_meeting",
                        "title": args.get("title", "Meeting"),
                        "start_iso": args.get("start_iso"),
                        "duration_minutes": args.get("duration_minutes", 30),
                        "attendees": args.get("attendees", [])
                    }
                )

        state = self.auth_state(user_or_chat_id)
        if state == "not_connected" and tool_name in ["delete_meeting"]:
            return ToolResult(
                ok=False,
                error={"type": ErrorType.NOT_CONNECTED.value, "message": "Google Calendar is not connected."}
            )

        if tool_name == "get_schedule":
            t_date = args.get("target_date")
            try:
                from main import get_unified_schedule
                d_obj = date.fromisoformat(t_date) if t_date else datetime.now(IST).date()
                sched = await get_unified_schedule(user_or_chat_id, d_obj)
                events = sched.get("calendar_events", [])
                rems = sched.get("reminders", [])
                comms = sched.get("commitments", [])
                return ToolResult(
                    ok=True,
                    data={"date": d_obj.isoformat(), "calendar_events": events, "reminders": rems, "commitments": comms},
                    ids=[e.get("id") for e in events if e.get("id")],
                    urls=[e.get("meet_url") for e in events if e.get("meet_url")]
                )
            except Exception as exc:
                return ToolResult(ok=False, error={"type": ErrorType.API_ERROR.value, "message": str(exc)})

        elif tool_name == "check_availability":
            s_time = args.get("start_time")
            t_date = args.get("target_date")
            s_iso = args.get("start_iso")
            dur = args.get("duration_minutes", 30)
            act = args.get("activity_type")
            try:
                from schedule.intervals import check_interval_availability
                res = await check_interval_availability(
                    chat_id=user_or_chat_id,
                    start_time_str=s_time or (s_iso[11:16] if s_iso else "17:00"),
                    target_date_str=t_date or (s_iso[:10] if s_iso else None),
                    duration_minutes=dur,
                    activity_type=act,
                    travel_buffer_minutes=15
                )
                return ToolResult(ok=True, data=res)
            except Exception as exc:
                try:
                    from main import get_unified_schedule
                    start_dt = datetime.fromisoformat(s_iso) if s_iso else datetime.now(IST)
                    sched = await get_unified_schedule(user_or_chat_id, start_dt.date())
                    clashes = [ev.get("summary") for ev in sched.get("calendar_events", [])]
                    return ToolResult(
                        ok=True,
                        data={"is_free": len(clashes) == 0, "clashes": clashes, "start_iso": s_iso or start_dt.isoformat(), "duration_minutes": dur}
                    )
                except Exception:
                    return ToolResult(ok=True, data={"is_free": True, "clashes": []})
            try:
                import google_services
                res = await google_services.create_meeting(
                    user_or_chat_id,
                    title=args.get("title", "Meeting"),
                    start_iso=args.get("start_iso"),
                    duration_minutes=args.get("duration_minutes", 30),
                    attendees=args.get("attendees", [])
                )
                meet_url = res.get("meet_url") or ""
                return ToolResult(
                    ok=True,
                    data={"status": "success", "event": res, "meet_url": meet_url},
                    ids=[str(res.get("id"))] if res.get("id") else [],
                    urls=[meet_url] if meet_url else []
                )
            except Exception as exc:
                return ToolResult(ok=False, error={"type": ErrorType.API_ERROR.value, "message": str(exc)})

        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": f"Unknown tool '{tool_name}'"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        state = self.auth_state(user_or_chat_id)
        if state != "connected":
            return HealthResult(ok=True, connected=False, message="Calendar is not connected.")
        try:
            import google_services
            access, acc = await google_services.access_token_for_chat(user_or_chat_id)
            return HealthResult(ok=True, connected=True, message=f"Connected to Google Calendar as {acc}")
        except Exception as exc:
            return HealthResult(ok=False, connected=False, message=f"Calendar health error: {str(exc)}")

    def fixtures(self) -> Dict[str, Any]:
        return CALENDAR_FIXTURES

    def mock(self) -> "Connector":
        return MockCalendarConnector()


class MockCalendarConnector(CalendarConnector):
    def __init__(self, forced_state: Literal["connected", "not_connected", "expired", "partial"] = "connected"):
        self._forced_state = forced_state

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        return self._forced_state

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if self._forced_state == "not_connected":
            return ToolResult(ok=False, error={"type": ErrorType.NOT_CONNECTED.value, "message": "Google Calendar not connected."})
        if self._forced_state == "expired":
            return ToolResult(ok=False, error={"type": ErrorType.AUTH_EXPIRED.value, "message": "Google Calendar token expired."})

        if tool_name == "get_schedule":
            return ToolResult(
                ok=True,
                data={"date": args.get("target_date", "2026-10-27"), "calendar_events": CALENDAR_FIXTURES["events"]},
                ids=["cal_evt_101"],
                urls=["https://meet.google.com/abc-defg-hij"]
            )
        elif tool_name == "create_meeting":
            if not args.get("_confirmed", False):
                return ToolResult(
                    ok=True,
                    data={"status": "needs_confirmation", "action": "create_meeting", "title": args.get("title"), "start_iso": args.get("start_iso")}
                )
            return ToolResult(
                ok=True,
                data={"status": "success", "event_id": "cal_evt_999", "meet_url": "https://meet.google.com/mock-meet-999"},
                ids=["cal_evt_999"],
                urls=["https://meet.google.com/mock-meet-999"]
            )

        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": "Unknown mock tool"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        return HealthResult(ok=True, connected=(self._forced_state == "connected"), message="Mock Calendar OK")
