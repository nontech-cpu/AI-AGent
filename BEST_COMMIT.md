# GOLDEN STABLE COMMIT SNAPSHOTS

## Snapshot 3: Master Golden Release - Live Two-Way Calendar Sync, Multi-Turn Server Quote Validation & Status Question Protection (LATEST)

- **Commit Hash**: `b943b08`
- **Branch**: `main`
- **Date**: `Tue Nov 10 19:05:00 2026 +0530`
- **Commit Message**: `fix(multi-turn-grounding): fix cross-database chat history quote validation and prevent false verifier interception on tool errors`

### What is Guaranteed in Snapshot 3
1. **Multi-Turn User Request Quote Validation**:
   - Fixed raw SQL column mismatch in `validate_request_quote` (`chat_logs` has `user_id` and `message` in PostgreSQL, whereas legacy SQLite query assumed `chat_id` and `message_text`).
   - Now uses database-agnostic `db.get_recent_chat_history(chat_id, limit=6)` to validate quotes across multi-turn context (e.g. Turn 1 *"set up a reminder for me at 2pm"* + Turn 2 *"it should be about meeting"*).
   - Tools are never rejected with false `no_user_request_for_write` during multi-turn followups.
2. **Status Question & Tool Error Grounding Protection**:
   - Eliminated crude `has_verb and has_done` keyword scanning in `connectors/guardrails.py` and `agent/verifier.py`.
   - Informational status inquiries (e.g. *"ky tumn firecraker wala reminder htaa diya caldner se bhi?"*) and error explanations (e.g. *"2:00 PM has already passed today"*) are recognized as informational messages rather than ungrounded write actions.
   - Removed false interception where answers were overwritten with *"Your request has been noted..."*.
3. **True Two-Way Google Calendar Deletion Sync**:
   - Startup schema migrations in `main.py:lifespan` automatically initialize `memory_items.google_calendar_event_id VARCHAR(128)`.
   - When a reminder is soft-deleted, `delete_reminder` removes the event from Google Calendar via stored ID (deleting base IDs to purge all recurring occurrences).
   - Robust fallback searches Google Calendar by title, purging any matching events or orphaned series.
4. **Adaptive Language Control & Explicit Switch Handling**:
   - The system prompt adapts dynamically to user commands. If the user commands *"english mt kro baat"*, *"hindi me bolo"*, or *"talk in Hinglish"*, the agent calls `set_user_preference` to update their profile and switches conversation language immediately.
5. **All Unit & Regression Test Suites Passing**:
   - 31/31 tests passing cleanly across `test_live_calendar_and_language_resolution.py`, `test_missing_info_heldout.py`, `test_no_hardcoding.py`, `test_persistent_user_preference.py`, `test_status_questions_never_write.py`, `test_part_c_calendar_recurrence.py`, and `test_delete_policy_and_turn_traces.py`.

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
