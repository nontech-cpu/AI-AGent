"""
Integration and End-to-End Tests for the 6 Frontier Memorae Pillars:
1. pgvector Cosine Search
2. S3 / Cloudflare R2 Asset Pipeline
3. ReAct Step Streaming (SSE / NDJSON)
4. Model Context Protocol (MCP) Client Hub
5. Structured Memory Bubbles
6. ElevenLabs Audio Briefing Engine
"""
import pytest
import json
import asyncio
from fastapi.testclient import TestClient
from main import app
import db
from services.embeddings import embed_text, embed_query
from services.storage import upload_asset
from services.elevenlabs_service import clean_text_for_speech, synthesize_speech
from connectors.registry import get_connector

client = TestClient(app)
TEST_CHAT_ID = 1234828592


def test_frontier_get_me():
    """Verifies /api/me endpoint returns frontier status."""
    res = client.get("/api/me", headers={"X-Chat-Id": str(TEST_CHAT_ID)})
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "ready"
    assert "bubbles_count" in data
    assert data["bubbles_count"] >= 5
    assert data["version"] == "2.3.0-frontier"


def test_structured_memory_bubbles():
    """Verifies listing and creating structured memory bubbles."""
    # 1. List default bubbles
    res = client.get("/api/bubbles", headers={"X-Chat-Id": str(TEST_CHAT_ID)})
    assert res.status_code == 200
    bubbles = res.json()["bubbles"]
    slugs = [b["slug"] for b in bubbles]
    for expected in ["everything", "work", "travel", "health", "finance"]:
        assert expected in slugs

    # 2. Create custom bubble
    create_res = client.post(
        "/api/bubbles",
        headers={"X-Chat-Id": str(TEST_CHAT_ID)},
        json={"slug": "investments", "name": "Crypto & Stocks", "color_hex": "#10B981", "icon_name": "TrendingUp"}
    )
    assert create_res.status_code == 200
    assert create_res.json()["status"] == "success"

    # Verify custom bubble is listed
    res2 = client.get("/api/bubbles", headers={"X-Chat-Id": str(TEST_CHAT_ID)})
    slugs2 = [b["slug"] for b in res2.json()["bubbles"]]
    assert "investments" in slugs2


def test_pgvector_cosine_search():
    """Verifies pgvector insertion and cosine similarity retrieval."""
    # 1. Generate embeddings
    v1 = embed_text("Air India flight AI-102 to Delhi leaves at 06:00 AM terminal 3")
    assert v1 is not None
    assert len(v1) == 384

    # 2. Save memory item to travel bubble
    mem_id = db.save_bubble_memory(
        chat_id=TEST_CHAT_ID,
        content="Air India flight AI-102 from Mumbai to Delhi departs at 06:00 AM tomorrow from Terminal 3",
        title="Flight to Delhi AI-102",
        bubble_slug="travel"
    )
    assert mem_id is not None

    # 3. Perform cosine similarity search
    results = db.search_bubble_memories_cosine(
        chat_id=TEST_CHAT_ID,
        query_text="When is my flight to Delhi?",
        bubble_slug="travel",
        limit=5,
        min_similarity=0.40
    )
    assert len(results) > 0
    top = results[0]
    assert "delhi" in top["title"].lower() or "delhi" in top["content"].lower()
    assert top["bubble_slug"] == "travel"
    assert top["similarity"] >= 0.40


def test_s3_r2_asset_pipeline():
    """Verifies asset upload pipeline and persistence."""
    sample_bytes = b"%PDF-1.4 Mock Flight Ticket PDF content for testing asset storage"
    res = upload_asset(sample_bytes, "ticket.pdf", "application/pdf", user_id=TEST_CHAT_ID)
    assert "url" in res
    assert "storage_type" in res
    assert res["storage_type"] in ("r2", "s3", "local")
    assert "ticket.pdf" in res["filename"]


def test_mcp_client_and_registry():
    """Verifies MCP connector is registered and can discover and store servers."""
    conn = get_connector("mcp")
    assert conn is not None
    assert conn.name == "mcp"
    tools = conn.tools()
    tool_names = [t.name for t in tools]
    assert "list_mcp_servers" in tool_names
    assert "call_mcp_tool" in tool_names

    # Test REST MCP registration endpoint
    res = client.post(
        "/api/connectors/mcp",
        headers={"X-Chat-Id": str(TEST_CHAT_ID)},
        json={
            "server_name": "test_mcp_server",
            "server_url": "http://127.0.0.1:9999/mcp"
        }
    )
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "success"
    assert data["server_name"] == "test_mcp_server"

    # List MCP servers
    list_res = client.get("/api/connectors/mcp", headers={"X-Chat-Id": str(TEST_CHAT_ID)})
    assert list_res.status_code == 200
    servers = list_res.json()["servers"]
    assert any(s["server_name"] == "test_mcp_server" for s in servers)

    # Clean up test MCP server
    del_res = client.delete("/api/connectors/mcp/test_mcp_server", headers={"X-Chat-Id": str(TEST_CHAT_ID)})
    assert del_res.status_code == 200


def test_elevenlabs_and_audio_engine():
    """Verifies text cleanup and audio synthesis."""
    raw = "```python\nprint('hello')\n```\n# Header\n* Bullet item with [link](https://example.com)!"
    cleaned = clean_text_for_speech(raw)
    assert "```" not in cleaned
    assert "https://" not in cleaned
    assert "Bullet item with link" in cleaned

    # Test speech synthesis
    audio_bytes = asyncio.run(synthesize_speech("Hello, this is an executive audio test."))
    assert isinstance(audio_bytes, bytes)
    assert len(audio_bytes) > 1000  # Valid MP3 audio payload


def test_react_step_streaming_endpoint():
    """Verifies /api/assistant/chat streams reasoning steps in NDJSON format."""
    payload = {
        "message": "Devender ke sath meeting hai kal subah 10 baje",
        "chat_id": TEST_CHAT_ID,
        "bubble_slug": "work",
        "stream": True
    }
    response = client.post("/api/assistant/chat", json=payload)
    assert response.status_code == 200

    # Parse streamed NDJSON lines
    lines = [json.loads(line) for line in response.text.strip().split("\n") if line.strip()]
    assert len(lines) >= 4

    step_names = [item.get("name") for item in lines if item.get("type") == "step"]
    assert "Thinking" in step_names
    assert "Searching" in step_names
    assert "Checking" in step_names
    assert "Executing" in step_names

    # Check for delta or done
    types = [item.get("type") for item in lines]
    assert "delta" in types or "done" in types
