# PeculiarAI / Memorae — Forensic Project Brief & Technical Audit

**Audit Date**: 2026-11-05  
**Audit Target**: `c:\Users\neera\OneDrive\ai agent\ai-agent`  
**Database Dialect**: PostgreSQL (Neon Serverless Pooler)  
**Production Runtime**: Render (`peculiar-ai-backend`, Plan: Free)  
**Primary Entry Points**: `main.py` (Telegram Webhook & Core Lifecycle), `api_v1.py` (Web Companion / Mobile API)

---

## 1. ARCHITECTURE MAP

### 1.1 Complete Source Codebase Directory Tree & Runtime Reachability
*Excluding virtual environments (`venv/`), Git objects (`.git/`), caches (`__pycache__/`, `.pytest_cache/`), and temporary backups.*

Total Python source files identified: **136**.  
Runtime-reachable files imported directly or transitively by `main.py` and `api_v1.py`: **57**.  
Dead / unimported candidate files (excluding `tests/`, `evals/`, `scripts/`): **23**.

```
ai-agent/
├── main.py                             # [RUNTIME] Primary FastAPI web service, Telegram webhook handler, lifespan background workers, and global error recovery (5650 lines)
├── api_v1.py                           # [RUNTIME] FastAPI REST router for mobile/web companion app: sessions, vault, chat, boards, lists, rules (2580 lines)
├── db.py                               # [RUNTIME] SQLAlchemy database connection engine, connection pooler, user identity mapping, core schemas (2410 lines)
├── google_services.py                  # [RUNTIME] Google Workspace OAuth2 integration: Calendar events, Gmail search/send, contacts (890 lines)
├── integrations.py                     # [RUNTIME] Third-party developer integrations: GitHub REST API, Notion API token validators (710 lines)
├── notion_auth.py                      # [RUNTIME] Notion OAuth2 token exchange and state signature verification (190 lines)
├── utilities.py                        # [RUNTIME] Safe AST arithmetic calculator, live web scraping, currency conversion, weather (640 lines)
├── voice.py                            # [RUNTIME] Groq Whisper voice transcription and Telegram audio response pipeline (160 lines)
├── multimodal.py                       # [RUNTIME] Groq Vision / Telegram image and OCR document ingestion (130 lines)
├── crypto.py                           # [RUNTIME] AES/Fernet encryption/decryption utilities for sensitive tokens at rest (120 lines)
├── timing.py                           # [RUNTIME] Structured reminder timing plans and multi-stage alert offsets (140 lines)
├── morning_briefing.py                 # [RUNTIME] Autonomous morning briefing generator and 8:00 AM delivery loop (380 lines)
├── inbox.py                            # [RUNTIME] Gmail inbox monitoring and unread email alert poller (210 lines)
├── nlu.py                              # [RUNTIME] Legacy Hinglish/Hindi token classification and intent heuristics (240 lines)
├── email_intelligence.py               # [RUNTIME] Email parser, action item extractor, unread digest generator (520 lines)
├── agent/
│   ├── loop.py                         # [RUNTIME] Agent Core v2 ReAct execution loop, tool dispatch, multi-model failover (1150 lines)
│   ├── prompt.py                       # [RUNTIME] Runtime system prompt builder, dynamic temporal anchoring, schedule context (530 lines)
│   ├── tools.py                        # [RUNTIME] 55 tool definitions (Pydantic schemas), tool execution registry, semantic router (3700 lines)
│   ├── verifier.py                     # [RUNTIME] Deterministic code guardrails, action verification, anti-hallucination interceptor (380 lines)
│   └── deep_research.py                # [RUNTIME] Autonomous multi-hop web research and report synthesis engine (180 lines)
├── memory/
│   ├── store.py                        # [RUNTIME] Layered cognitive memory persistence: entities, commitments, measurements, episodic items (1820 lines)
│   ├── retrieval.py                    # [RUNTIME] Hybrid search (pgvector + tsvector + pg_trgm with RRF k=60), XML context assembler (380 lines)
│   ├── extractor.py                    # [RUNTIME] Background asynchronous conversation fact and entity extractor (430 lines)
│   └── backfill.py                     # [MAINTENANCE] One-time migration script for legacy memory items (110 lines)
├── schedule/
│   └── intervals.py                    # [RUNTIME] Pure mathematical interval collision: max(s1, s2) < min(e1, e2) and buffer checks (160 lines)
├── services/
│   ├── clash_detector.py               # [RUNTIME] Schedule conflict and tight transit buffer evaluator (190 lines)
│   ├── embeddings.py                   # [RUNTIME] 384-dimensional dense embeddings via Gemini Cloud API / FastEmbed (140 lines)
│   ├── health_radar.py                 # [RUNTIME] Cognitive decay score and radar calculator (180 lines)
│   ├── relationship_service.py         # [RUNTIME] People CRM and relationship knowledge graph (210 lines)
│   ├── subscription_service.py         # [RUNTIME] Recurring subscription and free trial detector (220 lines)
│   ├── followup_service.py             # [RUNTIME] Unanswered email follow-up watchdog (190 lines)
│   ├── shared_bubble_service.py        # [RUNTIME] Collaborative memory bubbles and invitation codes (240 lines)
│   └── storage.py                      # [RUNTIME] Document vault and local file storage helpers (160 lines)
├── state/
│   ├── active_context.py               # [RUNTIME] Multi-turn conversational working context and topic tracker (240 lines)
│   └── timeline.py                     # [RUNTIME] 4D reality timeline and transit span tracking (180 lines)
├── connectors/
│   ├── base.py                         # [RUNTIME] Base connector interface and authentication state contract (90 lines)
│   ├── registry.py                     # [RUNTIME] Connector catalog and health check registry (110 lines)
│   ├── trace.py                        # [RUNTIME] Turn-by-turn observability logging and suspect turn detection (180 lines)
│   ├── guardrails.py                   # [RUNTIME] Prompt injection defense and output sanitization filters (220 lines)
│   ├── github.py                       # [RUNTIME] GitHub connector implementation (260 lines)
│   ├── gmail.py                        # [RUNTIME] Gmail connector implementation (280 lines)
│   ├── calendar.py                     # [RUNTIME] Google Calendar connector implementation (290 lines)
│   ├── drive.py                        # [RUNTIME] Google Drive connector implementation (210 lines)
│   ├── notion.py                       # [RUNTIME] Notion connector implementation (240 lines)
│   └── mcp.py                          # [RUNTIME] Model Context Protocol dynamic connector (170 lines)
├── scripts/
│   ├── cleanup_memory_pollution.py     # [MAINTENANCE] Script to purge corrupted or hallucinated memories (90 lines)
│   ├── migrate_chat_logs_channel.py    # [MAINTENANCE] DB migration for chat log channel isolation (40 lines)
│   ├── promote_suspects.py             # [MAINTENANCE] Script to promote suspect traces into golden evals (110 lines)
│   ├── reembed_memories.py             # [MAINTENANCE] Re-computes 384-dim embeddings for all memories (60 lines)
│   └── verify_manager_readiness.py     # [MAINTENANCE] Pre-deployment verification script (160 lines)
├── evals/
│   ├── golden_benchmark.py             # [EVAL] 65-test golden evaluation benchmark runner (280 lines)
│   ├── golden_dataset.json             # [EVAL] Ground truth dataset for retrieval, math, and grounding (65 test cases)
│   └── scenarios.py                    # [EVAL] End-to-end multi-turn evaluation scenarios (190 lines)
├── tests/                              # 48 test files covering unit, integration, and security contracts (351 tests)
└── Dead / Unimported Legacy Files:
    ├── db_test.py                      # [DEAD CODE] Obsolete SQLite test script causing pytest collection errors
    ├── test_llm.py                     # [DEAD CODE] Obsolete standalone LLM test script causing pytest collection errors
    ├── llm.py                          # [DEAD CODE] Legacy v1 LLM routing and chat completion adapter (superseded by agent/loop.py)
    ├── list_models.py                  # [DEAD CODE] Standalone utility to list Groq models
    ├── set_webhook.py                  # [DEAD CODE] Standalone CLI script to set Telegram webhook URL
    ├── proxy.py                        # [DEAD CODE] Unused WebSocket / HTTP reverse proxy prototype
    ├── web_app.py                      # [DEAD CODE] Standalone Streamlit UI prototype (not referenced by FastAPI backend)
    ├── verify_frontier.py              # [DEAD CODE] Standalone verification runner
    ├── verify_isolation_and_greetings.py # [DEAD CODE] Standalone verification runner
    ├── lang.py                         # [DEAD CODE] Superseded by nlu.py and prompt rules
    └── timing_utils.py                 # [DEAD CODE] Superseded by schedule/intervals.py and timing.py
```

---

### 1.2 Request Flow: Telegram Text Message (Step-by-Step with File:Line)

```
[Telegram Webhook Inbound]
   │
   ▼
1. main.py:5565 `telegram_webhook(request, x_telegram_bot_api_secret_token)`
   ├── main.py:5569: Check header `secrets.compare_digest(x_telegram_bot_api_secret_token, SECRET)` -> 403 Forbidden on mismatch
   ├── main.py:5577: Check `uid in SEEN_UPDATES` -> return `{"ok": True}` if duplicate
   ├── main.py:5582: Check `callback_query in update` -> send retired buttons message
   ├── main.py:5599: Upsert `users` first_name and username in PostgreSQL
   └── main.py:5605: Route message type:
         ├── audio/voice -> voice.py:handle_voice
         ├── photo/document -> multimodal.py:handle_photo
         └── text -> main.py:5546 `_safe_handle_user_text(chat_id, incoming_text)`
               │
               ▼
2. main.py:2861 `handle_user_text(chat_id, text)`
   ├── main.py:2865: `record_chat_turn(chat_id, "user", clean)`
   ├── main.py:2869: `get_state(chat_id)`
   │
   ├── [PRE-LLM SHORTCUTS & INTERCEPTIONS]:
   │   ├── main.py:2881: If `state == "consent"` and not consented -> `yes_no(clean)` onboarding gate
   │   ├── main.py:2908: If `/start` or `start` -> reset state, pop pending actions, reply "Welcome back!"
   │   ├── main.py:2912: If `/cancel`, "rehne do", "chhoro" -> reset state, pop pending actions, reply "Cancelled"
   │   ├── main.py:2917: If `/help`, `/menu` -> send `HELP_TEXT`
   │   ├── main.py:2922: If `/memories`, `memories` -> query `get_active_memories(uid)` and list
   │   ├── main.py:2935: If `/forget <target>` -> execute `forget_memory(uid, target)`
   │   ├── main.py:2951: If `/radar`, `/health` -> compute and send cognitive health score
   │   ├── main.py:2958: If `/share_bubble`, `/join_bubble` -> manage collaborative memory spaces
   │   ├── main.py:3000: If `/relationships`, `/people` -> query and list relationship knowledge graph
   │   ├── main.py:3017: If `/subscriptions` -> list detected subscriptions
   │   ├── main.py:3034: If `/followups` -> list unanswered outbound emails
   │   ├── main.py:3060: If `state == "confirm_master_wipe"` -> OTP delivery and data deletion
   │   ├── main.py:3217: If `is_master_wipe_request(clean)` -> set state `confirm_master_wipe`
   │   ├── main.py:3225: If `/clear`, `/reset`, "chat history delete" -> `DELETE FROM chat_logs`
   │   ├── main.py:3237: If `/connectors`, `/selftest`, `/debug` -> send connector observability status
   │   └── main.py:3277: If `state in ("meeting_offer", "meeting_time")` -> handle confirmation
   │
   └── [CONVERSATIONAL AGENT EXECUTION]:
         │
         ▼
3. main.py:2448 `handle_active(chat_id, text)`
   ├── main.py:2451: Acquire `async with _get_chat_lock(chat_id):`
   └── main.py:2454: `res = await run_agent_turn(chat_id, text, channel="telegram")`
         │
         ▼
4. agent/loop.py:208 `run_agent_turn(chat_id, text, channel)`
   ├── agent/loop.py:232: Fast-path for direct Notion (`ntn_`) or GitHub (`ghp_`) API tokens
   ├── agent/loop.py:358: Resolve canonical user ID via `db.canonical_chat_id(chat_id)`
   ├── agent/loop.py:365: Build cognitive memory context via `assemble_memory_context(chat_id, text)`
   ├── agent/loop.py:372: Build system prompt via `build_runtime_system_prompt(chat_id, text, mem_ctx=...)`
   ├── agent/loop.py:385: Dynamic tool routing via `route_tools(text)`
   │
   ├── agent/loop.py:476: Candidate Model Selection Loop:
   │     ├── Tier 1: Gemini `gemini-3-flash-preview`, `gemini-3.1-flash-lite-preview`
   │     └── Tier 2: Groq `openai/gpt-oss-120b`, `qwen/qwen3.8-27b`
   │     └── POST request via `httpx.AsyncClient(timeout=30.0)`
   │
   ├── agent/loop.py:535: LLM Response Analysis:
   │     ├── If text content returned: stored as `candidate_reply`
   │     └── If `tool_calls` returned:
   │           ├── Loop tool calls -> execute `execute_tool(t_name, t_args, chat_id=...)` in agent/tools.py:1868
   │           ├── Append tool results into conversation messages
   │           └── Re-prompt model for final conversational synthesis
   │
   ├── agent/loop.py:1012: Output Grounding & Verification:
   │     └── `deterministic_verify(draft_reply, tool_results, user_query)` in agent/verifier.py:294
   │
   ├── agent/loop.py:1085: Observability Logging:
   │     ├── `db.record_chat_turn(chat_id, "assistant", final_reply)`
   │     └── `log_turn_trace(...)` into `turn_traces` table
   │
   └── Return `{"reply": final_reply, "tool_calls_made": [...]}`
         │
         ▼
5. main.py:2455 `await say(chat_id, res["reply"])` -> Telegram Bot API `/sendMessage`
   │
   └── [EXCEPTION FALLBACK]:
       If unhandled exception thrown in agent turn:
       main.py:2460: Log traceback
       main.py:2468: Send fallback: `"Haanji, network mein thoda fluctuation ho gaya tha. Ek baar dobara bataenge?"`
```

---

### 1.3 Request Flow: Web Companion / Mobile (`api_v1.py`)

```
[Inbound HTTP POST /api/v1/chat/message]
   │
   ▼
1. api_v1.py:515 `@router.post("/chat/message", response_model=ChatResponse)`
   ├── api_v1.py:517: Authenticate session via `chat_id = Depends(get_current_chat_id)`
   │     └── Resolves token from `Authorization: Bearer <token>` or `X-Session-Token` header
   │
   ├── api_v1.py:526: Unit Test Mock Check:
   │     └── If `hasattr(understand, "mock")` -> return `_execute_user_action()`
   │
   ├── api_v1.py:528: Primary Execution Path:
   │     └── `agent_res = await run_agent_turn(chat_id, body.message, channel="app")`
   │     └── Returns:
   │           `ChatResponse(reply=agent_res["reply"], intent="chat", action_taken=bool(...))`
   │
   └── api_v1.py:537: Fallback on Exception:
         ├── Catch `Exception as exc`, log fallback
         └── Fallback to `_execute_user_action(chat_id, body.message, body.history)` in api_v1.py:461:
               ├── Query Groq `https://api.groq.com/openai/v1/chat/completions` directly
               ├── Model: `openai/gpt-oss-20b`, Temperature: 0.4, Timeout: 15.0s
               └── Return `ChatResponse(reply=reply_text, intent=intent, action_taken=False)`
```

---

## 2. LLM CALLS

### 2.1 Call Sites Inventory

| Component / Function | File:Line | Provider / Endpoint | Models Configured | Temp | Timeout | Fallback Order |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Agent Core Turn Loop** (`run_agent_turn`) | `agent/loop.py:482, 492` | Google Gemini via OpenAI-compat `/v1beta/openai/chat/completions` & Groq Cloud | Tier 1: `gemini-3-flash-preview`, `gemini-3.1-flash-lite-preview`<br>Tier 2: `openai/gpt-oss-120b`, `qwen/qwen3.8-27b` | 0.2 | 30.0s | 1. Gemini Flash -> 2. Gemini Flash-Lite -> 3. Groq GPT-OSS-120B -> 4. Groq Qwen-27B |
| **Recovery Synthesis** | `agent/loop.py:956` | Groq Cloud | `openai/gpt-oss-120b` | 0.2 | 15.0s | Direct single-call synthesis |
| **Memory Extractor** (`extract_and_persist_turn`) | `memory/extractor.py:169, 196` | Google Gemini & Groq Cloud | Tier 1: `gemini-3-flash-preview`, `gemini-3.1-flash-lite-preview`<br>Tier 2: `openai/gpt-oss-120b`, `llama-3.1-8b-instant` | 0.1 | 12.0s (Gemini)<br>15.0s (Groq) | Gemini Flash -> Gemini Lite -> Groq 120B -> Groq 8B |
| **Deep Research Planner** (`generate_search_queries`) | `agent/deep_research.py:50` | Groq Cloud | `openai/gpt-oss-120b` | 0.2 | 15.0s | Single candidate |
| **Deep Research Synthesizer** (`synthesize_deep_research`) | `agent/deep_research.py:97` | Groq Cloud | `openai/gpt-oss-120b` | 0.2 | 25.0s | Single candidate |
| **Action Guard Verifier** (`verify_and_ground_reply`) | `agent/verifier.py:322` | Groq Cloud | `openai/gpt-oss-120b` | 0.0 | 8.0s | Deterministic code rules run first; LLM verifier runs only on borderline claims |
| **Web Companion Fallback** (`_execute_user_action`) | `api_v1.py:497` | Groq Cloud | `openai/gpt-oss-20b` | 0.4 | 15.0s | Hardcoded Groq call |
| **Web Companion Summary** (`generate_summary`) | `api_v1.py:1137` | Groq Cloud | `llama-3.1-8b-instant` | 0.3 | 12.0s | Single candidate |
| **Web Companion Suggestions** (`suggest_actions`) | `api_v1.py:1411` | Groq Cloud | `llama-3.1-8b-instant` | 0.3 | 10.0s | Single candidate |
| **Voice Transcriber** (`transcribe_voice_note`) | `voice.py:49` | Groq Cloud STT | `whisper-large-v3` | 0.0 | 20.0s | Audio transcription |
| **Multimodal Vision** (`describe_photo`) | `multimodal.py:9` | Groq Cloud Vision | `llama-3.2-11b-vision-preview` | 0.1 | 15.0s | Image comprehension |
| **Dense Embeddings** (`_embed_with_gemini`) | `memory/store.py:188` | Google Gemini REST `/v1beta/models/{model}:embedContent` | `gemini-embedding-2-preview`, `gemini-embedding-001` (384-dim) | N/A | 5.0s | Embedding 2 -> Embedding 001 -> Local FastEmbed |

---

### 2.2 Provider Abstraction Analysis: Is There ONE Adapter Function?
- **Finding**: **NO**. There is no unified adapter function.
- **Evidence**:
  - `agent/loop.py:507` constructs custom `httpx.AsyncClient` payloads with dual-tier URL routing.
  - `memory/extractor.py:169, 196` duplicates manual JSON formatting and token authorization headers.
  - `api_v1.py:497` contains a duplicate inline `httpx.AsyncClient.post` call.
  - `agent/verifier.py:322` duplicates inline `httpx` logic.
  - `agent/deep_research.py:50, 97` implements two separate inline `httpx` POST blocks.
- **Effort to Swap in OpenAI or Anthropic Claude API**:
  - High friction. Refactoring requires touching **7 separate files** (`agent/loop.py`, `memory/extractor.py`, `agent/verifier.py`, `agent/deep_research.py`, `api_v1.py`, `main.py`, `voice.py`) across approximately **420 lines of code** because parameters, headers, response schemas, and streaming logic are tightly coupled to Groq and Google Gemini REST shapes.

---

### 2.3 Current Date & Time Prompt Injection Text

The exact rendered temporal lines injected into the system prompt each turn (`agent/prompt.py:255, 332, 452`):

```text
Current Time: 01:48 PM, 05 November 2026 (Thursday) [Asia/Kolkata] | Today's Date: 2026-11-05 | User: Neerav | Style: English
...
- TODAY'S DATE ANCHOR (CRITICAL): Today's exact calendar date is 2026-11-05 (Thursday). The current year is 2026. When user says 'aaj', 'today', 'aajke', or asks about reminders/schedule, always use today's exact date (2026-11-05). NEVER use dates with past training cutoff years (e.g. 2024 or 2025)!
...
- Schedule: Today (Thursday, 05 Nov): Reminders: Fast blood sugar measurement for friend (10:00 AM - 10:30 AM, 30m), Meeting (04:00 PM - 04:30 PM, 30m) |
```

---

### 2.4 Token Size Measurements (Real Turn Benchmark)

*Measured on live conversation for `chat_id=[REDACTED_CHAT_SECONDARY]` using Hugging Face `tokenizers` and BPE character ratios:*

| Component | Character Count | Estimated Tokens | Percentage of Context Window |
| :--- | :--- | :--- | :--- |
| **System Prompt (Full Assembly)** | 14,073 chars | **~3,703 tokens** | ~2.9% of 128k context |
| **All Tool Definitions (55 Tools)** | 31,312 chars | **~8,946 tokens** | ~7.0% of 128k context |
| **Routed Tools per Turn** (for reminders query) | 10,958 chars (18 tools) | **~3,130 tokens** | ~2.4% of 128k context |
| **Recent Conversation History** (Last 6 turns) | ~3,200 chars | **~850 tokens** | ~0.7% of 128k context |
| **Total Inbound Turn Payload** | **~28,231 chars** | **~7,683 tokens** | **~6.0%** of 128k context |

---

## 3. DATABASE (Read-Only Live Audit)

### 3.1 Backend Selection Logic & Missing Variable Behavior
- **Decided in Code**: `db.py:27-38`:
  ```python
  raw_db_url = os.getenv("DATABASE_URL") or "sqlite:///:memory:"
  if raw_db_url.startswith("postgres://"):
    raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)
```
- **Live Backend on Render**: Neon Serverless PostgreSQL (`ep-steep-dawn-b4f6w0un-pooler.c-6.us-east-2.aws.neon.tech/neondb`).
- **Behavior if `DATABASE_URL` is Missing**: Silently falls back to in-memory SQLite: `"sqlite:///:memory:"` (`db.py:27`). All persisted tables, users, and memories are destroyed as soon as the Python process exits.

---

### 3.2 Live Database Schema, Row Counts, & Extension Audit

PostgreSQL Extensions Active:
1. `plpgsql` (Version 1.0)
2. `vector` (Version 0.8.6 - **pgvector is enabled and operational**)
3. `pg_trgm` (Version 1.6 - Trigram text search enabled)

Total Live Tables in `public` schema: **35**.

| Table Name | Row Count | Primary Columns & Types | Key Indexes |
| :--- | :--- | :--- | :--- |
| `users` | 27 | `id` (serial), `telegram_chat_id` (bigint), `persona` (text), `language` (varchar 10), `consented_at` (timestamp) | `users_telegram_chat_id_key` (UNIQUE) |
| `memories` | 162 | `id` (serial), `user_id` (int), `memory_type` (varchar), `content` (text), `tsv` (tsvector), `embedding` (vector 384), `status` (varchar), `created_at` (timestamp) | `idx_memories_tsv` (GIN), `idx_memories_embedding` (hnsw/ivfflat), `idx_memories_user_id` |
| `memory_items` | 114 | `id` (serial), `user_id` (int), `category` (varchar), `title` (text), `amount` (numeric), `due_date` (date), `due_time` (time), `duration_minutes` (int) | `idx_memory_items_user_date` |
| `reminders` | 148 | `id` (serial), `item_id` (int), `remind_at` (timestamp), `label` (varchar), `sent` (boolean) | `idx_reminders_remind_at` |
| `chat_logs` | 442 | `id` (serial), `user_id` (int), `channel` (varchar), `role` (varchar), `message` (text), `created_at` (timestamp) | `idx_chat_logs_user_created` |
| `turn_traces` | 188 | `id` (serial), `trace_id` (varchar), `chat_id` (bigint), `message` (text), `tools_called` (text), `tool_results` (text), `is_suspect` (boolean), `latency_ms` (numeric) | `turn_traces_pkey` |
| `entities` | 24 | `id` (serial), `user_id` (int), `name` (varchar), `entity_type` (varchar), `attributes` (jsonb), `created_at` (timestamp) | `idx_entities_user_name` |
| `entity_aliases` | 18 | `id` (serial), `entity_id` (int), `alias` (varchar) | `idx_entity_aliases` |
| `commitments` | 12 | `id` (serial), `user_id` (int), `entity_id` (int), `commitment` (text), `status` (varchar), `due_date` (timestamp) | `idx_commitments_user` |
| `measurements` | 38 | `id` (serial), `user_id` (int), `subject_name` (varchar), `metric` (varchar), `value` (numeric), `unit` (varchar), `context` (varchar), `measured_at` (timestamp) | `idx_measurements_user_metric` |
| `user_facts` | 4 | `id` (serial), `user_id` (int), `fact` (text), `category` (varchar), `status` (varchar), `created_at` (timestamp) | `idx_user_facts_user` |
| `projects` | 6 | `id` (serial), `user_id` (int), `name` (varchar), `objective` (text), `tech_stack` (text), `status` (varchar) | `idx_projects_user` |
| `user_corrections` | 8 | `id` (serial), `chat_id` (bigint), `wrong_concept` (text), `correction_rule` (text), `category` (varchar) | `idx_user_corrections_chat` |
| `daily_morning_briefings`| 19 | `id` (serial), `chat_id` (bigint), `briefing_date` (date), `trigger_kind` (varchar), `sent_at` (timestamp) | `daily_morning_briefings_chat_date_key` (UNIQUE) |
| `google_tokens` | 3 | `id` (serial), `user_id` (int), `email` (varchar), `refresh_token_enc` (text), `updated_at` (timestamp) | `google_tokens_user_id_key` (UNIQUE) |
| `user_integrations` | 2 | `id` (serial), `user_id` (int), `service` (varchar), `api_key_enc` (text), `is_active` (boolean) | `idx_user_integrations_user_service` |

---

### 3.3 Overlapping & Fragmented Tables Analysis
The database suffers from historical architectural schema fragmentation:
1. **`user_facts` vs `memories`**:
   - `user_facts` stores user profile facts (`id`, `user_id`, `fact`, `category`).
   - `memories` stores episodic facts with `memory_type='semantic_fact'`.
   - Facts written to `user_facts` via `save_user_semantic_fact` are not indexed into `memories`, creating dual-retrieval silos.
2. **`memories` vs `memory_items`**:
   - `memory_items` stores scheduled reminders, tasks, and appointments (`title`, `due_date`, `due_time`, `duration_minutes`).
   - `memories` also stores notes with dates. When reminders were created in `memories` in earlier versions, users saw duplicates.
3. **`entities` vs `memory_entities`**:
   - Both tables store person/contact entities, but `memory_entities` is an unmigrated legacy table while `entities` is actively used by `memory/store.py:301`.

---

### 3.4 Vector Embedding Column Population
- **Table**: `memories`
- **Total Rows**: **162**
- **Populated with 384-dim Vector (`embedding IS NOT NULL`)**: **144** rows (88.9%)
- **Unpopulated / Null Vector (`embedding IS NULL`)**: **18** rows (11.1%)
- **Impact**: Any turn executing vector cosine similarity search (`m.embedding <=> CAST(:q_emb AS vector)`) completely skips the 18 un-embedded rows, relying solely on text/trigram fallback.

---

## 4. MEMORY SYSTEM

### 4.1 Write Paths Matrix

| Function | File:Line | Caller | Target Table | Stored Fields | Importance / Memory Type |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `save_memory_item` | `memory/store.py:590` | `memory/extractor.py:270` | `memories` | `user_id`, `content`, `memory_type`, `tsv`, `embedding`, `status`, `importance` | `importance=1..5`, `memory_type` (`semantic`, `episodic`, `preference`, `constraint`) |
| `save_entity` | `memory/store.py:301` | `memory/extractor.py:285` | `entities`, `entity_aliases` | `user_id`, `name`, `entity_type`, `attributes` (JSONB) | `entity_type='person'` |
| `save_measurement`| `memory/store.py:705` | `memory/extractor.py:310` | `measurements` | `user_id`, `subject_name`, `metric`, `value`, `unit`, `context`, `measured_at` | Health metric reading |
| `create_commitment`| `memory/store.py:1653`| `memory/extractor.py:325` | `commitments` | `user_id`, `entity_id`, `commitment`, `status`, `due_date` | Promise / action item |
| `consolidate_and_save_fact` | `memory/store.py:1104` | `agent/tools.py:1750` | `user_facts` | `user_id`, `fact`, `category`, `status` | Category: `personal`, `work`, `health` |
| `record_action_event` | `memory/store.py:1232` | `google_services.py:220` | `action_events` | `user_id`, `entity_id`, `action_type`, `summary`, `metadata` | Outbound email / calendar sync event |

---

### 4.2 Read Paths Matrix

| Retrieval Function | File:Line | Tables Queried | Timeout / Cache TTL | Fallback on Failure |
| :--- | :--- | :--- | :--- | :--- |
| `assemble_memory_context` | `memory/retrieval.py:52` | Orchestrator (calls sub-functions below) | 15.0-second in-memory TTL (`_MEM_CTX_CACHE`) | Returns empty `<memory>` block `""` |
| `find_entity` | `memory/store.py:469` | `entities`, `entity_aliases` | DB query | Returns `None` |
| `get_commitments_for_user` | `memory/store.py:986` | `commitments` | DB query | Returns `[]` |
| `search_memories_hybrid` | `memory/store.py:822` | `memories` (vector, tsvector, pg_trgm via RRF) | DB query | Falls back to pure lexical tsvector + trigram |
| `get_measurements` | `memory/store.py:760` | `measurements` | DB query | Returns `[]` |
| `get_core_profile` | `memory/store.py:1592` | `user_facts` | In-memory cache | Returns empty string |
| `get_user_semantic_facts` | `memory/store.py:1062` | `user_facts` | DB query | Returns `[]` |

---

### 4.3 Embeddings & `DISABLE_LOCAL_EMBEDDINGS=1` Verification
- **Model**: Google Gemini `gemini-embedding-2-preview` / `gemini-embedding-001` (384-dimensional). Local fallback is FastEmbed `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`.
- **Proof of Behavior on Render**:
  - `memory/store.py:164` checks: `if os.getenv("GEMINI_API_KEY") or os.getenv("RENDER") or os.getenv("DISABLE_LOCAL_EMBEDDINGS", "1") ... return None`.
  - FastEmbed is prevented from loading to avoid crashing Render's 512 MB memory limit.
  - In `memory/store.py:212`, `embed_text()` calls `_embed_with_gemini()`.
  - When `GEMINI_API_KEY` is present, Cloud Gemini generates the 384-dim dense vector.
  - When `GEMINI_API_KEY` is absent AND `DISABLE_LOCAL_EMBEDDINGS=1` is set, `embed_text()` returns `None`.
  - When `embed_text()` returns `None`, `search_memories_hybrid` (`memory/store.py:875`) branches to:
    ```sql
    WITH fts_ranked AS (... ts_rank_cd(m.tsv, plainto_tsquery('english', :query)) ...),
         trgm_ranked AS (... similarity(m.content, :query) > 0.08 ...)
```
    Vector search is omitted, but PostgreSQL Full-Text Search and Trigram similarity continue running.

---

### 4.4 End-to-End Memory Trace Experiment

1. **Step (a) - User states fact**: *"my home address is 42 Baker Street Jodhpur"*
   - Persisted into `memories` via `memory/extractor.py:270`:
     - `memory_type`: `'semantic_fact'`
     - `content`: `'User home address is 42 Baker Street, Jodhpur'`
     - `importance`: `4`
     - `embedding`: 384-dim vector generated via Gemini Cloud API.
2. **Step (b) - Next turn query**: *"what is my address"*
   - Assembled into `<memory>` block via `memory/retrieval.py:310`:
     ```xml
     <memory>
     [Relevant Past Notes & Memories]:
     • [SEMANTIC] User home address is 42 Baker Street, Jodhpur
     [User Life Preferences & Facts]:
     • Home address: 42 Baker Street, Jodhpur
     </memory>
     ```
3. **Step (c) - Simulated 1 Day Later (Recency Decay Math)**:
   - Reciprocal Rank Fusion score calculation (`memory/store.py:868`):
     $$\text{Score} = \frac{1}{60 + \text{rank}_v} + \frac{1}{60 + \text{rank}_f} + \frac{1}{60 + \text{rank}_t} + \frac{0.015}{1.0 + \frac{\Delta t_{\text{seconds}}}{86400.0}}$$
   - **Day 0** ($\Delta t = 0$): Recency boost = $\frac{0.015}{1.0} = 0.0150$.
   - **Day 1** ($\Delta t = 86,400\text{s}$): Recency boost = $\frac{0.015}{2.0} = 0.0075$ (50% recency decay).
   - **Day 7** ($\Delta t = 604,800\text{s}$): Recency boost = $\frac{0.015}{8.0} = 0.001875$ (87.5% decay).
   - The memory remains easily retrievable because vector cosine and trigram scores dominate the base score ($\sim 0.030 - 0.045$).

---

## 5. REMINDERS & CLASH LOGIC

### 5.1 Full Execution Path of `create_reminder`

```
agent/tools.py:2063 `elif tool_name in ("create_reminder", "add_reminder"):`
   │
   ├── agent/tools.py:2066: Resolve user timezone `u_tz, _ = await _resolve_user_timezone(chat_id)`
   │     └── `today_d = datetime.now(u_tz).date()`
   │
   ├── agent/tools.py:2070: Date Resolution:
   │     └── If `not args.due_date`: `d_val = today_d`
   │     └── Else: `d_val = date.fromisoformat(args.due_date)`  <--- CRITICAL BUG TRIGGER
   │
   ├── agent/tools.py:2077: Time Resolution:
   │     └── Parse `args.due_time` ('HH:MM') -> `t_val = dtime(h, m)`
   │     └── If missing: fallback to `_extract_time_from_text(user_text)`
   │
   ├── agent/tools.py:2088: Duration Resolution: `dur_mins = _safe_int(args.duration_minutes, default=30)`
   │
   ├── agent/tools.py:2118: Pure Math Interval Collision Check:
   │     ├── Fetch today's items: `existing_items = db.items_for_date(chat_id, d_val)`
   │     ├── Candidate interval: `cand_start = t_val.hour * 60 + t_val.minute`, `cand_end = cand_start + dur_mins`
   │     └── For each item in `existing_items`:
   │           ├── `ex_start = r_h * 60 + r_m`, `ex_end = ex_start + r_dur`
   │           └── Collision condition: `if max(cand_start, ex_start) < min(cand_end, ex_end):`
   │                 └── Append to `conflicts`
   │
   ├── agent/tools.py:2145: Google Calendar Conflict Check:
   │     └── Check events via `get_unified_schedule(chat_id, d_val)`
   │
   ├── agent/tools.py:2202: Database Write:
   │     └── `db.save_memory(chat_id, 'appointments', clean_title, due_date=d_val, due_time=t_val, duration_minutes=dur_mins)`
   │
   ├── agent/tools.py:2210: Reminder Triggers:
   │     └── `plan = build_plan(d_val, t_val)` -> `add_reminders(item_id, plan['reminders'])`
   │
   ├── agent/tools.py:2230: Google Calendar Sync:
   │     └── `google_services.create_calendar_event(chat_id, summary=..., start_time=..., end_time=...)`
   │
   └── agent/tools.py:2248: Invalidate Schedule Cache:
         └── `invalidate_prompt_schedule_cache(chat_id)`
```

---

### 5.2 Forensic Bug Reproduction: Meeting 16:00-17:00 vs Movie at 16:30

#### Reproduction Scenario
1. User had an existing meeting today (`2026-11-05`) from 16:00 to 17:00 (`duration_minutes=60`).
2. User stated: *"4:30PM pr movie jana h uska timer laga dena"*.
3. Model's internal weights (with training cutoff around Jan 2025) hallucinated `due_date="2025-01-24"`.
4. `agent/tools.py:2073` blindly executed:
   ```python
   d_val = date.fromisoformat(args.due_date)  # d_val = date(2025, 1, 24)
```
5. `db.items_for_date(chat_id, date(2025, 1, 24))` was queried and returned an empty list `[]`.
6. Collision check evaluated:
   $$\max(\text{cand\_start}, \text{ex\_start}) < \min(\text{cand\_end}, \text{ex\_end})$$
   Because there were 0 items on 2025-01-24, `conflicts = []` and `has_conflict = False`.
7. The code wrote the reminder into the live database on **2025-01-24** without warning.

#### Forensic Live DB Row Evidence
```python
# Query: db.items_for_date([REDACTED_CHAT_SECONDARY], datetime.date(2025, 1, 24))
(
    'appointments',
    'Movie jana hai',
    None,
    datetime.time(16, 30),
    202,
    30,
    datetime.date(2025, 1, 24),
)
```

#### Collision Check Simulation Output
- **Case A (Evaluated against 2025-01-24 items)**: `conflicts = []` $\rightarrow$ `has_conflict = False` *(Silent collision failure)*.
- **Case B (Evaluated against 2026-11-05 items)**: `conflicts = ["'Meeting' (16:00 - 17:00)"]` $\rightarrow$ `has_conflict = True` *(Accurate detection)*.

---

### 5.3 Complete Inventory of Unvalidated Model Dates
Places where model-supplied dates and times are trusted without validation against current year or realistic boundaries:
1. `agent/tools.py:2073` (`create_reminder`): `date.fromisoformat(args.due_date)` accepted without year check.
2. `agent/tools.py:1947` (`update_reminder`): `new_date = date.fromisoformat(args.due_date)` accepted without year check.
3. `agent/tools.py:1853` (`smart_packing_list`): `date.fromisoformat(args.trip_date)` accepted without validation.
4. `agent/tools.py:2455` (`get_day_schedule`): `target_d = date.fromisoformat(args.target_date)`.
5. `agent/tools.py:2445` (`find_free_slots`): `target_date_str` passed directly to interval planner.
6. `agent/tools.py:3509` (`analyze_schedule_density`): `args.target_date` trusted directly.
7. `google_services.py:118` (`create_calendar_event`): `start_time` and `end_time` strings accepted without temporal bounds check.

---

## 6. REGEX & KEYWORD MEANING LOGIC AUDIT

Automated analysis across all 136 Python source files separated logic into **Meaning-determining logic** (intent, confirmation, language switching, stopword gating) vs **Structural parsing** (dates, URLs, UUIDs, numbers).

Total Meaning-determining regex / keyword expressions: **383**.  
Total Structural parsing regex expressions: **23**.

### Meaning Regex / Keyword Counts by File

| File | Count | Dominant Semantic Categories Decided by Heuristics |
| :--- | :--- | :--- |
| `main.py` | **106** | Pre-LLM command routing, confirmation words (`yes_no`, `classify_yes_no`), greeting regexes, master wipe triggers, cancel shortcuts |
| `llm.py` | **37** | Legacy intent routing keywords, category selectors, confirmation keywords |
| `utilities.py` | **25** | Calculator syntax keywords, web query cleaners, currency token matches |
| `agent/tools.py` | **22** | Reschedule phrase matches (`"shift"`, `"badal"`, `"update"`), transit vehicle checks (`"movie"`, `"flight"`), AM/PM token hints |
| `agent/verifier.py` | **17** | Unconfirmed action claim keywords, regional language script checks, Meet link patterns |
| `state/active_context.py` | **17** | Topic switching tokens, pronoun tracking heuristics |
| `connectors/guardrails.py`| **15** | Prompt injection keyword blocks, jailbreak heuristics |
| `email_intelligence.py` | **14** | Email urgency keywords, action item classification words |
| `integrations.py` | **13** | Token prefix checks (`"ntn_"`, `"ghp_"`), service name aliases |
| `agent/loop.py` | **12** | Language alias mapping (`ALIAS_TO_CANONICAL`), intent word lists (`INTENT_WORDS`) |
| `morning_briefing.py` | **11** | Greeting filter keywords, agenda item formatters |
| `lang.py` | **10** | Language classification stopwords |
| `state/timeline.py` | **9** | Transit and buffer keyword heuristics |
| `memory/retrieval.py` | **8** | Candidate entity token filters, 120+ word Hinglish stopword set |
| `services/elevenlabs_service.py` | **8** | Audio emotion keyword triggers |
| `connectors/trace.py` | **6** | Suspect turn keyword flags |
| `google_services.py` | **5** | Calendar summary clean keywords |
| `inbox.py` | **5** | Email subject classification keywords |
| `nlu.py` | **5** | Hindi token sets (`[\u0900-\u097F]+`) |
| `api_v1.py` | **4** | Companion chat intent fallback words |

---

## 7. TESTS & BENCHMARK REPORT

### 7.1 Existing Test Suite Execution
- **Command**: `pytest tests -v --tb=short`
- **Total Tests Collected**: **351**
- **Test Results**:
  - **Passed**: **324** (92.3%)
  - **Failed**: **25** (7.1%)
  - **Skipped**: **2** (0.6%)
  - **Execution Time**: 191.00s (0:03:11)

#### Categorization of 25 Test Failures

| Category | Count | Failing Test Identifiers | Root Cause / Explanation |
| :--- | :--- | :--- | :--- |
| **Outdated Tests** | **18** | `tests/test_master_repairs.py` (6 tests)<br>`tests/test_agent_v2.py` (4 tests)<br>`tests/connectors/test_connector_matrix.py[mcp]` (3 tests)<br>`tests/test_smart_pillars.py`<br>`tests/test_elite_pillars.py`<br>`tests/test_multimodal_documents.py`<br>`tests/test_production_resilience.py` (2 tests) | Asserting legacy v1 state-machine responses, outdated text banners (e.g. deprecated Google recommendation banner), or obsolete Groq 429 mock string signatures. |
| **Needs Live Credentials** | **3** | `tests/test_connected_email_intelligence.py::test_search_user_emails_tool`<br>`tests/test_google_services.py::test_create_meeting_success`<br>`tests/test_integrations.py::test_github_link_status_query` | Tests execute real API integration calls requiring live Google OAuth tokens or active GitHub tokens in the database. |
| **Real Code Bugs** | **4** | `tests/test_full_pillars.py::test_reminder_conflict_detection`<br>`tests/test_oauth_security.py::test_clean_public_base_url_sanitization`<br>`tests/test_phase4_golden_eval.py::test_phase4_golden_benchmark_thresholds`<br>`tests/test_agent_v2.py::test_multi_intent_execution_and_tool_catalog` | 1. Collision detection assertion mismatch in full pillars test.<br>2. Localhost URL trailing slash sanitization bug.<br>3. Measurements routing score 75% vs 80% threshold.<br>4. Multi-intent compound tool catalog handling error. |

---

### 7.2 Golden Benchmark Results (`evals/golden_benchmark.py`)

*Evaluated across 65 ground-truth test cases (`evals/golden_dataset.json`):*

| Evaluation Metric | Benchmark Result | Target SLA | Status |
| :--- | :--- | :--- | :--- |
| **Total Test Cases** | **65** | 65 | Completed |
| **Memory Retrieval Recall@3** | **95.8%** | $\ge$ 90.0% | **PASS** |
| **Memory Retrieval Recall@5** | **95.8%** | $\ge$ 95.0% | **PASS** |
| **Math AST Calculator Accuracy** | **100.0%** | 100.0% | **PASS** |
| **Hallucination Interception Rate** | **0.0%** (0 unverified slipped) | 0.0% | **PASS** |
| **Measurements SQL Routing** | **75.0%** | $\ge$ 80.0% | **FAIL** (Threshold gap) |
| **Short Entity Disambiguation** | **83.3%** | $\ge$ 80.0% | **PASS** |
| **Latency P50** | **1.20 ms** | $\le$ 1500 ms | **PASS** |
| **Latency P90** | **2.06 ms** | $\le$ 2500 ms | **PASS** |
| **Latency P95** | **2.10 ms** | $\le$ 3000 ms | **PASS** |
| **Mean Latency** | **0.89 ms** | $\le$ 1200 ms | **PASS** |
| **Average Tokens per Turn** | **~228 tokens** | $\le$ 800 tokens | **PASS** |

---

## 8. DEPLOYMENT (Render Cloud Audit)

### 8.1 `render.yaml` Configuration & Infrastructure Profile
```yaml
services:
  - type: web
    name: peculiar-ai-backend
    runtime: python
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
    plan: free
    autoDeploy: true
    envVars:
      - key: PYTHON_VERSION
        value: 3.11.9
      - key: DISABLE_LOCAL_EMBEDDINGS
        value: "1"
```

- **Plan Type**: `free` (Render Free Web Service).
- **RAM Threshold**: **512 MB**. Loading local ONNX models (FastEmbed / PyTorch) causes an immediate OOM kill (exit code 137).
- **Persistent Disk**: **None**. Render free web services have an ephemeral root filesystem. Any SQLite file (`peculiar.db`, `peculiarai_web_verify.sqlite3`) or local attachment written to disk is destroyed upon restart or redeployment.
- **Sleep / Cold Start Behavior**:
  - The free container automatically spins down after **15 minutes of inactivity** (zero inbound HTTP requests).
  - Cold start wake-up latency takes **50 to 90 seconds** on the next request.
- **Health Check Endpoint**:
  - Defined in `main.py:4249`: `@app.get("/health")` and `main.py:4254`: `@app.get("/")`.
  - Not explicitly referenced under `healthCheckPath` in `render.yaml`.

---

### 8.2 In-Memory Volatile State Lost on Container Restart
Because state is stored in Python module-level memory rather than Redis or PostgreSQL, restarting or redeploying the container wipes:
1. `PENDING_GOOGLE_ACTIONS` (`main.py:270`): In-flight Google action confirmation requests, OTP requests, and two-step action dialogs.
2. `_CHAT_LOCKS` (`main.py:321`): In-memory conversation asyncio locks.
3. `_UNIFIED_PROMPT_CACHE` (`agent/prompt.py:115`): Pre-computed user schedule and prompt blocks.
4. `_MEM_CTX_CACHE` (`memory/retrieval.py:49`): 15-second retrieval cache.
5. `_EMBED_QUERY_CACHE` (`memory/store.py:177`): Query embedding vector cache.
6. `_CHAT_HISTORY_CACHE` (`db.py:1257`): Recent message history cache.
7. `active_context` (`state/active_context.py`): In-memory conversational topic and entity resolution buffers.
8. Active `asyncio` background tasks created via `create_task`.

---

### 8.3 Background Scheduled Jobs Analysis
Six background loops are launched in `main.py:4105-4112` via FastAPI's `lifespan` handler:
1. `reminder_loop()` (`main.py:4035`): Wakes every 30 seconds to query `reminders` table and dispatch due notifications.
2. `commitment_nudge_loop()` (`main.py:4106`): Scans pending promises to trigger nudge alerts.
3. `mailbox_loop()` (`main.py:4107`): Checks unread email notifications.
4. `connected_user_gmail_monitor_loop()` (`main.py:4108`): Monitors connected user mailboxes.
5. `morning_briefing_loop()` (`morning_briefing.py:35`): Polls for 8:00 AM IST to send daily digests.
6. `heartbeat_loop()` (`main.py:4110`): Emits self-health signals.

#### Behavioral Flaws on Free Tier & Multi-Instance:
- **Sleep Freezing**: When Render spins down the free container after 15 minutes of inactivity, Python's event loop freezes. **Morning briefings scheduled for 8:00 AM and timed reminders do NOT fire** unless an external cron job or HTTP request wakes the container.
- **Duplicate Firing on Scale / Rolling Deployments**: There are **no distributed locks** (such as Redis or PostgreSQL `pg_advisory_lock`). If scaled to 2 instances or during rolling redeployments, both containers run all 6 loops simultaneously, delivering duplicate reminders and double morning briefings.

---

### 8.4 Environment Variables Audit

| Variable Name | Required / Optional | Status in Live Setup | Purpose |
| :--- | :--- | :--- | :--- |
| `DATABASE_URL` | **Required** | Configured | Neon PostgreSQL connection string (pooler) |
| `TELEGRAM_BOT_TOKEN` | **Required** | Configured | Telegram Bot API token |
| `WEBHOOK_SECRET` | **Required** | Configured | Secret token checked in `X-Telegram-Bot-Api-Secret-Token` |
| `GEMINI_API_KEY` | **Required** | Configured | Tier 1 LLM calls & Cloud 384-dim embeddings |
| `GROQ_API_KEY` | **Required** | Configured | Tier 2 LLM failover, Whisper STT, Vision |
| `SECRET_KEY` | **Required** | Configured | AES encryption key for user tokens at rest |
| `DISABLE_LOCAL_EMBEDDINGS` | **Required** | Configured (`1`) | Protects Render 512 MB RAM limit |
| `GOOGLE_CLIENT_ID` | Optional | Configured | Google Workspace OAuth2 client ID |
| `GOOGLE_CLIENT_SECRET` | Optional | Configured | Google Workspace OAuth2 client secret |
| `NOTION_CLIENT_ID` | Optional | Configured | Notion integration OAuth client ID |
| `NOTION_CLIENT_SECRET` | Optional | Configured | Notion integration OAuth client secret |
| `GITHUB_CLIENT_ID` | Optional | Configured | GitHub OAuth client ID |
| `GITHUB_CLIENT_SECRET` | Optional | Configured | GitHub OAuth client secret |

---

### 8.5 Security Quick Scan
1. **Secrets in Repo / Git History**: No API keys or plain tokens are committed in repository source files. Secrets are sourced exclusively from `.env` and environment variables.
2. **Webhook Secret Enforced**: **YES**. Verified at `main.py:5569`:
   ```python
   if not SECRET or not x_telegram_bot_api_secret_token or not secrets.compare_digest(x_telegram_bot_api_secret_token, SECRET):
       raise HTTPException(status_code=403, detail="Forbidden: Invalid secret token")
   ```
3. **Tokens Encrypted at Rest**: **YES**. Google refresh tokens and Notion tokens are encrypted before database insertion using AES/Fernet in `crypto.py:28` (`refresh_token_enc`, `api_key_enc`).
4. **Hardcoded User IDs Violation**: **YES (CRITICAL)**. Hardcoded Telegram IDs exist in source code at `db.py:495`:
   ```python
   CANONICAL_CHAT_ID_MAP = {
       [REDACTED_CHAT_PRIMARY]: [REDACTED_CHAT_SECONDARY],  # Hardcoded Telegram User ID mapping
   }
   ```

---

## 9. TOP 15 PROBLEMS (Ranked by Severity)

| Rank & Severity | Problem Title | File:Line Reference | One-Line Impact | Forensic Evidence |
| :---: | :--- | :--- | :--- | :--- |
| **1. CRITICAL** | **Model Cutoff Hallucination & Blind Date Trust** | `agent/tools.py:2073`<br>`agent/loop.py:484`<br>`agent/prompt.py:214` | When user gives a time without a full date, model sends training cutoff (`2025-01-24`), which code accepts blindly, creating reminders in the past and causing 100% collision misses and empty schedule listings. | DB row `('appointments', 'Movie jana hai', None, 16:30, 202, 30, 2025-01-24)` created today; collision simulation against 2025 returned `conflicts=[]`. |
| **2. CRITICAL** | **Generic Exception Trap Silencing Real Errors** | `main.py:2460-2469` | Any model timeout, 429 quota exhaustion, or agent error is caught by blanket `except Exception` and masked with `"Haanji, network mein thoda fluctuation ho gaya tha"`, hiding root causes from user. | Matches exact repeated text in user's Telegram screenshots at 12:06 PM. |
| **3. CRITICAL** | **Hardcoded User Telegram IDs in Source Code** | `db.py:495` | Multi-tenant privacy violation and security anti-pattern; user account IDs are hardcoded directly into repository version control. | `CANONICAL_CHAT_ID_MAP = {[REDACTED_CHAT_PRIMARY]: [REDACTED_CHAT_SECONDARY]}` hardcoded in `db.py:495`. |
| **4. HIGH** | **Ephemeral Render Free Tier Losing In-Memory State** | `render.yaml:8`<br>`main.py:270, 321`<br>`agent/prompt.py:115` | Container spin-down wipes pending action confirmations (`PENDING_GOOGLE_ACTIONS`), locks, and working memory buffers. | `render.yaml: plan: free`. `PENDING_GOOGLE_ACTIONS = {}` defined as bare in-memory dict. |
| **5. HIGH** | **Background Scheduled Workers Freeze When Service Sleeps** | `main.py:4105-4112`<br>`morning_briefing.py:35` | When Render free tier spins down after 15 min, Python event loop stops; 8:00 AM morning briefings and reminders never fire. | Lifespan starts in-process asyncio tasks without external trigger or cron service. |
| **6. HIGH** | **No Distributed Locking for Multi-Instance Deployments** | `main.py:4035`<br>`morning_briefing.py:35` | If scaled to 2 instances or during rolling deployments, duplicate reminder and briefing messages are delivered to Telegram. | In-process sleep `await asyncio.sleep(30)` used without PostgreSQL advisory locks. |
| **7. HIGH** | **383 Meaning Regex/Keyword Heuristics Bypassing AI Semantics** | `main.py:2877-3310`<br>`llm.py:37`<br>`agent/tools.py:22` | Brittle keyword and regex checks intercept user messages before the LLM runs, causing false intent misclassifications. | Automated audit found 383 meaning-determining regex/keyword expressions. |
| **8. HIGH** | **Fragmented & Overlapping Memory Tables** | `db.py:1457`<br>`memory/store.py:590`<br>`api_v1.py:1276` | Facts and memories are split across `user_facts`, `memories`, `memory_items`, `entities`, creating fragmented retrieval silos. | Live DB inspection revealed 35 tables with duplicate entity and fact representations. |
| **9. MEDIUM** | **Unpopulated Embedding Vectors in Memories Table** | `memory/store.py:164-215` | 18 out of 162 memory rows have `embedding IS NULL`, meaning vector cosine search completely misses them. | Live DB query: `{'memories': {'populated': 144, 'null': 18, 'total': 162}}`. |
| **10. MEDIUM** | **Lack of Unified LLM Provider Adapter** | `agent/loop.py:482`<br>`memory/extractor.py:169`<br>`api_v1.py:497` | Direct `httpx` POST calls to Groq and Gemini endpoints are duplicated across 7 files, making swapping to OpenAI/Claude costly. | 21 duplicated call sites across the codebase. |
| **11. MEDIUM** | **Silent Catch-All `except Exception: pass` Blocks** | `memory/retrieval.py:112, 160`<br>`agent/tools.py:2222` | Errors during entity resolution, hybrid search, and Google Calendar sync are silently swallowed, frustrating debugging. | 7 bare `except Exception: pass` blocks in `memory/retrieval.py`. |
| **12. MEDIUM** | **Timezone Drift Fallbacks** | `agent/prompt.py:228`<br>`agent/tools.py:2066` | When Google Calendar timezone resolution fails, code falls back to IST (`Asia/Kolkata`), breaking schedules for non-IST users. | Hardcoded `except Exception: u_tz = IST` across prompt and tools. |
| **13. MEDIUM** | **Obsolete Root Test Scripts Breaking Bare Pytest** | `db_test.py:1`<br>`test_llm.py:1` | Running bare `pytest` in root fails during collection because abandoned test scripts have broken imports. | Pytest collection interrupted with 2 collection errors on root test files. |
| **14. MEDIUM** | **Measurements Routing Benchmark Threshold Gap** | `evals/golden_benchmark.py:220` | Health measurements SQL routing achieved 75% accuracy versus the 80% benchmark target. | Golden benchmark scorecard: `Measurements SQL Routing: 75.0%`. |
| **15. LOW** | **Database URL Fallback to Ephemeral In-Memory SQLite** | `db.py:27` | If `DATABASE_URL` fails to load, app starts silently on `"sqlite:///:memory:"` without warning, dropping all data on exit. | `raw_db_url = os.getenv("DATABASE_URL") or "sqlite:///:memory:"` in `db.py:27`. |

---
*Report compiled autonomously via forensic discovery script suite. End of brief.*

