import pytest
from unittest.mock import patch, AsyncMock, MagicMock
import os
import json

import db
from agent.tools import TOOL_DEFINITIONS, execute_tool

TEST_CHAT_ID = 998877509

MINIMAL_VALID_ARGS = {
    "execute_python_code": {"code": "print(1)", "purpose": "test"},
    "search_memory": {"query": "test query"},
    "get_measurements": {"metric": "glucose"},
    "get_contact": {"name_or_alias": "Aarav"},
    "save_contact": {"name": "Aarav Mishra", "phone": "9876543210"},
    "list_contacts": {},
    "update_contact_commitment": {"contact_name": "Aarav", "commitment": "send files"},
    "get_commitments": {},
    "save_note_or_fact": {"text": "Prefers cold coffee", "category": "preference"},
    "clear_chat_history": {"confirm": True},
    "request_master_privacy_wipe": {"confirm": True},
    "set_user_timezone": {"timezone": "Asia/Kolkata"},
    "delete_note_or_fact": {"query": "coffee"},
    "manage_smart_list": {"action": "create", "list_name": "Groceries", "items": ["Milk"]},
    "create_packing_list": {"trip_title": "Goa Trip", "items": ["Sunscreen"]},
    "create_reminder": {"title": "Doctor Appointment", "due_date": "2026-11-10", "due_time": "10:00"},
    "update_reminder": {"title": "Doctor Appointment", "due_date": "2026-11-11", "due_time": "11:00"},
    "delete_reminder": {"title": "Doctor Appointment"},
    "restore_reminder": {"reminder_id": 1},
    "list_reminders": {"date_str": "2026-11-10"},
    "get_schedule": {"target_date": "2026-11-10"},
    "check_availability": {"target_date": "2026-11-10", "start_time": "14:00", "duration_minutes": 30},
    "create_meeting": {"title": "Team Sync", "start_iso": "2026-11-10T14:00:00+05:30", "duration_minutes": 30},
    "get_meet_link": {"query": "Team Sync"},
    "send_email": {"to": "aarav@example.com", "subject": "Test Email", "body": "Hello Aarav"},
    "create_notion_page": {"title": "Project Notes", "content": "Discussion notes"},
    "update_notion_page": {"page_title_or_id": "Project Notes", "content": "Updated content"},
    "list_github_repos": {"username": "testuser"},
    "get_github_file": {"repo": "testuser/testrepo", "path": "README.md"},
    "update_github_file": {"repo": "testuser/testrepo", "path": "README.md", "content": "# Readme"},
    "list_github_issues": {"repo": "testuser/testrepo"},
    "generate_pdf": {"title": "Monthly Report", "content": "Summary of events"},
    "generate_excel": {"data_type": "contacts"},
    "search_user_emails": {"query": "test"},
    "check_integrations_status": {"service": "github"},
    "connect_integration": {"service": "github", "value": "ghp_1234567890123456789012"},
    "disconnect_integration": {"service": "github"},
    "list_notion_pages": {"query": "notes"},
    "get_notion_page_content": {"page_id_or_title": "Project Notes"},
    "delete_notion_page": {"page_id_or_title": "Project Notes"},
    "ask_user": {"question": "Do you agree?", "options": ["Yes", "No"]},
    "get_weather": {"city": "Jodhpur"},
    "convert_currency": {"amount": 100, "from_currency": "USD", "to_currency": "INR"},
    "get_market_price": {"item": "gold", "currency": "INR"},
    "calculate": {"expression": "2 + 2"},
    "deep_research": {"topic": "Quantum Computing", "goal": "Architecture survey"},
    "learn_user_correction": {"wrong_concept": "wrong assumption", "correct_rule": "correct rule", "category": "rule"},
    "find_nearby_places": {"category": "restaurants", "city": "Jodhpur"},
    "get_movie_showtimes": {"city": "Jodhpur", "movie": "Inception"},
    "search_web": {"query": "current technology news"},
    "web_search": {"query": "current technology news", "num_results": 3},
    "fetch_url": {"url": "https://example.com"},
    "manage_commitment": {"action": "create", "title": "Finish code review"},
    "read_webpage": {"url": "https://example.com"},
    "manage_relationship": {"action": "list"},
    "manage_subscription": {"action": "list"},
    "get_memory_health_radar": {}
}

@pytest.fixture(autouse=True)
def setup_user():
    db.ensure_user(TEST_CHAT_ID)
    db.give_consent(TEST_CHAT_ID)
    db.set_persona(TEST_CHAT_ID, "job")
    db.set_state(TEST_CHAT_ID, "active")
    yield

ALL_TOOL_NAMES = [t["function"]["name"] for t in TOOL_DEFINITIONS]

@pytest.mark.asyncio
@pytest.mark.parametrize("tool_name", ALL_TOOL_NAMES)
async def test_all_tools_smoke(tool_name):
    """
    Parametrized smoke test across all 55 TOOL_DEFINITIONS.
    Asserts zero unhandled Python exceptions (NameError, UnboundLocalError, AttributeError, TypeError).
    All outputs must be structured dictionaries with typed status.
    """
    args = MINIMAL_VALID_ARGS.get(tool_name, {})
    
    # Mock external calls safely
    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock) as mock_g_conn, \
         patch("google_services.fetch_user_inbox_messages", new_callable=AsyncMock) as mock_inbox, \
         patch("google_services.list_calendar_events", new_callable=AsyncMock) as mock_events, \
         patch("integrations.validate_notion_token", new_callable=AsyncMock) as mock_notion_v, \
         patch("integrations.validate_github_token", new_callable=AsyncMock) as mock_gh_v:
        
        mock_g_conn.return_value = (True, "test@example.com")
        mock_inbox.return_value = []
        mock_events.return_value = []
        mock_notion_v.return_value = (True, "Test Workspace")
        mock_gh_v.return_value = (True, "testuser")
        
        res = await execute_tool(chat_id=TEST_CHAT_ID, tool_name=tool_name, arguments=args)
        
        assert isinstance(res, dict), f"Tool '{tool_name}' must return dict, got {type(res)}"
        status = res.get("status")
        valid_statuses = ("success", "confirmation_required", "needs_confirmation", "error", "not_found", "clarification_needed", "ready_for_dispatch")
        assert status in valid_statuses, f"Tool '{tool_name}' returned invalid status '{status}': {res}"
        
        # If error, assert it is typed and never contains raw Python exception text
        if status == "error":
            err_type = res.get("error_type") or res.get("type") or res.get("error")
            assert err_type is not None, f"Tool '{tool_name}' error must be typed: {res}"
            user_msg = res.get("user_message") or res.get("message") or ""
            forbidden = ("UnboundLocalError", "NameError", "AttributeError", "TypeError", "Traceback (most recent call last)")
            for fb in forbidden:
                assert fb not in user_msg, f"Tool '{tool_name}' leaked raw exception '{fb}' in user_message: {user_msg}"
