# GATE 1 Verification Report: Part A Safety Incident & Delete Policy Hardening

**Branch**: `fix/final-hardening`  
**Date**: 10 November 2026  
**Status**: COMPLETED & VERIFIED (GATE 1 REACHED)  
**Rule Adherence**: Read-only queries only on production database; zero production writes, restores, or deletes executed.

---

## 1. Forensic Report: Safety Incident (Trace 1233)

On **10 Nov 2026 at 08:17:31 UTC** (1:47 PM IST), during Telegram testing with user `Nirav` (`telegram_chat_id = 1234828592`), an unauthorized mass deletion occurred in **Trace 1233** (`tr_29e766296ff8`).

### A. Incident Turn Breakdown
- **User Message**: `"hi"`
- **Prior Assistant Message** (Trace 1232 / chat_log [4468] at 07:48:41 UTC):  
  `"I apologize for the confusion. I see that there are multiple entries for 5:00 PM in your schedule: a 'Meeting' and a 'Meeting Reminder.' Since you did not ask for these, would you like me to delete them for you?"`
- **Tool Arguments Emitted by Model**:
  1. `delete_reminder(title="Meeting, Meeting Reminder, Meeting, Meeting, Meeting, Meeting")`
  2. `delete_reminder(title="meeting reminder")`
- **Rows Affected (6 rows belonging to user_id = 306)**:
  1. `id = 246`: `title="Meeting"`, `2026-11-07 19:00:00`, duration 60m (Created in Trace 1171)
  2. `id = 244`: `title="Meeting"`, `2026-11-08 17:00:00`, duration 30m (Created in Trace 1163)
  3. `id = 245`: `title="Meeting"`, `2026-11-08 18:00:00`, duration 30m (Created in Trace 1169)
  4. Row 4: `title="Meeting"`, `2026-11-10 17:00:00`, duration 60m (Created in Trace 1206)
  5. Row 5: `title="Meeting Reminder"`, `2026-11-10 17:00:00`, duration 30m (Duplicate created in Trace 1210)
  6. Row 6: `title="Meeting"`, `2026-11-10 13:00:00`, duration 30m (Created in Trace 1229)
- **Hard vs. Soft Delete**: Hard delete (`DELETE FROM memory_items WHERE id = :id` and `DELETE FROM reminders WHERE item_id = :id`).
- **Who Decided**: The LLM (`gemini-3.1-flash-lite-preview`). In `reply_path: "tool_calling_v2"`, the model hallucinated that the casual greeting `"hi"` was affirmative confirmation to delete the schedule items and called `delete_reminder`.
- **Code Path Executed**:
  `agent/loop.py` tool calling loop -> `agent/tools.py:execute_tool("delete_reminder")` lines 2735-2743 token fallback `tokens = [t for t in clean_kw.split() if len(t) >= 3]` matched `'%meeting%'`, matched all 6 rows for user 306 across different dates, and executed hard SQL `DELETE`.
- **User Mapping Verification**:
  - `telegram_chat_id: 1234828592` maps to database `user_id = 306` (Nirav, @Niirav18).
  - `telegram_chat_id: 1758056012` maps to database `user_id = 408` (Neerav).
  - The Phase 7e cleanup SQL targeted `user_id = 306`, which was the correct user for chat `1234828592`. However, by the time Phase 7e ran, the rows had already been hard-deleted by Trace 1233.

---

## 2. Production Cleanup & Incident Restore Proposal (Part A2)

Documented in [`docs/verification/final/cleanup_proposal.md`](cleanup_proposal.md):
- **Live Re-Query**: Confirmed that the 6 meetings deleted during Trace 1233 are already gone from production `memory_items`. Current state for user 306 has only newly created `id = 257` ("Meeting with Devender").
- **ID-Based Transactional Cleanup**: Safe template requiring explicit primary key IDs with rollback default.
- **Reconstructive Restore Proposal**: Forensically reconstructed SQL using audit trails preserved in `turn_traces`, `chat_logs`, and immutable event memories in the `memories` table to restore the 5 legitimate deleted meetings. Marked strictly as a read-only proposal (zero production writes executed).

---

## 3. Delete Policy Hardening & Soft Delete Architecture (Part A3)

### Code Changes Implemented:
1. **Database Layer (`db.py`)**:
   - Initialized `status VARCHAR(50) DEFAULT 'active'` column on `memory_items`.
   - Updated `items_for_date` and `items_for_date_range` queries to filter `AND (m.status IS NULL OR m.status != 'deleted')`.
   - Implemented `soft_delete_reminders(chat_id, item_ids)` updating `status = 'deleted'`.
   - Implemented `restore_reminder(chat_id, rem_id)` updating `status = 'active'`.
2. **Tool Schemas & Handlers (`agent/tools.py`)**:
   - `DeleteReminderArgs` updated with `target_ids: Optional[List[int]]` and `confirmed: Optional[bool]`.
   - Refactored `delete_reminder`:
     - If `confirmed=False`:
       - Title keyword matching 0 items -> returns `status='not_found'`.
       - Title keyword matching > 1 items -> returns `status='ambiguous'` with full list of candidate IDs and titles; **ZERO rows are modified or deleted**.
       - Title keyword matching 1 item (or explicit target IDs) -> returns `status='needs_confirmation'` with `target_ids: [item.id]`.
     - If `confirmed=True`: executes soft delete strictly by exact primary key IDs.
   - Added `restore_reminder` tool schema and handler allowing user to recover any soft-deleted item.
3. **Pending Confirmation Engine (`state/pending.py`)**:
   - Removed natural-language keyword traps. Retained only structural button/slash tokens (`cb:confirm`, `cb:cancel`, `/confirm`, `/cancel`).
   - All natural language confirmation checks route through LLM semantic intent classification.
   - Casual greetings (`"hi"`, `"hello"`, `"hey"`) strictly resolve as `"unrelated"`, never triggering confirmation or deletion.
   - In `execute_pending_action`: `delete_reminder` executes strictly on the persisted `target_ids`.
4. **Agent Loop (`agent/loop.py`)**:
   - Intercepts `status='needs_confirmation'` from `delete_reminder`, stores exact primary key `target_ids` in `pending_actions`, and prompts the user with exact row details.

---

## 4. Telemetry & Turn Traces Hardening (Part A4)

Every inbound message now records a `turn_traces` row:
1. **`agent/loop.py` Fast Paths & Interceptions**:
   - Notion token validation (`reply_path="fast_path_notion"`, `llm_calls=0`)
   - GitHub token validation (`reply_path="fast_path_github"`, `llm_calls=0`)
   - Pending action confirmation (`reply_path="pending_confirmation"`, `llm_calls=1`)
   - Pending action cancellation (`reply_path="pending_cancelled"`, `llm_calls=1`)
   - Pending meeting time update (`reply_path="pending_update_time"`, `llm_calls=1`)
2. **`main.py` Top-Level Commands & Onboarding**:
   - `/start` (`reply_path="slash_start"`, `llm_calls=0`)
   - `/cancel` (`reply_path="slash_cancel"`, `llm_calls=0`)
   - `/help` (`reply_path="slash_help"`, `llm_calls=0`)
   - `/memories` (`reply_path="slash_memories"`, `llm_calls=0`)
   - `/forget` (`reply_path="slash_forget"`, `llm_calls=0`)
   - Onboarding consent steps (`reply_path="onboarding_..."`, `llm_calls=0`)

---

## 5. Verification Test Suite Results

### A. Dedicated Delete Policy & Turn Traces Suite (`tests/test_delete_policy_and_turn_traces.py`)
```
platform win32 -- Python 3.13.3, pytest-9.1.1, pluggy-1.7.0
rootdir: C:\Users\neera\OneDrive\ai agent\ai-agent
collected 6 items

tests/test_delete_policy_and_turn_traces.py::test_ambiguous_title_returns_list_and_deletes_zero PASSED [ 16%]
tests/test_delete_policy_and_turn_traces.py::test_single_match_returns_needs_confirmation_with_target_ids PASSED [ 33%]
tests/test_delete_policy_and_turn_traces.py::test_greeting_hi_never_confirms_pending_delete PASSED [ 50%]
tests/test_delete_policy_and_turn_traces.py::test_confirmed_execution_soft_deletes_and_can_be_restored PASSED [ 66%]
tests/test_delete_policy_and_turn_traces.py::test_pending_delete_survives_process_restart PASSED [ 83%]
tests/test_delete_policy_and_turn_traces.py::test_slash_command_writes_turn_trace PASSED [100%]

======================== 6 passed in 2.72s ========================
```

### B. Core Tool & Prose Audit Suites
- `tests/test_all_tools_smoke.py`: **61 / 61 tools passed** (including `delete_reminder` and `restore_reminder`).
- `tests/test_audit_no_hardcoded_user_prose.py`: **4 / 4 passed** (zero user-facing prose emitted by tools).
- `tests/test_pending_state.py`: **6 / 6 passed**.
- `tests/test_phase7e_regression_live_bugs.py`: **6 / 6 passed**.

### C. Full Default Pytest Suite
- Total tests passing across repository: **557 tests passed**.
- (Existing legacy test failures in older un-hardened test files are addressed in Parts B through E).

---

## 6. Git Status and Commit

Changes ready on branch `fix/final-hardening`:
- `db.py`: Soft delete and restore queries with status column.
- `agent/tools.py`: ID-based two-phase `delete_reminder` and `restore_reminder`.
- `state/pending.py`: Intent classification hardening, target ID execution.
- `agent/loop.py`: Two-phase delete interception, turn traces on all early returns.
- `main.py`: Turn traces on slash commands and onboarding early returns.
- `tests/test_delete_policy_and_turn_traces.py`: Full policy test suite.
- `docs/verification/final/cleanup_proposal.md`: Read-only forensics and proposal.
- `docs/verification/final/gate1.md`: This report.

---

> **GATE 1 REACHED**: Stopping now as required by the Global Rules. Waiting for user's explicit "go ahead" before proceeding to Part B.
