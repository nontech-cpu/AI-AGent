"""
Tests for Memorae Companion App REST APIs.
Validates Dashboard Overview, Reminders CRUD, Calendar Events, List Templates,
Tasks with AI Generation, Integrations cards, Emergency Vault, and Real-Time Sync.
"""
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from main import app
import db

client = TestClient(app)
TEST_CHAT_ID = 887766554


@pytest.fixture(autouse=True)
def setup_user():
    db.ensure_user(TEST_CHAT_ID)
    db.give_consent(TEST_CHAT_ID)
    db.set_persona(TEST_CHAT_ID, "Dev")


def test_dashboard_overview_endpoint():
    """Validates aggregated dashboard payload for Memorae app."""
    # Seed a reminder, list, and task card
    db.save_memory(TEST_CHAT_ID, "reminder", "Drink water at 10 AM", None, datetime.now().date(), None)
    list_id = db.create_smart_list(TEST_CHAT_ID, "Trip to Goa", "travel")
    db.add_smart_list_item(TEST_CHAT_ID, list_id, "Sunscreen")
    db.create_board_card(TEST_CHAT_ID, "Review project milestone", status="today", priority="high")

    response = client.get(f"/api/v1/dashboard/overview?chat_id={TEST_CHAT_ID}")
    assert response.status_code == 200
    data = response.json()

    # User profile & mascot
    assert "user_profile" in data
    assert "greeting" in data["user_profile"]
    assert "mascot" in data["user_profile"]
    assert "Aviator Hat" in data["user_profile"]["mascot"]["reward"]

    # Portal pills
    assert len(data["portal_pills"]) >= 4

    # Reminders card
    assert "reminders_card" in data
    assert any("Drink water" in str(it) for it in data["reminders_card"]["items"])

    # Calendars card
    assert "calendars_card" in data
    assert "events" in data["calendars_card"]

    # Lists card
    assert "lists_card" in data
    assert any(l["name"] == "Trip to Goa" for l in data["lists_card"]["lists"])

    # Tasks card
    assert "tasks_card" in data
    assert data["tasks_card"]["today_count"] >= 1


def test_reminders_crud_endpoints():
    """Validates filtered reminders retrieval, creation, patch, and deletion."""
    today_str = datetime.now().strftime("%Y-%m-%d")

    # 1. Create reminder via API
    create_resp = client.post(
        f"/api/v1/reminders?chat_id={TEST_CHAT_ID}",
        json={
            "title": "Pay Electricity Bill",
            "due_date": today_str,
            "due_time": "18:00",
            "category": "bill",
            "amount": 1450.0
        }
    )
    assert create_resp.status_code == 200
    rem_id = create_resp.json()["reminder_id"]
    assert rem_id is not None

    # 2. Get reminders for 'today'
    get_resp = client.get(f"/api/v1/reminders?chat_id={TEST_CHAT_ID}&section=today")
    assert get_resp.status_code == 200
    rems = get_resp.json()["reminders"]
    assert any(r["title"] == "Pay Electricity Bill" for r in rems)

    # 3. Patch reminder (complete)
    patch_resp = client.patch(
        f"/api/v1/reminders/{rem_id}?chat_id={TEST_CHAT_ID}",
        json={"completed": True}
    )
    assert patch_resp.status_code == 200

    # 4. Delete reminder
    del_resp = client.delete(f"/api/v1/reminders/{rem_id}?chat_id={TEST_CHAT_ID}")
    assert del_resp.status_code == 200


def test_calendar_events_and_creation():
    """Validates calendar events endpoint with fallback to local items."""
    now = datetime.now()
    now_iso = now.isoformat()

    # 1. Create event
    create_resp = client.post(
        f"/api/v1/calendar/events?chat_id={TEST_CHAT_ID}",
        json={
            "title": "Architecture Sync",
            "start_iso": now_iso,
            "duration_minutes": 45,
            "description": "Discuss Memorae app APIs"
        }
    )
    assert create_resp.status_code == 200

    # 2. Query calendar events for current date
    query_resp = client.get(
        f"/api/v1/calendar/events?chat_id={TEST_CHAT_ID}&date={now.strftime('%Y-%m-%d')}"
    )
    assert query_resp.status_code == 200
    data = query_resp.json()
    assert "events" in data
    assert "minutes_booked" in data


def test_lists_from_template():
    """Validates creating pre-bundled template lists (e.g. Packing List for travel)."""
    resp = client.post(
        f"/api/v1/lists/from-template?chat_id={TEST_CHAT_ID}",
        json={
            "template_id": "packing_list",
            "custom_name": "Goa Trip Packing List"
        }
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Goa Trip Packing List"
    assert data["items_count"] >= 5
    assert any("Passport" in it["title"] or "Charger" in it["title"] for it in data["items"])


def test_tasks_endpoints_and_ai_generation():
    """Validates Kanban task listing, creation, and 'Generate with Memorae' AI synthesis."""
    # 1. Add manual task
    add_resp = client.post(
        f"/api/v1/tasks?chat_id={TEST_CHAT_ID}",
        json={
            "title": "Design App Navigation Dock",
            "status": "this_week",
            "priority": "high"
        }
    )
    assert add_resp.status_code == 200

    # 2. Get tasks
    get_resp = client.get(f"/api/v1/tasks?chat_id={TEST_CHAT_ID}&tab=this_week")
    assert get_resp.status_code == 200
    assert any("Design App Navigation Dock" in t["title"] for t in get_resp.json()["tasks"])

    # 3. Test Generate with Memorae
    ai_gen_resp = client.post(f"/api/v1/tasks/generate-ai?chat_id={TEST_CHAT_ID}")
    assert ai_gen_resp.status_code == 200
    assert ai_gen_resp.json()["generated_count"] >= 1


def test_integrations_and_activity_ledger():
    """Validates Integrations cards and audit activity ledger."""
    integ_resp = client.get(f"/api/v1/integrations?chat_id={TEST_CHAT_ID}")
    assert integ_resp.status_code == 200
    cards = integ_resp.json()["cards"]
    card_ids = [c["id"] for c in cards]
    assert "google_workspace" in card_ids
    assert "google_calendar" in card_ids
    assert "github" in card_ids
    assert "notion" in card_ids

    ledger_resp = client.get(f"/api/v1/activity-ledger?chat_id={TEST_CHAT_ID}")
    assert ledger_resp.status_code == 200
    assert "entries" in ledger_resp.json()


def test_emergency_travel_vault():
    """Validates Phone IMEI and emergency travel vault saving."""
    # Save IMEI (*#06#)
    save_resp = client.post(
        f"/api/v1/vault/emergency?chat_id={TEST_CHAT_ID}",
        json={
            "item_type": "imei",
            "title": "Primary Phone IMEI",
            "value": "864920194820194",
            "notes": "Emergency travel insurance document"
        }
    )
    assert save_resp.status_code == 200

    # Fetch emergency vault
    get_resp = client.get(f"/api/v1/vault/emergency?chat_id={TEST_CHAT_ID}")
    assert get_resp.status_code == 200
    items = get_resp.json()["items"]
    assert any("864920194820194" in it["title"] for it in items)


def test_serve_companion_app_ui():
    """Validates that /app and /companion serve the rich interactive web UI."""
    resp = client.get("/app")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")
    assert "Memorae — Personal AI Companion" in resp.text
    assert "portal-hero" in resp.text
    assert "mascot-sphere" in resp.text

