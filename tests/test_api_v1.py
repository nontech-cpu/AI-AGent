"""
Unit & integration tests for Phase 1 Mobile App API v1 endpoints:
- POST /api/v1/auth/session
- GET  /api/v1/auth/me
- POST /api/v1/auth/consent
- POST /api/v1/chat/message
- GET  /api/v1/workspace/office
- GET  /api/v1/workspace/park
- GET  /api/v1/workspace/park/search
- GET  /api/v1/tools/status
- POST /api/v1/tools/cross-query
- POST /api/v1/chat/document & /api/v1/chat/document/query
"""
import pytest
from unittest.mock import patch, AsyncMock, MagicMock
from fastapi.testclient import TestClient
from datetime import date, time, datetime


def test_auth_unauthorized(client: TestClient):
    """Endpoints requiring authentication must reject requests without tokens or X-Chat-Id."""
    res = client.get("/api/v1/auth/me")
    assert res.status_code == 401
    assert "Unauthorized" in res.json().get("detail", "")


def test_auth_session_creation_and_profile(client: TestClient):
    """Creating a session generates a valid Bearer token and accesses user profile."""
    chat_id = 987654321
    with patch("api_v1.ensure_user"):
        with patch("api_v1.get_persona", return_value="Business"):
            with patch("api_v1.has_consent", return_value=True):
                # 1. Create session
                res = client.post("/api/v1/auth/session", json={"chat_id": chat_id})
                assert res.status_code == 200
                data = res.json()
                assert data["chat_id"] == chat_id
                assert data["persona"] == "Business"
                assert data["has_consent"] is True
                assert "token" in data
                token = data["token"]

                # 2. Access profile via X-Chat-Id header
                res_me = client.get("/api/v1/auth/me", headers={"X-Chat-Id": str(chat_id)})
                assert res_me.status_code == 200
                assert res_me.json()["chat_id"] == chat_id
                assert res_me.json()["persona"] == "Business"

                # 3. Access profile via Authorization Bearer token with chat_id
                res_bearer = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {chat_id}"})
                assert res_bearer.status_code == 200
                assert res_bearer.json()["chat_id"] == chat_id


def test_auth_consent_update(client: TestClient):
    """Updating consent grants or revokes assistant permissions."""
    chat_id = 987654321
    with patch("api_v1.ensure_user"):
        with patch("api_v1.give_consent") as mock_give:
            res = client.post("/api/v1/auth/consent?action=give", headers={"X-Chat-Id": str(chat_id)})
            assert res.status_code == 200
            assert res.json()["consented"] is True
            mock_give.assert_called_once_with(chat_id)


def test_chat_message_save_item_flow(client: TestClient):
    """Chat message with actionable item parses and saves to memory."""
    chat_id = 987654321
    mock_understood = {
        "intent": "save_item",
        "item": {
            "category": "bills",
            "title": "Electricity Bill",
            "amount": 1200,
            "due_date": "2026-10-25",
            "due_time": "18:00"
        }
    }
    with patch("api_v1.ensure_user"):
        with patch("api_v1.understand", new_callable=AsyncMock, return_value=mock_understood):
            with patch("api_v1.save_memory", return_value=101) as mock_save:
                with patch("api_v1.add_reminders") as mock_rem:
                    res = client.post(
                        "/api/v1/chat/message",
                        json={"message": "electricity bill 1200 due on 25 Oct at 6 PM"},
                        headers={"X-Chat-Id": str(chat_id)}
                    )
                    assert res.status_code == 200
                    data = res.json()
                    assert data["intent"] == "save_item"
                    assert data["action_taken"] is True
                    assert "Saved" in data["reply"]
                    assert data["saved_item"]["id"] == 101
                    assert data["saved_item"]["amount"] == 1200
                    mock_save.assert_called_once()


def test_workspace_office_agenda(client: TestClient):
    """The Office endpoint returns today's tasks, reminders, and weather."""
    chat_id = 987654321
    mock_items = [("bills", "Internet Bill", 799, time(14, 0))]
    mock_reminders = [(datetime(2026, 10, 21, 14, 0), "15min", "bills", "Internet Bill", 799, time(14, 0))]
    mock_weather = {"city": "Delhi", "temperature": 27.5, "condition_code": 0}

    with patch("api_v1.ensure_user"):
        with patch("api_v1.items_for_date", return_value=mock_items):
            with patch("api_v1.get_upcoming_reminders", return_value=mock_reminders):
                with patch("api_v1.get_free_weather", new_callable=AsyncMock, return_value=mock_weather):
                    with patch("api_v1.get_google_token", return_value=None):
                        res = client.get("/api/v1/workspace/office", headers={"X-Chat-Id": str(chat_id)})
                        assert res.status_code == 200
                        data = res.json()
                        assert "date" in data
                        assert "weather" in data
                        assert data["weather"]["city"] == "Delhi"
                        assert len(data["today_agenda"]) == 1
                        assert data["today_agenda"][0]["title"] == "Internet Bill"
                        assert data["stats"]["today_tasks_count"] == 1


def test_workspace_park_bubbles(client: TestClient):
    """The Park workspace organizes memories into categorized bubbles."""
    chat_id = 987654321
    mock_memories = [
        ("bills", "House Rent", 15000, date(2026, 11, 1), time(10, 0)),
        ("appointments", "Dr. Mehta Consultation", None, date(2026, 10, 22), time(17, 0)),
        ("general", "Buy milk and groceries", None, None, None)
    ]

    with patch("api_v1.ensure_user"):
        with patch("api_v1.list_memories", return_value=mock_memories):
            res = client.get("/api/v1/workspace/park", headers={"X-Chat-Id": str(chat_id)})
            assert res.status_code == 200
            data = res.json()
            bubbles = data["memory_bubbles"]
            assert len(bubbles["bills"]) == 1
            assert bubbles["bills"][0]["title"] == "House Rent"
            assert len(bubbles["appointments"]) == 1
            assert bubbles["appointments"][0]["title"] == "Dr. Mehta Consultation"
            assert len(bubbles["general"]) == 1
            assert data["total_active_memories"] == 3


def test_tools_status(client: TestClient):
    """Tools status endpoint displays all free integrations."""
    chat_id = 987654321
    with patch("api_v1.ensure_user"):
        with patch("api_v1.get_google_token", return_value=None):
            res = client.get("/api/v1/tools/status", headers={"X-Chat-Id": str(chat_id)})
            assert res.status_code == 200
            data = res.json()
            tool_ids = [t["id"] for t in data["integrations"]]
            assert "google" in tool_ids
            assert "outlook" in tool_ids
            assert "notion" in tool_ids
            assert "github" in tool_ids
            assert "weather" in tool_ids
            # Weather must always be free and active
            weather_tool = next(t for t in data["integrations"] if t["id"] == "weather")
            assert weather_tool["free"] is True
            assert weather_tool["connected"] is True


def test_document_trunk_upload_and_query(client: TestClient):
    """Uploading a document to the trunk extracts summary and permits natural queries."""
    chat_id = 987654321
    file_bytes = b"Sample text invoice contract between Client A and Vendor B for $5,000 due Nov 2026."

    with patch("api_v1.ensure_user"):
        with patch("api_v1.summarize_document_content", new_callable=AsyncMock, return_value="Invoice summary for $5,000"):
            res_upload = client.post(
                "/api/v1/chat/document",
                files={"document": ("invoice.txt", file_bytes, "text/plain")},
                headers={"X-Chat-Id": str(chat_id)}
            )
            assert res_upload.status_code == 200
            doc_data = res_upload.json()
            assert "doc_id" in doc_data
            assert doc_data["filename"] == "invoice.txt"
            doc_id = doc_data["doc_id"]

            # Query the document
            with patch("api_v1.answer_document_query", new_callable=AsyncMock, return_value="The total amount is $5,000."):
                res_q = client.post(
                    "/api/v1/chat/document/query",
                    json={"query": "What is the total amount?", "doc_id": doc_id},
                    headers={"X-Chat-Id": str(chat_id)}
                )
                assert res_q.status_code == 200
                assert "5,000" in res_q.json()["reply"]


def test_task_boards_crud(client: TestClient):
    """Test Kanban board endpoints: create, get, move, delete cards."""
    chat_id = 987654321
    mock_board = {
        "queue": [{"id": 1, "title": "Setup CI/CD", "status": "queue"}],
        "this_week": [],
        "today": [],
        "done": []
    }
    with patch("api_v1.ensure_user"):
        with patch("api_v1.get_board_cards", return_value=mock_board):
            # GET board
            res_get = client.get("/api/v1/boards", headers={"X-Chat-Id": str(chat_id)})
            assert res_get.status_code == 200
            assert "queue" in res_get.json()
            assert len(res_get.json()["queue"]) == 1

        with patch("api_v1.create_board_card", return_value=501) as mock_create:
            # POST card
            res_post = client.post(
                "/api/v1/boards/cards",
                json={"title": "Fix Auth Bug", "status": "today", "priority": "high"},
                headers={"X-Chat-Id": str(chat_id)}
            )
            assert res_post.status_code == 200
            assert res_post.json()["card_id"] == 501

        with patch("api_v1.move_board_card", return_value=True) as mock_move:
            # PATCH move card
            res_patch = client.patch(
                "/api/v1/boards/cards/501/move",
                json={"status": "done"},
                headers={"X-Chat-Id": str(chat_id)}
            )
            assert res_patch.status_code == 200
            assert res_patch.json()["new_status"] == "done"

        with patch("api_v1.delete_board_card", return_value=True) as mock_del:
            # DELETE card
            res_del = client.delete("/api/v1/boards/cards/501", headers={"X-Chat-Id": str(chat_id)})
            assert res_del.status_code == 200
            assert res_del.json()["status"] == "ok"


def test_smart_lists_crud(client: TestClient):
    """Test smart checklist endpoints: create list, add item, toggle item, delete."""
    chat_id = 987654321
    mock_lists = [
        {
            "id": 10,
            "name": "Grocery",
            "items": [{"id": 101, "title": "Milk", "checked": False}]
        }
    ]
    with patch("api_v1.ensure_user"):
        with patch("api_v1.get_smart_lists", return_value=mock_lists):
            res_get = client.get("/api/v1/lists", headers={"X-Chat-Id": str(chat_id)})
            assert res_get.status_code == 200
            assert len(res_get.json()["lists"]) == 1

        with patch("api_v1.create_smart_list", return_value=20):
            res_create = client.post(
                "/api/v1/lists",
                json={"name": "Goa Trip Packing"},
                headers={"X-Chat-Id": str(chat_id)}
            )
            assert res_create.status_code == 200
            assert res_create.json()["list_id"] == 20

        with patch("api_v1.add_smart_list_item", return_value=201):
            res_add = client.post(
                "/api/v1/lists/20/items",
                json={"title": "Sunscreen", "quantity": "1 bottle"},
                headers={"X-Chat-Id": str(chat_id)}
            )
            assert res_add.status_code == 200
            assert res_add.json()["item_id"] == 201

        with patch("api_v1.toggle_smart_list_item", return_value=True):
            res_toggle = client.patch(
                "/api/v1/lists/items/201/toggle",
                json={"checked": True},
                headers={"X-Chat-Id": str(chat_id)}
            )
            assert res_toggle.status_code == 200


def test_user_behavioral_rules(client: TestClient):
    """Test user behavioral rules learning endpoints."""
    chat_id = 987654321
    mock_rules = [{"id": 1, "rule": "Do not schedule meetings before 10 AM", "active": True}]
    with patch("api_v1.ensure_user"):
        with patch("api_v1.get_user_rules", return_value=mock_rules):
            res_get = client.get("/api/v1/user/rules", headers={"X-Chat-Id": str(chat_id)})
            assert res_get.status_code == 200
            assert len(res_get.json()["rules"]) == 1

        with patch("api_v1.add_user_rule", return_value=2):
            res_add = client.post(
                "/api/v1/user/rules",
                json={"rule": "Always format notes with bullet points"},
                headers={"X-Chat-Id": str(chat_id)}
            )
            assert res_add.status_code == 200
            assert res_add.json()["rule_id"] == 2

        with patch("api_v1.delete_user_rule", return_value=True):
            res_del = client.delete("/api/v1/user/rules/2", headers={"X-Chat-Id": str(chat_id)})
            assert res_del.status_code == 200


def test_serendipity_feed_and_action(client: TestClient):
    """Test serendipity feed to review older memories and keep/delete."""
    chat_id = 987654321
    mock_memories = [
        {"id": 88, "title": "Buy book on psychology", "category": "general", "saved_date": "2026-09-01"}
    ]
    with patch("api_v1.ensure_user"):
        with patch("api_v1.get_serendipity_memories", return_value=mock_memories):
            res_feed = client.get("/api/v1/feed/serendipity", headers={"X-Chat-Id": str(chat_id)})
            assert res_feed.status_code == 200
            assert len(res_feed.json()["feed"]) == 1

        # Keep action
        res_keep = client.post(
            "/api/v1/feed/serendipity/88/action",
            json={"action": "keep"},
            headers={"X-Chat-Id": str(chat_id)}
        )
        assert res_keep.status_code == 200
        assert res_keep.json()["action"] == "kept"

        # Delete action
        with patch("api_v1.delete_memory_items", return_value=1):
            res_delete = client.post(
                "/api/v1/feed/serendipity/88/action",
                json={"action": "delete"},
                headers={"X-Chat-Id": str(chat_id)}
            )
            assert res_delete.status_code == 200
            assert res_delete.json()["action"] == "deleted"


def test_briefings_morning_and_weekly(client: TestClient):
    """Test morning and weekly focus briefing generation."""
    chat_id = 987654321
    mock_weather = {"city": "Delhi", "temperature": 28.0}
    with patch("api_v1.ensure_user"):
        with patch("api_v1.get_free_weather", new_callable=AsyncMock, return_value=mock_weather):
            with patch("api_v1.items_for_date", return_value=[]):
                with patch("api_v1.get_db_user_id", return_value=None):
                    res_m = client.get("/api/v1/briefing/morning", headers={"X-Chat-Id": str(chat_id)})
                    assert res_m.status_code == 200
                    assert "briefing_text" in res_m.json()
                    assert res_m.json()["weather"]["city"] == "Delhi"

        with patch("api_v1.items_between", return_value=[]):
            res_w = client.get("/api/v1/briefing/weekly", headers={"X-Chat-Id": str(chat_id)})
            assert res_w.status_code == 200
            assert "weekly_strategy" in res_w.json()


def test_contacts_networking_memory(client: TestClient):
    """Test Enfolo-style relationship memory: list, create, delete."""
    chat_id = 987654321
    mock_contacts = [
        {
            "id": 1,
            "name": "Devendra",
            "company": "Peculiar",
            "role_title": "Founder",
            "met_at": "Bangalore Event",
            "context_notes": "Discussed AI agent architecture",
            "commitments": "Send draft API endpoints by Wednesday"
        }
    ]
    with patch("api_v1.ensure_user"):
        with patch("api_v1.get_contact_memories", return_value=mock_contacts):
            res_get = client.get("/api/v1/contacts", headers={"X-Chat-Id": str(chat_id)})
            assert res_get.status_code == 200
            assert len(res_get.json()["contacts"]) == 1
            assert res_get.json()["contacts"][0]["name"] == "Devendra"

        with patch("api_v1.save_contact_memory", return_value=2):
            res_post = client.post(
                "/api/v1/contacts",
                json={
                    "name": "Sarah Connor",
                    "company": "Tech Corp",
                    "role_title": "CTO",
                    "met_at": "Coffee Shop",
                    "context_notes": "Interested in AI automation",
                    "commitments": "Follow up next Friday"
                },
                headers={"X-Chat-Id": str(chat_id)}
            )
            assert res_post.status_code == 200
            assert res_post.json()["contact_id"] == 2

        with patch("api_v1.delete_contact_memory", return_value=True):
            res_del = client.delete("/api/v1/contacts/2", headers={"X-Chat-Id": str(chat_id)})
            assert res_del.status_code == 200

