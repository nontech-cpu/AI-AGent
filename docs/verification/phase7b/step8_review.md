# Step 8 Comprehensive Review & Evidence Report

> **Branch**: `fix/pre-phase8-live-fixes`  
> **Target Production Branch**: `main` (autoDeploy: true on Render)  
> **Database Status**: Neon Serverless PostgreSQL (`us-east-2`, Ohio) — **ZERO production writes executed**  
> **Status**: Completed all 5 review items. Awaiting your explicit review and approval before proceeding to Phase 8.

---

## 1. Transmission of Steps 1–7 Evidence

### A. Full Summary Matrix ([`docs/verification/phase7b/summary.md`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/summary.md))

| Step | Objective | Status | Key Evidence & Artifact Links |
|---|---|---|---|
| **Step 1** | **Email Recall Diagnosis** | `DONE` | [`docs/verification/phase7b/step1_email_recall_diagnosis.md`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/step1_email_recall_diagnosis.md)<br>• Traces 1082-1084: `UnboundLocalError: google_services` in `search_user_emails`.<br>• `action_events` table does not exist in DB.<br>• Sent email on Oct 31 was never logged in any memory table.<br>• User 408 Google account has active `gmail.readonly` scope. |
| **Step 2** | **Event Memory Architecture & Dry-Run Backfill** | `DONE` (Awaiting Approval) | [`docs/verification/phase7b/event_memory_design.md`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/event_memory_design.md)<br>[`docs/verification/phase7b/dry_run_event_memory_backfill.json`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/dry_run_event_memory_backfill.json)<br>• Single `memories` table design (`memory_type='event'`).<br>• Dry-run cataloged 8 candidates (1 email, 1 meeting, 6 reminders).<br>• Zero production writes applied. |
| **Step 3** | **Gmail Lookup Tool & Honest Errors** | `DONE` | [`agent/tools.py:305, 920, 3381`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/tools.py)<br>[`tests/test_phase7b_gmail_and_event_memory.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_phase7b_gmail_and_event_memory.py)<br>• Added `import google_services` and query routing (`in:sent`, `to:{email}`).<br>• Typed errors: `not_connected`, `auth_scope_missing`, `rate_limited`, `provider_error`, `not_found`.<br>• 5/5 unit tests passed. |
| **Step 4** | **Live Grounding Diagnosis & Guard Fixes** | `DONE` | [`docs/verification/phase7b/step4_live_grounding_diagnosis.md`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/step4_live_grounding_diagnosis.md)<br>[`agent/verifier.py:192-212`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/verifier.py#L192-L212)<br>• Trace 1056 proved mall list came from TripAdvisor search snippet, not memory.<br>• Trace 1057 proved route was ungrounded parametric hallucination.<br>• Deterministic navigation guard intercepts ungrounded routes and returns Google Maps URL. |
| **Step 5** | **Weekday & Date Grounding** | `DONE` | [`agent/prompt.py:45, 196`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/prompt.py#L45)<br>[`tests/test_phase7b_date_and_time.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_phase7b_date_and_time.py)<br>• Injected `{WEEKDAY}` (`timeutil.now(IST).strftime("%A")`) directly into prompt.<br>• Anchored date answers strictly to timeutil, eliminating mental calendar math.<br>• 20/20 test cases passed across 10 dates (including leap days and month ends). |
| **Step 6** | **Model Order & Measured Latency Facts** | `DONE` | [`docs/verification/phase7b/step6_model_order_and_latency.md`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/step6_model_order_and_latency.md)<br>[`docs/verification/phase7b/step6_latency_and_metrics.json`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/step6_latency_and_metrics.json)<br>• Tool comparison confirms `gemini-3.1-flash-lite-preview` 100% success vs `gemini-3-flash-preview` 429 quota exhausted.<br>• Real 20 `SELECT 1` RTT to Neon: min 313ms, p50 314ms, avg 346ms.<br>• 678 turns analyzed over last 7 days: p50 turn latency 8,005 ms, p95 54,480 ms. |
| **Step 7** | **Deployment State & Rollback Runbook** | `DONE` | [`docs/verification/phase7b/deploy_state.md`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/deploy_state.md)<br>• Render confirmed running commit `3c27a2e` from `main`.<br>• Documented branch topology and zero-downtime rollback steps via Render dashboard.<br>• Ohio region change runbook with webhook / env var survival verified. |
| **Step 8** | **Pending Approvals Specification** | `DONE` | [`docs/verification/phase7b/pending_approvals.md`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/pending_approvals.md)<br>• Full evidence assembled below. |

---

### B. Step 1 Diagnosis: Email Recall Live Failure
- **User Query at 11:22 PM**: *"mene aaravmishra ko ek mail send kra rh tum bta skte ho vo"*
- **Live Database Traces (Rows 1082, 1083, 1084 in `turn_traces`)**:
  ```json
  {
    "id": 1082,
    "chat_id": 1758056012,
    "user_message": "mene aaravmishra ko ek mail send kra rh tum bta skte ho vo",
    "tools_called": ["search_user_emails"],
    "tool_results": [{"status": "error", "error": "cannot access local variable 'google_services' where it is not associated with a value"}],
    "error_type": "UnboundLocalError",
    "provider": "Gemini",
    "model": "gemini-3.1-flash-lite-preview",
    "llm_calls": 0,
    "memory_status": "active"
  }
  ```
- **`action_events` Table**:
  - Queried PostgreSQL: `SELECT COUNT(*) FROM action_events;`
  - Raw result: `psycopg.errors.UndefinedTable: relation "action_events" does not exist`.
  - Logging to `action_events` never started in production because the migration was never executed.
- **Google OAuth Scopes Stored for User 408**:
  - Queried `users` table:
    ```json
    {
      "email": "neeravmishra1805@gmail.com",
      "scopes": [
        "https://www.googleapis.com/auth/userinfo.email",
        "https://www.googleapis.com/auth/userinfo.profile",
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/gmail.readonly"
      ]
    }
    ```
  - `gmail.readonly` is active. **No re-consent is required.**
- **Exact Code Path Swallowing the Error**:
  - `agent/tools.py:3391`: Executed `if chat_id in google_services._CHAT_GMAIL_INSUFFICIENT_SCOPES:` without `import google_services` at the top of `execute_tool`.
  - Threw `UnboundLocalError`, caught by generic `except Exception as exc:` in `agent/loop.py:650`, which fed the error back into the LLM context.
  - The LLM synthesized: *"email search mein technical dikkat aa rahi hai"*.
- **Truth Assessment (i, ii, iii)**:
  - **(i) TRUE**: Phase 3 removed the legacy keyword-gated sent-mail prompt injection block from `memory/retrieval.py`.
  - **(ii) PARTIAL (CODE BUG, NOT SCOPE FAILURE)**: The OAuth token *does* have read scope, but the tool code crashed on an unbound variable.
  - **(iii) TRUE**: The email sent to Aarav Mishra on Oct 31 was stored in `pending_actions` (id 37), but never inserted into `memories`.

---

### C. Step 4 Grounding Report: Mall & Route Turns
- **Mall Turn (Trace 1056)**:
  - Query: *"jodhpur m kosne mall h"*
  - Tool called: `find_nearby_places(query="malls in jodhpur")`.
  - Source: Live TripAdvisor snippet titled *"10 Best Shopping Malls in Jodhpur"* which included standalone boutique shops (*Raju's Fashion Mall, Saleraj Store, Bagtesh Fashion, Risala The Boutique*). The model quoted TripAdvisor's listicle verbatim. Not a memory hallucination.
- **Route Turn (Trace 1057)**:
  - Query: *"bhy jdhpur m ratanada se sardarpura jana ka sbse best route kya h"*
  - Tool called: `search_web(query="best route from ratanada to sardarpura jodhpur")`.
  - Source: Tool returned general taxi/cab aggregator URLs.
  - Failure: The agent had no real-time GPS/navigation tool, so it hallucinated: *"Circuit House Road hote hue, 10-15 minute"*.
  - Verifier Path: The verifier passed this because `evidence_str` was coarsely non-empty.
  - Fix: Added deterministic guard in `agent/verifier.py:192-212` that intercepts route/travel-time claims when no GPS tool is present and replaces the draft with a direct Google Maps directions URL (`https://www.google.com/maps/dir/...`).

---

## 2. Backfill Dry-Run Fixes (DRY-RUN ONLY — ZERO PROD WRITES)

### A. Core Profile Isolation (`importance = 2–3`)
- Updated `memory/store.py:1771`:
  ```sql
  SELECT content, created_at FROM memories
  WHERE user_id = :uid AND status = 'active' AND valid_to IS NULL
    AND memory_type NOT IN ('event', 'action_event')
    AND (importance >= 4 OR memory_type IN ('preference', 'profile'))
  ORDER BY importance DESC, created_at DESC
  LIMIT 10
  ```
- All event rows are assigned `importance = 3` (never >= 4).

### B. Pipeline Insertion (Not Raw SQL)
- Insertions run via `memory.store.save_memory_item()`:
  - Generates 384-dimensional dense vector embeddings (`embed_text`).
  - Automatically falls back to `embedding_retry_queue` if embedding provider fails.
  - Automatically sets `valid_from = created_at`, `status = 'active'`, and `confidence = 1.0`.

### C. Idempotency Check
- Dedup key: `(user_id, source_type, source_id)`.
- Re-running the dry-run script on an already populated state yields **0 new rows**.

### D. All 8 Historical Candidate Events (Categorized & Labeled)

| # | Chat ID | Summary | Source ID | Label | Empirical Justification |
|---|---|---|---|---|---|
| **1** | `911` | Created calendar meeting 'Sprint Planning' (2026-10-22) | `pending_action:5` | **LIKELY TEST DATA** | Chat ID 911 is the hardcoded synthetic fixture used in automated test scripts on Oct 22. |
| **2** | `1758056012` | Sent email to `aaravmishra619@gmail.com` with subject 'weather' (2026-10-31) | `pending_action:37` | **REAL** | Live user interaction from Oct 31, 2026. This is the exact sent email the user asked about at 11:22 PM. |
| **3** | `1758056012` | Deleted reminder 'Test Dummy Reminder' (2026-11-05) | `pending_action:39` | **LIKELY TEST DATA** | Created during live debugging at 03:03 AM explicitly titled 'Test Dummy Reminder'. |
| **4** | `1758056012` | Created reminder 'Movie jana hai' for 2025-01-24 at 16:30 | `trace:1043` | **LIKELY TEST DATA / POISONED CUTOFF** | Created prior to Phase 1 temporal fix; model defaulted to training cutoff year 2025. |
| **5** | `1758056012` | Created reminder 'Movie (3 hours long)' for 2025-05-15 at 13:00 | `trace:1058` | **REAL (Live turn with cutoff year)** | Live Telegram turn from Nov 5 at 9:57 PM (*"bhy mere toh kl 1 pm movie reminder set krdena"*). |
| **6** | `1758056012` | Created reminder 'Flight' for 2025-05-16 at 14:00 | `trace:1060` | **REAL (Live turn with cutoff year)** | Live Telegram turn from Nov 5 at 9:58 PM (*"kl 2pm m ko meri flight b h"*). |
| **7** | `1758056012` | Deleted reminder 'Movie jana hai, Movie (3 hours long)' | `trace:1062` | **UNSURE / MULTI-DELETE ARTIFACT** | Title is a comma-joined string artifact created when a bulk reminder delete tool executed. |
| **8** | `1758056012` | Created reminder 'Movie' for 2026-11-06 at 13:00 | `trace:1078` | **REAL** | Live Telegram turn from Nov 5 at 10:10 PM after Phase 7 temporal fix correctly resolving to 2026. |

### E. Test Suite Verification & Raw Output
- **Test File**: [`tests/test_phase7b_event_memory.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_phase7b_event_memory.py)
- **Command**: `.\venv\Scripts\pytest tests/test_phase7b_event_memory.py -v`
- **Raw Command Output**:
  ```
  ============================= test session starts =============================
  platform win32 -- Python 3.13.3, pytest-9.1.1, pluggy-1.7.0 -- .\venv\Scripts\python.exe
  cachedir: .pytest_cache
  rootdir: C:\Users\neera\OneDrive\ai agent\ai-agent
  configfile: pytest.ini
  plugins: anyio-4.15.1, asyncio-1.4.0
  asyncio: mode=Mode.STRICT, debug=False
  collected 3 items

  tests/test_phase7b_event_memory.py::test_event_rows_excluded_from_get_core_profile PASSED [ 33%]
  tests/test_phase7b_event_memory.py::test_event_backfill_idempotency PASSED [ 66%]
  tests/test_phase7b_event_memory.py::test_event_memory_retrieval_and_llm_answering PASSED [100%]

  ======================== 3 passed, 2 warnings in 7.32s ========================
  ```

---

## 3. Deletions Audit & Safety Proof

### A. Render AGENT_CORE Proof
1. **Env Var on Render**: Inspected `render.yaml`. `AGENT_CORE` is **not present** in `render.yaml`.
2. **Default in Code**:
   - `main.py:2429`: `if os.getenv("AGENT_CORE", "v2").lower() == "v2":`
   - Default is `"v2"`.
3. **Startup Log & `/health` Verification**:
   - Added startup log: `STARTUP CONFIG: AGENT_CORE=v2` in `validate_startup_environment()`.
   - Added `"agent_core": os.getenv("AGENT_CORE", "v2").lower()` to `/health` payload.
   - **Verification Command & Output**:
     ```powershell
     .\venv\Scripts\python -c "import main; from fastapi.testclient import TestClient; c = TestClient(main.app); r = c.get('/health'); print(r.status_code, r.json().get('agent_core'))"
     # Output: 200 v2
     ```

### B. Two-Commit Deletion Safety Plan
- **Critical Finding**: Directly removing `llm/legacy.py` without cleanup causes `main.py:40` to throw `ImportError: cannot import name 'extract' from 'llm'`.
- **Approved Two-Commit Sequence**:
  - **Commit 1**: Excise dead v1 branch in `main.py` (lines 2470–3221 and 3246–3530), removing unused legacy imports (`extract`, `understand`, `guess_persona`), and add minimal modern shims in `llm/__init__.py` for `_chat_json` and `parse_followup_time`.
  - **Commit 2**: Delete `llm/legacy.py` and remove `from . import legacy as _legacy` in `llm/__init__.py`.

### C. Retained Files Audit (`nlu.py` & `lang.py`)
- **Current Active Usages**:
  1. `nlu.py:yes_no()`: Used in initial consent flow (`main.py:2862, 3463`).
  2. `nlu.py:match_persona()` & `PERSONA_LABELS`: Used in onboarding persona setup (`main.py:1174, 1183`).
  3. `lang.py:get_language()` / `set_language()`: Used across `main.py` to localize replies.
- **Proposed Button-Based Replacements (Telegram `InlineKeyboardMarkup`)**:
  - **Consent**:
    ```python
    InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Agree & Continue", callback_data="consent_yes"),
        InlineKeyboardButton("❌ Disagree", callback_data="consent_no")
    ]])
    ```
  - **Persona Selection**:
    ```python
    InlineKeyboardMarkup([
        [InlineKeyboardButton("💼 Job / Professional", callback_data="persona:job")],
        [InlineKeyboardButton("🏢 Business / Founder", callback_data="persona:business")],
        [InlineKeyboardButton("🎓 Student", callback_data="persona:student")],
        [InlineKeyboardButton("🏠 Housework / Personal", callback_data="persona:housework")]
    ])
    ```
  - **Language Selection**:
    ```python
    InlineKeyboardMarkup([[
        InlineKeyboardButton("English", callback_data="lang:en"),
        InlineKeyboardButton("Hinglish", callback_data="lang:hinglish"),
        InlineKeyboardButton("हिंदी", callback_data="lang:hi")
    ]])
    ```

---

## 4. Security Audit: Token Paste Fast-Paths

- **Storage Inspection**:
  - `user_integrations` table: Uses AES-256-GCM encryption (`crypto.encrypt_aes256_gcm`).
  - `turn_traces`: Fast-path returns early before `log_turn_trace` is called (not stored).
  - `chat_messages` / `chat_logs`: **VULNERABILITY IDENTIFIED**. `db.log_chat_message(chat_id, "user", user_text)` wrote the raw pasted token into chat logs!
- **Implemented Fix**:
  - In [`agent/loop.py:159, 180`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L159), the token is redacted before logging:
    ```python
    redacted_token = trimmed_text[:7] + "..." + trimmed_text[-4:]
    db.log_chat_message(chat_id, "user", redacted_token, channel=channel)
    ```
- **Proposed OAuth-First Flow**:
  - Default Telegram message presents direct OAuth authorization button:
    `"Connect Notion via OAuth: https://peculiar-ai-backend.onrender.com/auth/notion"`
  - Direct token pasting retained strictly as a secondary fallback for self-hosted / private internal integrations.

---

## 5. `KNOWN_ISSUES.md` Keyword & Regex Tracker

Updated in [`docs/KNOWN_ISSUES.md`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/KNOWN_ISSUES.md) Section 4:
1. [`state/pending.py:211-219`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/state/pending.py#L211-L219): Conversational confirmation phrases (`"haan"`, `"kar do"`, `"bilkul"`, `"mat karo"`, `"rehne do"`).
2. [`state/pending.py:243`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/state/pending.py#L243): Question interceptor checking interrogatives and hardcoded entity `"ananya"`.
3. [`agent/tools.py:3745-3765`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/tools.py#L3745-L3765): `route_tools` keyword dictionaries (`"sona"`, `"chandi"`, `"bhav"`, `"weather"`, etc.).
4. [`main.py:2442`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L2442): Error fallback greeting filter (`"hi"`, `"hello"`, `"namaste"`).
5. [`state/active_context.py:288-343`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/state/active_context.py#L288-L343): PNR, train, flight, and seat extraction regexes.
6. [`memory/retrieval.py:120`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/memory/retrieval.py#L120): Temporal date expression regex.

---

## 6. Execution Safeguards & Next Steps

- **Branch**: `fix/pre-phase8-live-fixes` (unpushed to remote, isolated).
- **Production Database**: Zero writes executed. All queries executed read-only.
- **Phase 8 Readiness**: Pre-Phase-8 fixes and verification are complete.

> [!NOTE]
> **Antigravity has stopped here for your review.**
> Phase 8 (final verification and readiness report) will begin only when you reply **"go ahead"**.

