"""
PeculiarAI Frontier Memorae Architecture — Live Verification Suite
Tests all 6 Pillars live against the server and database:
  [1] pgvector Cosine Search
  [2] S3 / Cloudflare R2 Asset Storage
  [3] ReAct Step Streaming (NDJSON)
  [4] Model Context Protocol (MCP) Hub
  [5] Structured Memory Bubbles
  [6] ElevenLabs Audio Briefing Engine
"""
import sys
import os
import json
import time

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass

import httpx

BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8000")
TEST_CHAT_ID = 1234828592

PASSED = "[PASS]"
FAILED = "[FAIL]"

def header(title: str):
    print("\n" + "=" * 65)
    print(f"  {title}")
    print("=" * 65)

def check(name: str, success: bool, detail: str = ""):
    status = f"\033[92m{PASSED}\033[0m" if success else f"\033[91m{FAILED}\033[0m"
    detail_str = f" -> {detail}" if detail else ""
    print(f" {status} {name}{detail_str}")
    if not success:
        return False
    return True

def run_all_tests():
    total = 0
    passed = 0

    print("\n[+] Starting PeculiarAI Frontier 6-Pillars Live Verification...")
    print(f"[+] Target Server: {BASE_URL}")

    client = httpx.Client(timeout=httpx.Timeout(45.0, connect=10.0, read=45.0))

    # ---------------------------------------------------------
    # 0. Health & Version Check
    # ---------------------------------------------------------
    header("0. Server Version & System Health")
    total += 1
    try:
        r = client.get(f"{BASE_URL}/api/me", headers={"X-Chat-Id": str(TEST_CHAT_ID)})
        if r.status_code == 200:
            data = r.json()
            ver = data.get("version")
            spans = data.get("active_timeline_spans", 0)
            check("Server Status & Version", True, f"Version: {ver} | Active Timeline Spans: {spans}")
            passed += 1
        else:
            check("Server Status & Version", False, f"HTTP {r.status_code}: {r.text}")
    except Exception as e:
        check("Server Status & Version", False, str(e))

    # ---------------------------------------------------------
    # 1. Structured Memory Bubbles (Pillar 5)
    # ---------------------------------------------------------
    header("1. Structured Memory Bubbles (Pillar 5)")
    total += 1
    try:
        r = client.get(f"{BASE_URL}/api/bubbles", headers={"X-Chat-Id": str(TEST_CHAT_ID)})
        if r.status_code == 200:
            bubbles = r.json().get("bubbles", [])
            slugs = [b["slug"] for b in bubbles]
            expected = ["everything", "work", "travel", "health", "finance"]
            has_all = all(s in slugs for s in expected)
            check("Default 5 Memory Bubbles", has_all, f"Found: {slugs}")
            if has_all:
                passed += 1
        else:
            check("Default 5 Memory Bubbles", False, f"HTTP {r.status_code}")
    except Exception as e:
        check("Default 5 Memory Bubbles", False, str(e))

    total += 1
    try:
        # Create custom bubble
        create_res = client.post(
            f"{BASE_URL}/api/bubbles",
            headers={"X-Chat-Id": str(TEST_CHAT_ID)},
            json={"slug": "research", "name": "Deep Research", "color_hex": "#8B5CF6", "icon_name": "Cpu"}
        )
        ok = create_res.status_code == 200 and create_res.json().get("status") == "success"
        check("Custom Memory Bubble Creation", ok, "Created slug: 'research' with color #8B5CF6")
        if ok:
            passed += 1
    except Exception as e:
        check("Custom Memory Bubble Creation", False, str(e))

    # ---------------------------------------------------------
    # 2. pgvector Cosine Search (Pillar 1)
    # ---------------------------------------------------------
    header("2. pgvector Cosine Search Engine (Pillar 1)")
    total += 1
    try:
        import db
        # Save a test vector memory into health bubble
        t_id = db.save_bubble_memory(
            chat_id=TEST_CHAT_ID,
            content="Dr. Verma clinic appointment on Thursday 11 AM for Vitamin D and B12 injection",
            title="Dr. Verma Clinic Appointment",
            bubble_slug="health"
        )
        # Search via cosine similarity
        results = db.search_bubble_memories_cosine(
            chat_id=TEST_CHAT_ID,
            query_text="When is my appointment with Dr Verma for injections?",
            bubble_slug="health",
            limit=3,
            min_similarity=0.40
        )
        if results and len(results) > 0:
            top = results[0]
            sim_pct = round(top["similarity"] * 100, 1)
            check("pgvector Cosine Retrieval", True, f"Top Match: '{top['title']}' | Similarity: {sim_pct}% in '{top['bubble_slug']}' bubble")
            passed += 1
        else:
            check("pgvector Cosine Retrieval", False, "No memory retrieved above threshold")
    except Exception as e:
        check("pgvector Cosine Retrieval", False, str(e))

    # ---------------------------------------------------------
    # 3. S3 / Cloudflare R2 Asset Pipeline (Pillar 2)
    # ---------------------------------------------------------
    header("3. S3 / Cloudflare R2 Asset Storage Pipeline (Pillar 2)")
    total += 1
    try:
        from services.storage import upload_asset
        mock_pdf = b"%PDF-1.4 Mock Flight Ticket PDF AI-102 BOM to DEL"
        res = upload_asset(mock_pdf, "flight_ticket.pdf", "application/pdf", user_id=TEST_CHAT_ID)
        ok = "url" in res and res.get("storage_type") in ("r2", "s3", "local")
        check("Asset Persistence Pipeline", ok, f"Saved URL: {res.get('url')} | Storage: {res.get('storage_type').upper()}")
        if ok:
            passed += 1
    except Exception as e:
        check("Asset Persistence Pipeline", False, str(e))

    # ---------------------------------------------------------
    # 4. Model Context Protocol (MCP) Client Hub (Pillar 4)
    # ---------------------------------------------------------
    header("4. Model Context Protocol (MCP) Client Hub (Pillar 4)")
    total += 1
    try:
        # Register a test MCP server
        reg_res = client.post(
            f"{BASE_URL}/api/connectors/mcp",
            headers={"X-Chat-Id": str(TEST_CHAT_ID)},
            json={"server_name": "internal_test_mcp", "server_url": "http://127.0.0.1:9876/mcp"}
        )
        # List MCP servers
        list_res = client.get(f"{BASE_URL}/api/connectors/mcp", headers={"X-Chat-Id": str(TEST_CHAT_ID)})
        servers = list_res.json().get("servers", [])
        mcp_found = any(s["server_name"] == "internal_test_mcp" for s in servers)
        # Delete test MCP server
        client.delete(f"{BASE_URL}/api/connectors/mcp/internal_test_mcp", headers={"X-Chat-Id": str(TEST_CHAT_ID)})
        check("MCP Server Dynamic Registry", mcp_found, "Discovered, registered and unregistered MCP server")
        if mcp_found:
            passed += 1
    except Exception as e:
        check("MCP Server Dynamic Registry", False, str(e))

    # ---------------------------------------------------------
    # 5. ElevenLabs Audio Briefing Engine (Pillar 6)
    # ---------------------------------------------------------
    header("5. ElevenLabs Audio Briefing Engine (Pillar 6)")
    total += 1
    try:
        tts_res = client.post(
            f"{BASE_URL}/api/assistant/tts",
            json={"text": "Good morning. Here is your executive schedule for today with zero sycophancy."}
        )
        if tts_res.status_code == 200 and len(tts_res.content) > 1000:
            size_kb = round(len(tts_res.content) / 1024, 1)
            check("Text-to-Speech Synthesis", True, f"Synthesized {size_kb} KB MP3 audio stream")
            passed += 1
        else:
            check("Text-to-Speech Synthesis", False, f"HTTP {tts_res.status_code}")
    except Exception as e:
        check("Text-to-Speech Synthesis", False, str(e))

    # ---------------------------------------------------------
    # 6. ReAct Step Streaming (Pillar 3)
    # ---------------------------------------------------------
    header("6. ReAct Step Streaming (SSE/NDJSON) (Pillar 3)")
    total += 1
    try:
        print(" [*] Connecting to /api/assistant/chat streaming endpoint...")
        stream_steps = []
        stream_deltas = []
        is_done = False

        with client.stream(
            "POST",
            f"{BASE_URL}/api/assistant/chat",
            json={"message": "Devender ke sath meeting kal subah 10 baje hai", "chat_id": TEST_CHAT_ID, "bubble_slug": "work", "stream": True}
        ) as resp:
            if resp.status_code == 200:
                for line in resp.iter_lines():
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                        ev_type = ev.get("type")
                        if ev_type == "step":
                            s_name = ev.get("name")
                            s_status = ev.get("status")
                            s_detail = ev.get("detail", "")
                            stream_steps.append(s_name)
                            print(f"     -> [Step: {s_name}] ({s_status}) {s_detail}")
                        elif ev_type == "delta":
                            stream_deltas.append(ev.get("content", ""))
                        elif ev_type == "done":
                            is_done = True
                            print("     -> [Step: Complete] Final response streamed!")
                    except Exception:
                        pass
        
        has_required_steps = any("Thinking" in s for s in stream_steps) and any("Searching" in s for s in stream_steps) and any("Checking" in s for s in stream_steps)
        streaming_ok = has_required_steps and len(stream_deltas) > 0 and is_done
        check("ReAct Reasoning Step Streaming", streaming_ok, f"Streamed {len(stream_steps)} steps & {len(stream_deltas)} token chunks")
        if streaming_ok:
            passed += 1
    except Exception as e:
        check("ReAct Reasoning Step Streaming", False, str(e))

    # ---------------------------------------------------------
    # Final Scorecard
    # ---------------------------------------------------------
    print("\n" + "=" * 65)
    pct = round((passed / total) * 100)
    print(f"  SCORECARD: {passed}/{total} Passed ({pct}%)")
    print("=" * 65)

    if passed == total:
        print("\033[92m[SUCCESS] ALL 6 FRONTIER MEMORAE PILLARS ARE FULLY FUNCTIONAL!\033[0m\n")
    else:
        print(f"\033[91m[WARNING] {total - passed} checks had errors.\033[0m\n")


if __name__ == "__main__":
    run_all_tests()

