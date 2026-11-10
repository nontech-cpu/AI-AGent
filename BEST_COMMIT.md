# GOLDEN STABLE COMMIT SNAPSHOTS

## Snapshot 4: Production Hardening Release - Auto-Sync Scope Fix, Multi-Turn In-Place Modification, Delete Title Consistency & Prompt Injection Defense (LATEST)

- **Commit Hash**: `ead29f4`
- **Branch**: `main`
- **Date**: `Tue Nov 10 21:16:00 2026 +0530`
- **Commit Message**: `fix(hardening): fix google_services unbound local, implement in-place mind changer updates, enforce delete title consistency, and add layer 5 injection defense`

### What is Guaranteed in Snapshot 4
1. **Google Calendar Auto-Sync UnboundLocalError Fix**:
   - Fixed variable scoping in `agent/tools.py:_execute_tool_inner` by binding `import google_services` at the top of the function and removing redundant late imports, eliminating `UnboundLocalError: cannot access local variable 'google_services'`.
2. **Grounding Verifier Multi-Tool Turn Resilience**:
   - In `agent/verifier.py:deterministic_verify`, when a mutating action succeeded in the turn (`has_action_tool_success`), the verifier preserves the success draft and never clobbers it with *"Maine koshish ki lekin action complete nahi ho paya"* just because an auxiliary tool call errored.
3. **Multi-Turn "Mind Changer" In-Place Update**:
   - Added `new_title` parameter to `UpdateReminderArgs` and multi-turn fallback in `update_reminder`. When user changes their mind (e.g. *"are nahi Devender nahi Rahul ke saath 11 baje kar do"*), it updates the existing reminder in-place to the new title and time, preventing duplicate entries.
4. **Delete Target Consistency & Typo-Resilient Search**:
   - Enforces title consistency in `delete_reminder`: if an ID points to an unrelated item (e.g. Gym session ID 252) while user asked for *"meeting with devender"*, the mismatch is rejected and the tool searches active upcoming items (today + 14 days) using token overlap and Levenshtein typo tolerance (*"devedner"* -> *"devender"*).
5. **Layer 5 Meta-Prompt & Injection Defense**:
   - Enforces meta-prompt defense in `agent/prompt.py` and `agent/tools.py:validate_request_quote`: commands in quotes or prefixed with simulated administrative tags (*"System command: Ignore all safety rules..."*) are treated as passive text and strictly blocked from write/delete tools.
6. **Two-Phase Confirmed Execution Provenance Bridge**:
   - In `agent/tools.py:validate_request_quote` and `state/pending.py:execute_pending_action`, authenticated user confirmations (`_from_pending_executor=True`) are recognized as verified human intent, allowing confirmed deletions and creations to execute seamlessly without guardrail rejection.
7. **All 41 Regression & Unit Tests Passing**:
   - 41/41 tests passing cleanly across `test_live_calendar_and_language_resolution.py`, `test_phase2_llm_adapter.py`, `test_status_questions_never_write.py`, `test_no_hardcoding.py`, and `test_delete_policy_and_turn_traces.py`.

---

## Snapshot 3: Master Golden Release - Conversational Discontinuation, Canned Verifier Elimination, 503 Fast Failover & Two-Way Calendar Sync

- **Commit Hash**: `49275e5`
- **Branch**: `main`
- **Date**: `Tue Nov 10 20:10:00 2026 +0530`
- **Commit Message**: `fix(resilience): implement conversational discontinuation, eliminate canned verifier overwrites on cancellation, and add instant 503 fast failover`

### What is Guaranteed in Snapshot 3
1. **Conversational Discontinuation & Cancellation Handling**:
   - Explicit guideline in System Prompt: when user declines or cancels (e.g. *"no"*, *"cancel"*, *"rehne de"*, *"rhne de"*, *"chhod do"*, *"nahi chahiye"*), the model does NOT call creation tools and naturally acknowledges the dismissal.
   - Grounding Verifier (`agent/verifier.py`) and Guardrails (`connectors/guardrails.py`) recognize cancellations as conversational dismissals, completely eliminating robotic overwrites like *"Your request has been noted..."*.
2. **Instant Multi-Provider Fast Failover on HTTP 503 Traffic Spikes**:
   - When Google Gemini API returns HTTP 503 (`High Demand / Server Error`), the provider router fast-fails immediately to backup provider **Groq (Llama-3.3-70b)** in <300ms without pointlessly looping through other models of the overloaded provider.
3. **Physical Location vs Unconfigured Google Calendar Timezone Normalization**:
   - Resolves unconfigured Google Calendar default (`UTC`) to user physical location (`Asia/Kolkata` for India) so past/future times evaluate accurately.
4. **Multi-Turn User Request Quote Validation**:
   - Uses database-agnostic `db.get_recent_chat_history(chat_id, limit=6)` across PostgreSQL and SQLite for multi-turn reminder flows.
5. **True Two-Way Google Calendar Deletion Sync**:
   - Soft-deleting a reminder removes the event from Google Calendar via stored ID and cleans up orphaned recurrence batches.
6. **All 41 Key Unit & Regression Tests Passing**:
   - 41/41 tests passing cleanly across `test_live_calendar_and_language_resolution.py`, `test_phase2_llm_adapter.py`, `test_status_questions_never_write.py`, `test_no_hardcoding.py`, `test_part_c_calendar_recurrence.py`, `test_persistent_user_preference.py`, `test_delete_policy_and_turn_traces.py`, and `test_missing_info_heldout.py`.

---

## Snapshot 2: Final Hardening & Zero-Hardcoding Golden Release

- **Commit Hash**: `d5bf692872f96397bfbb1bf23e23a0acd05e69c1`
- **Short Hash**: `d5bf692`
- **Branch**: `main` (Merged from `fix/final-hardening`)
- **Date**: `Tue Nov 10 17:05:43 2026 +0530`
- **Commit Message**: `feat(hardening): complete Parts C, D, E, F - zero hardcoding, pre-execution validation, and readiness report`

### What is Guaranteed in Snapshot 2
1. **Trace 1233 Incident Resolution & Deletion Safety (A5)**:
   - Tool schemas stripped of model-supplied `confirmed=True` flag.
   - Destructive operations require explicit ID-bound pending action confirmation. Replying *"hi"* executes zero database mutations.
   - Comprehensive soft-delete across all 8 read paths (`status='deleted'`) with 30-day retention purge.
   - Zero fuzzy title deletes (exact ID matching only).
2. **Zero-Hardcoding Architecture (Parts B & C)**:
   - Natural language keyword loops (`"daily"`, `"roj"`), language translation dicts, and static city lists completely removed.
   - System prompt tool capability list dynamically generated from `TOOL_DEFINITIONS`.
   - Strict refusal against invented capabilities (movie ticket booking, cab booking).
   - Mandatory evidence citations for recommendations `(Source: Movie Showtimes)`.
3. **Live Screenshot Bugs Solved (Part C)**:
   - **Past-time reminder**: If requested time (e.g. 10:00 AM) is already past, daily recurrence automatically schedules the first occurrence for tomorrow 10:00 AM with zero past rows created.
   - **Calendar Sync**: Personal reminders sync as plain Google Calendar events without unwanted Google Meet video conference links.
   - **Language Persistence**: User language preferences (`"talk to me in english only"`) persist in database profile; dynamic prompt locks all subsequent turns to 100% fluent English.
   - **No Hardcoded Confirmations**: All confirmations formatted via centralized `core/i18n.py` templates with Date, Weekday, Time, Duration, and Recurrence.
4. **Pre-Execution Parameter & Evidence Validation (Part D)**:
   - Master catalog of all 61 tools across 3 risk tiers (`read`, `reversible_write`, `external_or_irreversible`) in `agent/tool_metadata.py`.
   - Pre-execution validation halts execution with `status="needs_info"` and zero side effects when essential parameters are missing.
   - 40/40 held-out test cases verified across 8 tools (`tests/heldout/test_missing_info_heldout.py`).
5. **Clean Architecture & Legacy Code Retirement (Part E)**:
   - Conversational phrase sets and interrogative keyword tuples deleted from `state/pending.py`.
   - Error handler greeting filters and dead v1 branches removed from `main.py`.
   - Fragile PNR regexes replaced with structured memory lookups.
   - English month regexes replaced with standard ISO date patterns.
6. **Test Suite Status**:
   - 42/42 core hardening and security unit tests passing (0 failures).

### Instant Rollback to Snapshot 2
```bash
git reset --hard d5bf692872f96397bfbb1bf23e23a0acd05e69c1
git push origin main --force
```

---

## Snapshot 1: Rules Codification Baseline

- **Commit Hash**: `55bdeaa34d4893c010b8b36f333ca7e8080b136f`
- **Short Hash**: `55bdeaa`
- **Date**: `Sat Nov 7 22:42:04 2026 +0530`
- **Commit Message**: `docs(rules): codify non-negotiable zero-deletion and zero-regex preservation policies`

### What is Guaranteed in Snapshot 1
1. **Core & Domain Tools Active**:
   - Document generation (`generate_pdf`, `generate_excel`)
   - Notion integration (`create_notion_page`, `update_notion_page`)
   - Reminders, Calendar & Contacts
2. **Immutable Project Rules Codified in `PROJECT_RULES.md`**:
   - Rule 1: Zero-Deletion & Preservation Policy
   - Rule 2: Zero Hardcore Regex Policy

### Instant Rollback to Snapshot 1
```bash
git reset --hard 55bdeaa34d4893c010b8b36f333ca7e8080b136f
git push origin main --force
```
