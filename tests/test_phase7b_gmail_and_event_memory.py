import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from datetime import datetime, timezone
import json

import db
from agent.tools import execute_tool
from agent.verifier import verify_and_ground_reply, deterministic_verify

TEST_CHAT_ID = 998877502

@pytest.fixture(autouse=True)
def setup_user():
    db.ensure_user(TEST_CHAT_ID)
    db.give_consent(TEST_CHAT_ID)
    db.set_persona(TEST_CHAT_ID, "job")
    db.set_state(TEST_CHAT_ID, "active")
    yield

# ------------------------------------------------------------------------------
# 1. GMAIL SEARCH ERRORS & SUCCESS
# ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_user_emails_not_connected():
    """Verify that search_user_emails returns typed 'not_connected' error when Google is unlinked."""
    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock) as mock_conn:
        mock_conn.return_value = (False, None)
        
        res = await execute_tool(
            tool_name="search_user_emails",
            arguments={"query": "in:inbox"},
            chat_id=TEST_CHAT_ID
        )
        
        assert res.get("status") == "error"
        assert res.get("error_type") == "not_connected"
        assert "connect" in res.get("message", "").lower()
        assert res.get("auth_url") is not None


@pytest.mark.asyncio
async def test_search_user_emails_auth_scope_missing():
    """Verify typed 'auth_scope_missing' error with actionable OAuth re-consent link."""
    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock) as mock_conn:
        mock_conn.return_value = (True, "testuser@gmail.com")
        
        # Simulate chat_id in _CHAT_GMAIL_INSUFFICIENT_SCOPES
        with patch("google_services._CHAT_GMAIL_INSUFFICIENT_SCOPES", {TEST_CHAT_ID}):
            res = await execute_tool(
                tool_name="search_user_emails",
                arguments={"query": "aarav", "folder": "sent"},
                chat_id=TEST_CHAT_ID
            )
            
            assert res.get("status") == "error"
            assert res.get("error_type") == "auth_scope_missing"
            assert "permission missing" in res.get("message", "").lower() or "read access" in res.get("message", "").lower()
            assert res.get("auth_url") is not None


@pytest.mark.asyncio
async def test_search_user_emails_not_found():
    """Verify that when 0 emails match, search_user_emails returns typed 'not_found' status."""
    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock) as mock_conn:
        mock_conn.return_value = (True, "testuser@gmail.com")
        
        with patch("google_services.fetch_user_inbox_messages", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = []
            
            res = await execute_tool(
                tool_name="search_user_emails",
                arguments={"query": "nonexistent_person@domain.com", "folder": "sent"},
                chat_id=TEST_CHAT_ID
            )
            
            assert res.get("status") == "not_found"
            assert res.get("count") == 0
            assert "koi matching email nahi mila" in res.get("message", "").lower()


@pytest.mark.asyncio
async def test_search_user_emails_sent_mail_success():
    """Verify that search_user_emails queries sent folder and returns structured emails."""
    with patch("main.get_valid_google_connection_by_chat_id", new_callable=AsyncMock) as mock_conn:
        mock_conn.return_value = (True, "testuser@gmail.com")
        
        fake_messages = [{
            "id": "msg_12345",
            "sender": "testuser@gmail.com",
            "to": "aaravmishra619@gmail.com",
            "subject": "weather update",
            "date": "Thu, 31 Oct 2026 16:30:00 +0530",
            "body": "It is hot in Jodhpur today"
        }]
        
        with patch("google_services.fetch_user_inbox_messages", new_callable=AsyncMock) as mock_fetch:
            mock_fetch.return_value = fake_messages
            
            res = await execute_tool(
                tool_name="search_user_emails",
                arguments={"to": "aaravmishra619@gmail.com", "folder": "sent"},
                chat_id=TEST_CHAT_ID
            )
            
            assert res.get("status") == "success"
            assert res.get("count") == 1
            emails = res.get("emails", [])
            assert len(emails) == 1
            assert emails[0]["id"] == "msg_12345"
            assert emails[0]["subject"] == "weather update"


# ------------------------------------------------------------------------------
# 2. ROUTE & TRAVEL TIME GROUNDING INTERCEPTION
# ------------------------------------------------------------------------------

def test_verifier_blocks_ungrounded_route_and_travel_time():
    """Verify that verifier blocks hallucinated route streets and travel times without maps tool."""
    user_q = "bhy jdhpur m ratanada se sardarpura jana ka sbse best route kya h"
    draft = "Route: Ratanada Circle se Circuit House Road hote hue jaiye. Time: 10-15 minute lagenge."
    
    # tool_results without maps tool
    fake_tool_results = [{"tool_name": "search_web", "status": "success", "message": "cab booking links"}]
    
    passed, revised, violations = deterministic_verify(
        draft_reply=draft,
        tool_results=fake_tool_results,
        user_query=user_q
    )
    
    assert passed is False
    assert any("Ungrounded route or travel time" in v for v in violations)
    assert "google.com/maps" in revised
    assert "live GPS navigation" in revised or "traffic maps" in revised

