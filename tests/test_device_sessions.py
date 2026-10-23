import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from db import (
    ensure_user,
    record_device_session,
    get_device_session,
    check_device_authorization,
    save_google_token,
)
from google_auth import encrypt_token
from main import app, _create_web_verification, _init_web_verify_db
import sqlite3
import os


@pytest.fixture
def client():
    return TestClient(app)


@pytest.fixture(autouse=True)
def init_test_db():
    from db import engine
    from sqlalchemy import text
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_chat_id INTEGER UNIQUE NOT NULL,
                consented_at TIMESTAMP,
                state TEXT,
                state_data TEXT,
                persona TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS google_tokens (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER UNIQUE NOT NULL,
                email TEXT NOT NULL,
                refresh_token_enc TEXT NOT NULL,
                scopes TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """))
    yield


def test_device_session_db_helpers():
    """Verify record_device_session and check_device_authorization logic."""
    chat_id = 999111222
    ensure_user(chat_id)
    from db import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        u_row = conn.execute(
            text("SELECT id FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id}
        ).first()
    user_id = int(u_row[0])

    dev1 = "device_alpha"
    dev2 = "device_beta"

    # Initially no session
    check1 = check_device_authorization(user_id, dev1)
    assert check1["valid"] is False
    assert check1["reason"] == "no_session"

    # Record session on dev1
    record_device_session(user_id, phone="+919999999991", device_id=dev1, google_email="user1@gmail.com")
    sess1 = get_device_session(user_id, dev1)
    assert sess1 is not None
    assert sess1["google_email"] == "user1@gmail.com"

    # Same device -> valid
    check2 = check_device_authorization(user_id, dev1)
    assert check2["valid"] is True
    assert check2["reason"] == "valid"

    # Different device (dev2) -> adopts existing authorized session for this phone user
    check3 = check_device_authorization(user_id, dev2)
    assert check3["valid"] is True
    assert check3["reason"] in ("valid", "adopted")
    assert check3["google_email"] == "user1@gmail.com"


def test_web_google_status_device_isolation(client):
    """Test /web/google/status enforcing phone number isolation and seamless device access."""
    # User A setup
    chat_a = 888111001
    ensure_user(chat_a)
    from db import engine
    from sqlalchemy import text
    with engine.connect() as conn:
        u_a = conn.execute(text("SELECT id FROM users WHERE telegram_chat_id = :c"), {"c": chat_a}).first()
    user_a_id = int(u_a[0])

    token_enc = encrypt_token("mock_refresh_token_a")
    save_google_token(user_a_id, "usera@example.com", token_enc, "openid")

    # Create verification session for User A with phone A and Device 1
    token_a, _ = _create_web_verification("+919000000001", device_id="device_laptop")
    with sqlite3.connect(os.getenv("PECULIARAI_WEB_VERIFY_DB", "peculiarai_web_verify.sqlite3")) as conn:
        conn.execute("UPDATE web_verifications SET verified = 1, telegram_chat_id = ? WHERE token = ?", (str(chat_a), token_a))
        conn.commit()

    # User A records device session on device_laptop
    record_device_session(user_a_id, "+919000000001", "device_laptop", "usera@example.com")

    # Mock get_valid_google_connection_by_chat_id so tests don't make real external Google network calls
    async def mock_valid_conn(cid):
        if cid == chat_a:
            return True, "usera@example.com"
        return False, None

    with patch("main.get_valid_google_connection_by_chat_id", side_effect=mock_valid_conn):
        # 1. User A checks from same device (device_laptop) -> connected: True
        res1 = client.post("/web/google/status", json={"verification_id": token_a, "device_id": "device_laptop"})
        assert res1.status_code == 200
        data1 = res1.json()
        assert data1["connected"] is True
        assert data1["email"] == "usera@example.com"
        assert data1["requires_relogin"] is False

        # 2. User A opens from different device (device_phone) -> automatically adopts User A's connected account!
        res2 = client.post("/web/google/status", json={"verification_id": token_a, "device_id": "device_phone"})
        assert res2.status_code == 200
        data2 = res2.json()
        assert data2["connected"] is True
        assert data2["email"] == "usera@example.com"
        assert data2["requires_relogin"] is False

        # 3. Friend (User B) logs in with friend's phone number on the same device (device_laptop)
        chat_b = 888111002
        ensure_user(chat_b)
        token_b, _ = _create_web_verification("+919000000002", device_id="device_laptop")
        with sqlite3.connect(os.getenv("PECULIARAI_WEB_VERIFY_DB", "peculiarai_web_verify.sqlite3")) as conn:
            conn.execute("UPDATE web_verifications SET verified = 1, telegram_chat_id = ? WHERE token = ?", (str(chat_b), token_b))
            conn.commit()

        res3 = client.post("/web/google/status", json={"verification_id": token_b, "device_id": "device_laptop"})
        assert res3.status_code == 200
        data3 = res3.json()
        # User B has never connected Google, so it must NOT leak User A's email or connection!
        assert data3["connected"] is False
        assert data3["email"] is None
        assert data3["requires_relogin"] is False
