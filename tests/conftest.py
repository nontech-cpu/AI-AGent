"""
Test configuration and fixtures for PeculiarAI test suite.
Sets up mock environment variables and deterministic test clients.
"""
import os
import sys

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
import pytest
from unittest.mock import MagicMock, AsyncMock
from fastapi.testclient import TestClient

# Ensure test environment variables are set before importing application modules
os.environ["TELEGRAM_BOT_TOKEN"] = "123456:TEST_TELEGRAM_BOT_TOKEN"
os.environ["WEBHOOK_SECRET"] = "test_webhook_secret_key_12345678"
os.environ["GROQ_API_KEY"] = "gsk_test_mock_groq_api_key"
os.environ["GROQ_MODEL"] = "openai/gpt-oss-120b"
os.environ["GROQ_STT_MODEL"] = "whisper-large-v3-turbo"
os.environ["GOOGLE_CLIENT_ID"] = "test-client-id.apps.googleusercontent.com"
os.environ["GOOGLE_CLIENT_SECRET"] = "test_google_client_secret"
os.environ["GOOGLE_TOKEN_KEY"] = "k1cM5_bU4mZJ2tW_jL9qP0rS8uV3wX6yA7bC4dE1fG8="  # Valid 32-byte Fernet key
os.environ["PUBLIC_BASE_URL"] = "https://peculiar-ai-backend.onrender.com"
os.environ["TELEGRAM_OTP_BOT_TOKEN"] = "123456:TEST_OTP_BOT_TOKEN"
os.environ["TELEGRAM_OTP_BOT_USERNAME"] = "PeculiarAI_Verify_Bot"
os.environ["PECULIARAI_WEB_VERIFY_DB"] = "test_web_verify.sqlite3"
os.environ["DATABASE_URL"] = "sqlite:///:memory:"

import main
import llm
import google_auth
import google_services
import multimodal


@pytest.fixture(scope="session")
def client():
    """FastAPI TestClient fixture."""
    with TestClient(main.app) as c:
        yield c


@pytest.fixture(autouse=True)
def clean_test_state():
    """Resets global in-memory state before each test."""
    main.PENDING_GOOGLE_ACTIONS.clear()
    main.RECENT_DOCS.clear()
    main.RECENT_CHAT_TURNS.clear()
    main.SEEN_UPDATES.clear()
    yield
    main.PENDING_GOOGLE_ACTIONS.clear()
    main.RECENT_DOCS.clear()
    main.RECENT_CHAT_TURNS.clear()
    main.SEEN_UPDATES.clear()
