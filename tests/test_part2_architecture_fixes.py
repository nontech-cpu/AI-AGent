"""
Regression & verification suite for Part 2 Industry-Grade Architectural Fixes:
1. Per-credential independent circuit-breaker cooldowns (Gemini 429 never kills GeminiBackup).
2. GroqBackup credential registration & automatic failover.
3. Semantic tool budget selection replacing keyword filters (find_nearby_places, list_notion_pages preserved).
4. Action ledger auto entity-linking, fallback entity-name lookup, profile dedup, and per-section memory budgets.
5. Context-preserving / fail-closed recovery in run_agent_turn (never runs amnesiac blind prompt).
6. Single-writer chat_logs idempotency between main.py and agent/loop.py.
"""
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import text

import db
import main
import core.timeutil as timeutil
from timing import IST
import llm.provider as llm_provider
from llm.provider import RateLimitedError, LLMResponse, get_available_providers, reset_provider_cooldowns
from agent.tools import TOOL_DEFINITIONS, execute_tool, select_semantic_tools_for_budget
from agent.loop import run_agent_turn
from memory.store import (
    resolve_user_id,
    save_entity,
    save_memory_item,
    record_action_event,
    get_recent_actions_for_user,
    get_core_profile,
    invalidate_memory_caches,
)
from memory.retrieval import assemble_memory_context


@pytest.fixture(autouse=True)
def _clean_state():
    reset_provider_cooldowns()
    invalidate_memory_caches()
    yield
    reset_provider_cooldowns()
    invalidate_memory_caches()


@pytest.mark.asyncio
async def test_gemini_backup_not_poisoned_by_primary_429():
    """Primary Gemini quota exhaustion must never cool down or skip GeminiBackup."""
    call_log = []

    async def mock_post(url, headers=None, json=None, **kwargs):
        auth = (headers or {}).get("Authorization", "")
        model = (json or {}).get("model", "")
        call_log.append((url, auth, model))
        resp = MagicMock()
        if "primary_gem_key" in auth:
            resp.status_code = 429
            resp.text = '[{"error":{"code":429,"message":"You exceeded your current quota, please check your plan and billing details."}}]'
            resp.headers = {}
            return resp
        if "backup_gem_key" in auth:
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"role": "assistant", "content": "Backup Gemini succeeded"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 4, "total_tokens": 9},
            }
            return resp
        resp.status_code = 500
        resp.text = "Unexpected call"
        return resp

    with patch.dict("os.environ", {
        "GEMINI_API_KEY": "primary_gem_key",
        "GEMINI_API_KEY_BACKUP": "backup_gem_key",
        "GROQ_API_KEY": "primary_groq_key",
        "USE_FAKE_LLM": "0",
        "LLM_PROVIDER_CHAIN": "",
    }, clear=False), patch("httpx.AsyncClient.post", side_effect=mock_post):
        providers = [p.name for p in get_available_providers()]
        assert providers[:3] == ["Gemini", "GeminiBackup", "Groq"]

        resp1 = await llm_provider.chat(messages=[{"role": "user", "content": "hi"}], max_retries_per_provider=0)
        assert resp1.provider == "GeminiBackup"
        assert resp1.text == "Backup Gemini succeeded"

        # Second turn: primary Gemini should be cooled down cleanly, while GeminiBackup responds immediately
        calls_before_t2 = len(call_log)
        resp2 = await llm_provider.chat(messages=[{"role": "user", "content": "hello again"}], max_retries_per_provider=0)
        assert resp2.provider == "GeminiBackup"
        assert any("backup_gem_key" in c[1] for c in call_log[calls_before_t2:])
        assert not any("primary_gem_key" in c[1] for c in call_log[calls_before_t2:])


@pytest.mark.asyncio
async def test_groq_backup_provider_registered_and_failover_works():
    """GROQ_API_KEY_BACKUP must be registered as GroqBackup and handle failover cleanly."""
    seen_auths = []

    async def mock_post(url, headers=None, json=None, **kwargs):
        auth = (headers or {}).get("Authorization", "")
        seen_auths.append(auth)
        resp = MagicMock()
        if "groq_primary_key" in auth:
            resp.status_code = 429
            resp.text = "Rate limit reached for model in organization (TPM)"
            resp.headers = {}
            return resp
        if "groq_backup_key" in auth:
            resp.status_code = 200
            resp.json.return_value = {
                "choices": [{"message": {"role": "assistant", "content": "GroqBackup healthy response"}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 5, "total_tokens": 10},
            }
            return resp
        resp.status_code = 500
        resp.text = "Unexpected provider"
        return resp

    with patch.dict("os.environ", {
        "GEMINI_API_KEY": "",
        "GEMINI_API_KEY_BACKUP": "",
        "GROQ_API_KEY": "groq_primary_key",
        "GROQ_API_KEY_BACKUP": "groq_backup_key",
        "USE_FAKE_LLM": "0",
        "LLM_PROVIDER_CHAIN": "",
    }, clear=False), patch("httpx.AsyncClient.post", side_effect=mock_post):
        names = [p.name for p in get_available_providers()]
        assert "Groq" in names
        assert "GroqBackup" in names

        resp = await llm_provider.chat(messages=[{"role": "user", "content": "status?"}], max_retries_per_provider=0)
        assert resp.provider == "GroqBackup"
        assert resp.text == "GroqBackup healthy response"
        assert any("groq_backup_key" in a for a in seen_auths)


def test_semantic_tool_budget_keeps_places_and_notion_lookup_tools():
    """Groq tool budget compaction must retain places/web/notion lookup tools without keyword hacks."""
    tools_burger = select_semantic_tools_for_budget(
        [{"role": "user", "content": "good burger the resturants in jodhpur"}],
        TOOL_DEFINITIONS,
        max_tools=24,
    )
    burger_names = {t["function"]["name"] for t in tools_burger}
    assert len(tools_burger) <= 24
    assert "find_nearby_places" in burger_names
    assert "search_web" in burger_names

    # Notice: no word "notion" or "page" in query, yet lookup tools must remain available
    tools_section = select_semantic_tools_for_budget(
        [{"role": "user", "content": "U created the section for me today only"}],
        TOOL_DEFINITIONS,
        max_tools=24,
    )
    section_names = {t["function"]["name"] for t in tools_section}
    assert len(tools_section) <= 24
    assert "list_notion_pages" in section_names
    assert "search_memory" in section_names


@pytest.mark.asyncio
async def test_tool_alias_find_nearby_place_normalization():
    """Singular 'find_nearby_place' emitted by smaller fallback models routes to 'find_nearby_places'."""
    with patch("utilities.search_web", new_callable=AsyncMock, return_value={"results": [{"title": "Burger Spot", "snippet": "Fresh burgers", "url": "https://example.com"}]}):
        res = await execute_tool(99887733, "find_nearby_place", {"category": "burger", "city": "Jodhpur"})
        assert res["status"] == "success"
        assert res["city"] == "Jodhpur"


def test_action_ledger_entity_autolink_dedup_and_section_budgets():
    """
    Verifies:
    - Core profile deduplicates repeated near-identical preference strings.
    - record_action_event auto-resolves known entity names even without explicit subject_name.
    - get_recent_actions_for_user finds historical events mentioning entity name even if subject_entity_id is NULL.
    - assemble_memory_context guarantees [Recent Confirmed Actions & Events] fits inside <= 2400 chars.
    """
    chat_id = 99887744
    db.ensure_user(chat_id)
    uid = resolve_user_id(chat_id)
    ent_id = save_entity(uid, "Devender", entity_type="person")

    # Save near-duplicate profile facts (should be deduplicated in core profile)
    for variant in [
        "User prefers to communicate in English.",
        "User prefers to communicate in English only.",
        "User prefers to communicate in English instead of Hinglish.",
    ]:
        save_memory_item(uid, variant, memory_type="profile", importance=5)

    profile = get_core_profile(uid)
    assert profile.lower().count("communicate in english") == 1

    # Record action without explicit subject_name -> auto-links to Devender
    record_action_event(
        chat_id,
        action_type="send_email",
        summary="Sent email to devender@example.com with Subject: 'Resignation Document - Devender Gehlot'",
        source_type="email",
        source_id="email_dev_auto_1",
    )

    # Also insert a historical action event with NULL subject_entity_id, plus calendar delete noise
    with db.engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO memories (user_id, memory_type, subject_entity_id, content, status, importance) "
                "VALUES (:uid, 'event', NULL, :content, 'active', 3)"
            ),
            {"uid": uid, "content": "Updated Notion page 'FastAPI Production Checklist'"},
        )
        for idx in range(7):
            conn.execute(
                text(
                    "INSERT INTO memories (user_id, memory_type, subject_entity_id, content, status, importance) "
                    "VALUES (:uid, 'action_event', NULL, :content, 'active', 2)"
                ),
                {"uid": uid, "content": f"Deleted Google Calendar meeting 'cal_noise_{idx}'"},
            )
    invalidate_memory_caches(uid)

    dev_actions = get_recent_actions_for_user(uid, entity_id=ent_id, limit=5)
    assert any("Resignation Document - Devender Gehlot" in a["summary"] for a in dev_actions)

    ctx = assemble_memory_context(chat_id, "What did you do today?")
    block = ctx.get("formatted_block", "")
    assert len(block) <= 2400
    assert "[Recent Confirmed Actions & Events]:" in block
    assert "FastAPI Production Checklist" in block
    assert "Resignation Document - Devender Gehlot" in block
    assert block.lower().count("communicate in english") == 1


@pytest.mark.asyncio
async def test_recovery_fails_closed_on_llm_error_and_preserves_context_otherwise():
    """
    1. When main LLM fails with RateLimitedError/llm_error, recovery must NOT invoke a second contextless LLM call.
    2. When main LLM returns empty text cleanly, recovery receives verified Schedule/Memory CONTEXT.
    """
    chat_id = 99887755
    db.ensure_user(chat_id)
    db.set_user_preference(chat_id, reply_language="english")
    tomorrow = (timeutil.now(IST) + timedelta(days=1)).date()
    db.save_memory(chat_id, "appointments", "Morning Milk Delivery", None, tomorrow, datetime.strptime("08:00", "%H:%M").time(), 30)

    # Case 1: All providers fail with 429 in iteration 0 -> fails closed, 1 llm_provider.chat call only
    with patch("llm.provider.chat", new_callable=AsyncMock, side_effect=RateLimitedError("429 Quota", provider="Groq")) as mock_chat:
        res1 = await run_agent_turn(chat_id, "any reminder for me tomorrow?")
        assert mock_chat.call_count == 1
        assert "what would you like me to remind" not in res1["reply"].lower()
        assert "traffic" in res1["reply"].lower() or "server" in res1["reply"].lower()

    # Case 2: Main iteration returns empty text without error -> recovery receives CONTEXT block with Milk reminder
    captured_rec_prompts = []

    async def mock_empty_then_grounded(messages=None, **kwargs):
        sys_txt = messages[0]["content"] if messages else ""
        if "Use ONLY the grounded schedule" in sys_txt:
            captured_rec_prompts.append(sys_txt)
            return LLMResponse(text="You have Morning Milk Delivery tomorrow at 8:00 AM.", provider="Gemini", model="gemini-3.5-flash")
        return LLMResponse(text="", tool_calls=[], provider="Gemini", model="gemini-3.5-flash")

    with patch("llm.provider.chat", side_effect=mock_empty_then_grounded):
        res2 = await run_agent_turn(chat_id, "any reminder for me tomorrow?")
        assert len(captured_rec_prompts) == 1
        assert "Morning Milk Delivery" in captured_rec_prompts[0]
        assert "Morning Milk Delivery" in res2["reply"]


@pytest.mark.asyncio
async def test_single_writer_chat_logs_no_duplicates_in_v2():
    """Telegram handle_user_text + run_agent_turn must insert each user/assistant turn only once in chat_logs."""
    chat_id = 99887766
    db.ensure_user(chat_id)
    db.give_consent(chat_id)
    db.set_persona(chat_id, "job")
    db.set_state(chat_id, "active")
    uid = resolve_user_id(chat_id)

    with db.engine.begin() as conn:
        conn.execute(text("DELETE FROM chat_logs WHERE user_id = :u"), {"u": uid})

    mock_resp = LLMResponse(text="Hello Nirav, single writer confirmed.", provider="Gemini", model="gemini-3.5-flash")
    with patch.dict("os.environ", {"AGENT_CORE": "v2"}, clear=False), \
         patch("llm.provider.chat", new_callable=AsyncMock, return_value=mock_resp), \
         patch("main.tg", new_callable=AsyncMock):
        await main.handle_user_text(chat_id, "test single writer message")
        # Let background persist task run if scheduled
        import asyncio
        await asyncio.sleep(0.05)

    with db.engine.connect() as conn:
        rows = conn.execute(
            text("SELECT role, message FROM chat_logs WHERE user_id = :u ORDER BY id ASC"),
            {"u": uid},
        ).fetchall()

    user_rows = [r for r in rows if r[0] == "user" and r[1] == "test single writer message"]
    asst_rows = [r for r in rows if r[0] == "assistant" and "single writer confirmed" in r[1]]
    assert len(user_rows) == 1, f"Expected 1 user row, got {len(user_rows)}: {rows}"
    assert len(asst_rows) == 1, f"Expected 1 assistant row, got {len(asst_rows)}: {rows}"


def test_embed_with_gemini_falls_back_across_models_on_same_key():
    """429 on one Gemini embedding model must fall back to the other model on the same key before switching keys."""
    from memory.store import _embed_with_gemini, _EMBED_MODEL_COOLDOWNS
    _EMBED_MODEL_COOLDOWNS.clear()
    seen_urls = []

    def fake_post(url, json=None, timeout=None):
        seen_urls.append(url)
        resp = MagicMock()
        if "gemini-embedding-001" in url:
            resp.status_code = 429
            resp.text = "rate limited"
            return resp
        if "gemini-embedding-2-preview" in url:
            resp.status_code = 200
            resp.json.return_value = {"embedding": {"values": [0.1] * 384}}
            return resp
        resp.status_code = 404
        return resp

    fake_sess = MagicMock()
    fake_sess.post.side_effect = fake_post
    with patch.dict("os.environ", {"GEMINI_API_KEY": "k1", "GEMINI_API_KEY_BACKUP": "k2"}, clear=False), \
         patch("memory.store._get_embed_session", return_value=fake_sess):
        vec = _embed_with_gemini("test reminder lookup")

    assert vec is not None and len(vec) == 384
    assert any("gemini-embedding-001" in u and "k1" in u for u in seen_urls)
    assert any("gemini-embedding-2-preview" in u and "k1" in u for u in seen_urls)


@pytest.mark.asyncio
async def test_groq_payload_compaction_prevents_413_and_retries_on_413():
    """GroqProvider must compact oversized system+tool payloads under 8k TPM and retry smaller on HTTP 413."""
    import json
    from llm.provider import GroqProvider

    big_sys = (
        "Current Time: 07:00 AM\n"
        + ("Rule guidance sentence (e.g. verbose sample repeated text). " * 260)
        + "\nYour capabilities are strictly limited to your registered tools: "
        + ", ".join(t["function"]["name"] for t in TOOL_DEFINITIONS)
        + ".\nCONTEXT:\n- Profile & Memory: Milk reminder at 08:00 AM\n"
    )
    msgs = [
        {"role": "system", "content": big_sys},
        {"role": "user", "content": "Mere kl reminder btana"},
    ]
    built = GroqProvider._build_groq_payload(msgs, TOOL_DEFINITIONS, "auto", "openai/gpt-oss-120b", 0.2, None)
    assert len(built["tools"]) <= 12
    assert "Milk reminder at 08:00 AM" in built["messages"][0]["content"]
    raw_chars = len(json.dumps(built))
    assert raw_chars / 3.1 < 6800

    seen_tool_counts = []

    async def mock_post(url, headers=None, json=None, **kwargs):
        seen_tool_counts.append(len((json or {}).get("tools") or []))
        resp = MagicMock()
        if len(seen_tool_counts) == 1:
            resp.status_code = 413
            resp.text = "Request too large"
            resp.headers = {}
            return resp
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Milk reminder at 8 AM"}}],
            "usage": {"prompt_tokens": 3400, "completion_tokens": 12, "total_tokens": 3412},
        }
        return resp

    gp = GroqProvider(api_key="groq_test_key", name="Groq")
    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        out = await gp.chat(messages=msgs, tools=TOOL_DEFINITIONS, model="openai/gpt-oss-120b")

    assert seen_tool_counts == [12, 8]
    assert out.text == "Milk reminder at 8 AM"


@pytest.mark.asyncio
async def test_transient_gemini_rpm_429_does_not_blackout_provider_for_10_min():
    """Per-minute Gemini RPM 429 must cool only that model briefly (<90s), never lock out the whole credential for 600s."""
    import time
    from llm.provider import _PROVIDER_COOLDOWNS, _MODEL_COOLDOWNS, GeminiProvider, _credential_cooldown_key

    async def mock_post(url, headers=None, json=None, **kwargs):
        model = (json or {}).get("model", "")
        resp = MagicMock()
        if model == "gemini-3.5-flash":
            resp.status_code = 429
            resp.text = (
                '{"error":{"code":429,"message":"You exceeded your current quota, please check your plan and billing details. '
                'PerMinute limit exceeded. Please retry in 25s."}}'
            )
            resp.headers = {"retry-after": "25"}
            return resp
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{"message": {"role": "assistant", "content": "Flash-lite healthy"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 5, "total_tokens": 105},
        }
        return resp

    with patch.dict("os.environ", {
        "GEMINI_API_KEY": "gem_rpm_key",
        "GEMINI_API_KEY_BACKUP": "",
        "GROQ_API_KEY": "",
        "GEMINI_MODELS": "gemini-3.5-flash",
        "USE_FAKE_LLM": "0",
        "LLM_PROVIDER_CHAIN": "",
    }, clear=False), patch("httpx.AsyncClient.post", side_effect=mock_post):
        res = await llm_provider.chat(messages=[{"role": "user", "content": "hi"}], max_retries_per_provider=0)
        assert res.model == "gemini-3.5-flash-lite"
        assert res.text == "Flash-lite healthy"
        cred_key = _credential_cooldown_key(GeminiProvider(api_key="gem_rpm_key"))
        assert cred_key not in _PROVIDER_COOLDOWNS
        ttl = _MODEL_COOLDOWNS.get((cred_key, "gemini-3.5-flash"), 0) - time.time()
        assert 10.0 <= ttl <= 70.0


@pytest.mark.asyncio
async def test_task_based_fast_tier_routes_verifier_to_lite_models():
    """audit_claims_with_llm and tier='fast' calls must prioritize fast/lite models ahead of primary 3.5-flash."""
    seen_models = []

    async def mock_post(url, headers=None, json=None, **kwargs):
        seen_models.append((json or {}).get("model", ""))
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": '{"claims_action_done": false, "which_action": null, "claims_external_fact": false, "sourced_by_tool": true, "reason": "ok"}'
                }
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 10, "total_tokens": 30},
        }
        return resp

    with patch.dict("os.environ", {
        "GEMINI_API_KEY": "gem_tier_key",
        "GEMINI_API_KEY_BACKUP": "",
        "GROQ_API_KEY": "",
        "GEMINI_MODELS": "gemini-3.5-flash,gemini-3.5-flash-lite,gemini-3-flash-preview",
        "USE_FAKE_LLM": "0",
        "LLM_PROVIDER_CHAIN": "",
    }, clear=False), patch("httpx.AsyncClient.post", side_effect=mock_post):
        from agent.verifier import audit_claims_with_llm
        out = await audit_claims_with_llm("Good morning!", "schedule clean", "Hi")
        assert out["sourced_by_tool"] is True
        assert seen_models[0] == "gemini-3.5-flash-lite"


@pytest.mark.asyncio
async def test_batch_embed_and_immutable_static_prompt_prefix():
    """embed_texts_batch uses a single :batchEmbedContents call and static prompt prefix precedes dynamic time/context."""
    from memory.store import embed_texts_batch, _EMBED_QUERY_CACHE, _EMBED_MODEL_COOLDOWNS
    from agent.prompt import build_runtime_system_prompt

    _EMBED_QUERY_CACHE.clear()
    _EMBED_MODEL_COOLDOWNS.clear()
    batch_calls = []

    def fake_post(url, json=None, timeout=None):
        batch_calls.append((url, len((json or {}).get("requests") or [])))
        resp = MagicMock()
        resp.status_code = 200
        reqs = (json or {}).get("requests") or []
        resp.json.return_value = {"embeddings": [{"values": [0.2] * 384} for _ in reqs]}
        return resp

    fake_sess = MagicMock()
    fake_sess.post.side_effect = fake_post
    with patch.dict("os.environ", {"GEMINI_API_KEY": "k_batch", "GEMINI_API_KEY_BACKUP": ""}, clear=False), \
         patch("memory.store._get_embed_session", return_value=fake_sess):
        vecs = embed_texts_batch(["alpha group", "beta group", "gamma group"], is_query=True)

    assert len(vecs) == 3 and all(v is not None and len(v) == 384 for v in vecs)
    assert len(batch_calls) == 1 and batch_calls[0][1] == 3
    assert "gemini-embedding-001:batchEmbedContents" in batch_calls[0][0]

    # Immutable static prefix must precede dynamic session time, preference lock, and CONTEXT
    chat_id = 99887799
    db.ensure_user(chat_id)
    db.set_user_preference(chat_id, reply_language="english")
    prompt = await build_runtime_system_prompt(chat_id, "hello", mem_ctx={"formatted_block": "<memory>m</memory>"})
    idx_header = prompt.find("OPERATING PRINCIPLES:")
    idx_security = prompt.find("5. Security Boundary & Untrusted Content")
    idx_runtime = prompt.find("RUNTIME SESSION & CONTEXT:")
    idx_pref = prompt.find("- LOCKED REPLY LANGUAGE: ENGLISH")
    idx_ctx = prompt.find("\nCONTEXT:\n")
    assert 0 <= idx_header < idx_security < idx_runtime < idx_pref < idx_ctx
