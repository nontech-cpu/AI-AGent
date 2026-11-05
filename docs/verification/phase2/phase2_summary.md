# Phase 2 Verification Summary: LLM Adapter & Resilient Error Handling

**Date:** 2026-11-05  
**Branch:** `fix/phase1-time-correctness`  
**Raw Test Evidence:** [`docs/verification/phase2/pytest_phase2.txt`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase2/pytest_phase2.txt)

---

## 1. Unified LLM Adapter (`llm/provider.py`)

A single, centralized provider adapter now governs all model completions across PeculiarAI, replacing over 400 lines of fragmented, manual HTTP client calls.

### A. Normalized Request / Response Contract
All callers invoke `await llm_provider.chat(...)`:
```python
resp = await llm_provider.chat(
    messages=messages,
    tools=tools,               # Optional OpenAI-format function definitions
    tool_choice=tool_choice,   # 'auto', 'required', 'none', or specific tool
    temperature=0.2,
    timeout=30.0,
    model=None,                # Defaults to provider chain priority
    response_format=None       # e.g. {"type": "json_object"}
)
```

The returned `LLMResponse` dataclass is normalized across all providers and supports both attribute access and dictionary key lookups:
- `resp.text` / `resp["text"]`: Assistant response string.
- `resp.tool_calls` / `resp["tool_calls"]`: List of normalized tool calls (`[{"id": ..., "type": "function", "function": {"name": ..., "arguments": ...}}]`).
- `resp.usage` / `resp["usage"]`: Aggregated token counts (`{"prompt_tokens": int, "completion_tokens": int, "total_tokens": int}`).
- `resp.provider`: Executing provider name (`"Gemini"`, `"Groq"`, `"OpenAI"`, `"Anthropic"`, `"FakeLLM"`).
- `resp.model`: Exact executing model name (e.g. `"gemini-3.1-flash-lite-preview"`).
- `resp.fallback_used`: Boolean indicating whether failover was triggered.
- `resp.fallback_reason`: Structured explanation if fallback occurred.

---

### B. Supported Providers & Extensible Architecture

1. **`GeminiProvider`**:
   - Google Generative Language OpenAI-compatible endpoint (`/v1beta/openai/chat/completions`).
   - Models: Configurable via `GEMINI_MODELS` (defaults to `gemini-3.1-flash-lite-preview`, `gemini-3-flash-preview`).
   - Native tool calling, 1M context window.

2. **`GroqProvider`**:
   - Groq Cloud OpenAI-compatible endpoint (`/openai/v1/chat/completions`).
   - Models: Configurable via `GROQ_MODELS` (defaults to `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`, `llama-3.1-8b-instant`).
   - Multi-key support: primary `GROQ_API_KEY` with automatic rollover to `GROQ_API_KEY_BACKUP`.

3. **`OpenAIProvider` (Stub & Clean Extension)**:
   - Complete implementation stub ready for `OPENAI_API_KEY`.
   - Comprehensive docstring explaining configuration (`OPENAI_API_KEY`, `OPENAI_MODELS`), standard `/v1/chat/completions` payload, and activation in `LLM_PROVIDER_CHAIN`.

4. **`AnthropicProvider` (Stub & Clean Extension)**:
   - Complete implementation stub ready for `ANTHROPIC_API_KEY`.
   - Comprehensive docstring explaining Messages API format, system prompt extraction, `x-api-key` header, and Anthropic tool schema transformation.

5. **`FakeLLMProvider` (Hermetic Testing Harness)**:
   - Allows deterministic scripting of tool calls, poisoned dates, malformed JSON, and exceptions (`get_fake_llm()`, `queue_response()`, `queue_poisoned_tool_call()`, `queue_error()`, `reset_fake_llm()`).
   - Activated automatically via `USE_FAKE_LLM=1`.

---

### C. Fallback & Resilient Retry Policy

1. **Retry with Exponential Backoff + Jitter**:
   - On HTTP 429 (Rate Limit) or HTTP 5xx / timeout, the adapter reads `retry-after` header (or defaults to 1.0s), adds uniform random jitter (`0.1s - 0.5s`), and retries the **same provider once**.
2. **Failover to Fallback Provider**:
   - If the retry fails or the provider is exhausted, the adapter seamlessly switches to the next configured candidate in `LLM_PROVIDER_CHAIN` (e.g. Gemini $\to$ Groq).
   - Telemetry marks `fallback_used = True` and appends detailed failure reasons to `fallback_reason`.
3. **Forced Tool Calling on Fallback**:
   - If a smaller fallback model returns free conversational text instead of calling a required tool, the adapter retries once with `tool_choice="required"`.
4. **Fallback XML Parser**:
   - If smaller fallback models emit tool calls wrapped in `<function=name>{...}</function>`, `parse_xml_tool_calls` normalizes them into standard OpenAI tool calls without failing the turn.

---

## 2. All Call Sites Routed Through Adapter

| File | Function / Block | Description |
| :--- | :--- | :--- |
| `agent/loop.py` | `run_agent_turn` (primary loop) | Replaced 60 lines of nested HTTP retry loops with `await llm_provider.chat(...)`. |
| `agent/loop.py` | Conversational recovery block | Replaced manual Groq POST with `await llm_provider.chat(...)`. |
| `memory/extractor.py` | `extract_and_persist_turn` | Replaced dual Gemini/Groq POST loops with single call to `await llm_provider.chat(...)`. |
| `agent/verifier.py` | `verify_and_ground_reply` | Replaced inline httpx Groq call with `await llm_provider.chat(...)`. |
| `agent/deep_research.py` | `generate_search_queries` | Replaced decomposer Groq POST with `await llm_provider.chat(...)`. |
| `agent/deep_research.py` | `synthesize_deep_research` | Replaced critic synthesizer Groq POST with `await llm_provider.chat(...)`. |
| `api_v1.py` | `_execute_user_action` | Replaced inline Groq chat completion fallback with `await llm_provider.chat(...)`. |
| `api_v1.py` | `cross_tool_query` | Replaced inline Groq summary call with `await llm_provider.chat(...)`. |
| `api_v1.py` | `daily_briefing` | Replaced inline Groq briefing generation with `await llm_provider.chat(...)`. |
| `main.py` | `web_chat` | Replaced inline Groq web companion call with `await llm_provider.chat(...)`. |

---

## 3. Error Handling Overhaul: Removal of "Network Fluctuation"

### A. Problem Eliminated
Previously, any unhandled runtime error in `run_agent_turn` was swallowed by a blanket `except Exception` in `main.py:2458`, which sent the user a deceptive canned reply:
> *"Haanji, network mein thoda fluctuation ho gaya tha. Ek baar dobara bataenge?"*

### B. Solution Implemented
1. **Typed Exception Hierarchy (`llm/provider.py`)**:
   - `ProviderTimeoutError`: HTTP timeout during LLM request.
   - `RateLimitedError`: HTTP 429 rate limit exceeded.
   - `ToolExecutionError`: Tool failure during execution.
   - `DatabaseUnavailableError`: Database query / connection dropout.
   - `ValidationError`: Schema / parameter validation failure.
2. **Trace ID Logging (`main.py:2458-2490`)**:
   - Every exception generates a unique trace ID: `trace_{uuid.uuid4().hex[:8]}`.
   - Full stack trace is logged with `trace_id` to uvicorn/application logs for instant observability in Render.
3. **Accurate User-Facing Messages with Retry Offer**:
   - `ProviderTimeoutError` $\to$ *"AI model response timed out. Please try sending your message again in a moment. [Ref: trace_1a2b3c4d]"*
   - `RateLimitedError` $\to$ *"AI service is currently experiencing high demand. Please wait a few seconds and try again. [Ref: trace_1a2b3c4d]"*
   - `ToolExecutionError` $\to$ *"An issue occurred while executing the requested action. Please verify details and retry. [Ref: trace_1a2b3c4d]"*
   - `DatabaseUnavailableError` $\to$ *"Database connection is temporarily slow or unavailable. Your data is safe. Please retry shortly. [Ref: trace_1a2b3c4d]"*
   - `ValidationError` $\to$ *"Invalid parameters provided. Please check the details and retry. [Ref: trace_1a2b3c4d]"*
   - General error $\to$ *"Something went wrong while processing your request. Please try again. [Ref: trace_1a2b3c4d]"*

---

## 4. Elimination of Silent `except: pass` Blocks

1. **`memory/retrieval.py`**:
   - Added module logger: `logger = logging.getLogger("memory.retrieval")`.
   - Replaced 9 bare `except: pass` blocks (recent actions, entity anaphora, project memory, cosine bubble search, hybrid RRF, temporal date search, measurements, core profile, active bookings) with structured `logger.warning(...)` calls.
2. **`agent/tools.py`**:
   - Replaced silent `except: pass` blocks in reminder plan generation, Google Calendar event auto-sync, and prompt schedule cache invalidation with structured `logger.warning(...)` calls.
   - Standardized `ValidationError` return contract to include `"error": "validation_error"` and `"message": str(val_err)`.

---

## 5. Test Suite Verification & Results

Dedicated test suite created in `tests/test_phase2_llm_adapter.py`:
1. `test_provider_429_backoff_and_fallback`: Verified primary 429 rate limit triggers backoff retry and fails over to secondary provider.
2. `test_provider_timeout_failover`: Verified provider timeout triggers clean failover to next candidate.
3. `test_xml_tool_call_extraction`: Verified XML tool calling parser normalizes free text into OpenAI tool call format.
4. `test_empty_response_normalization`: Verified empty responses normalize cleanly without exceptions.
5. `test_tool_call_invalid_arguments_structured_error`: Verified bad/missing arguments return structured error dictionaries.
6. `test_typed_exceptions_produce_clean_user_reply_with_trace_id`: Verified all 5 typed exceptions produce user-facing messages with `[Ref: trace_...]` and retry offers (and never send "network fluctuation").
7. `test_fake_llm_harness_poisoned_tool_call`: Verified FakeLLM harness operation for testing.

### Full Test Suite Run Output (`pytest` across all touched files)
```text
tests/test_phase1_time_and_clash.py::test_poisoned_cutoff_date_rejected PASSED [  2%]
tests/test_phase1_time_and_clash.py::test_agent_loop_self_corrects_poisoned_date PASSED [  5%]
tests/test_phase1_time_and_clash.py::test_clash_detection_meeting_vs_movie PASSED [  8%]
tests/test_phase1_time_and_clash.py::test_list_reminders_missing_date_returns_today PASSED [ 11%]
tests/test_phase1_time_and_clash.py::test_read_tools_accept_past_dates_and_relative_expressions PASSED [ 14%]
tests/test_phase1_time_and_clash.py::test_write_tools_reject_past_datetime_and_second_attempt_succeeds PASSED [ 17%]
tests/test_crashes_and_fallback.py::test_set_user_timezone_no_unbound_local PASSED [ 20%]
tests/test_crashes_and_fallback.py::test_agent_loop_ist_scoping_no_unbound_local PASSED [ 22%]
tests/test_crashes_and_fallback.py::test_unhandled_crash_triggers_typed_fallback_and_trace_id PASSED [ 25%]
tests/test_phase2_llm_adapter.py::test_provider_429_backoff_and_fallback PASSED [ 28%]
tests/test_phase2_llm_adapter.py::test_provider_timeout_failover PASSED  [ 31%]
tests/test_phase2_llm_adapter.py::test_xml_tool_call_extraction PASSED   [ 34%]
tests/test_phase2_llm_adapter.py::test_empty_response_normalization PASSED [ 37%]
tests/test_phase2_llm_adapter.py::test_tool_call_invalid_arguments_structured_error PASSED [ 40%]
tests/test_phase2_llm_adapter.py::test_typed_exceptions_produce_clean_user_reply_with_trace_id PASSED [ 42%]
tests/test_phase2_llm_adapter.py::test_fake_llm_harness_poisoned_tool_call PASSED [ 45%]
tests/test_nlu_and_parsing.py::test_sensitive_detection PASSED           [ 48%]
tests/test_nlu_and_parsing.py::test_explicit_calendar_action_detection PASSED [ 51%]
tests/test_nlu_and_parsing.py::test_word_numbers_devanagari_and_hinglish PASSED [ 54%]
tests/test_nlu_and_parsing.py::test_parse_followup_time PASSED           [ 57%]
tests/test_nlu_and_parsing.py::test_multilingual_yes_no_fast_paths PASSED [ 60%]
tests/test_nlu_and_parsing.py::test_meeting_title_extraction PASSED      [ 62%]
tests/test_agent_v2.py::test_tool_get_contact_fuzzy PASSED               [ 65%]
tests/test_agent_v2.py::test_tool_check_availability_detects_conflict PASSED [ 68%]
tests/test_agent_v2.py::test_tool_create_meeting_two_phase_confirmation PASSED [ 71%]
tests/test_agent_v2.py::test_agent_loop_executes_tool_and_answers PASSED [ 74%]
tests/test_agent_v2.py::test_tool_save_contact_and_lookup_typo PASSED    [ 77%]
tests/test_agent_v2.py::test_create_meeting_fails_gracefully_when_google_not_connected PASSED [ 80%]
tests/test_agent_v2.py::test_connect_integration_google_returns_oauth_url PASSED [ 82%]
tests/test_agent_v2.py::test_standalone_email_provides_google_oauth_url PASSED [ 85%]
tests/test_agent_v2.py::test_send_email_confirmation_dialogue_formats_email_not_meeting PASSED [ 88%]
tests/test_agent_v2.py::test_fake_success_guard_does_not_block_conversational_text PASSED [ 91%]
tests/test_agent_v2.py::test_casual_trip_schedule_never_creates_notion_page PASSED [ 94%]
tests/test_agent_v2.py::test_affirmative_yes_to_conversational_email_draft_executes_send_email PASSED [ 97%]
tests/test_agent_v2.py::test_multi_intent_execution_and_tool_catalog PASSED [100%]

====================== 35 passed, 18 warnings in 48.44s =======================
```
*(Raw output captured in `docs/verification/phase2/pytest_phase2.txt`)*

---

## 6. Phase 3 Scope & Next Steps

Phase 3 is **Memory (single source of truth)**:
- Verify live schema for facts (`memories`, `user_facts`, `memory_items`, `entities`).
- Write idempotent, reversible migration consolidating all facts into `memories` with `memory_type` and `importance`.
- Remove keyword and stopword gating; implement hybrid vector/FTS/trigram retrieval with recency, importance, and token budgeting.
- Backfill NULL embeddings and create embedding retry queue table.
- Replace hardcoded `CANONICAL_CHAT_ID_MAP` with identity-linking table.

**Phase 2 is complete. Awaiting user "go ahead" before proceeding to Phase 3.**
