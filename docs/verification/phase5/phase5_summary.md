# Phase 5 Verification Summary: Grounding & Anti-Hallucination

**Date:** 2026-11-05  
**Branch:** `fix/phase1-time-correctness`  
**Test Output:** [`docs/verification/phase5/pytest_phase5.txt`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/docs/verification/phase5/pytest_phase5.txt)

---

## 1. Executive Summary

Phase 5 replaces brittle keyword lists and heuristic gating with a unified, structured LLM claim-checker (`audit_claims_with_llm`), enforces strict database-grounded status checks for third-party integrations, and eliminates ambiguous tool definitions between creation and rescheduling.

Key results achieved:
- **Unified Structured Claim-Checker Schema**: Replaced keyword rules with `{claims_action_done, which_action, claims_external_fact, sourced_by_tool, reason}` in [`agent/verifier.py`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/agent/verifier.py).
- **Direct Reply Interception & Re-run**: In [`agent/loop.py`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/agent/loop.py), ungrounded direct replies on turn 0 (where an action or external fact was claimed without tool execution) are intercepted and re-run with `tool_choice="required"`, forcing tool execution within the strict 2-call LLM budget.
- **Failures 7 & 8 Permanently Fixed**: Bot no longer falsely claims an action was executed without calling tools (e.g. `create_reminder` or `delete_reminder`).
- **Failures 9, 10, 20 & 24 Blocked**: Fictional local entities (*Ansal Plaza, Orbit Mall* in Jodhpur), non-existent grocery orders (*Milk & Bread*), and fabricated highway numbers (*NH-48*) are intercepted and replaced with honest grounded replies (`"Mere paas iski verified information nahi hai."`).
- **Failures 12 & 22 Resolved**: Connected-account queries (*"is my google/github/notion linked?"*) are answered strictly from the database via [`check_integrations_status`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/agent/tools.py#L2613-L2720), never guessed.
- **Failure 19 Resolved (Reschedule vs Create)**: Tool descriptions for `create_reminder` and `update_reminder` were made mutually exclusive and unambiguous. Rescheduling phrases (*"shift meeting to 5pm"*, *"reschedule 4pm meeting to 5pm"*, *"meeting ko 4pm ke badle 5pm shift kar do"*) now update the existing item in-place without creating duplicate rows.
- **Test Execution**: Dedicated Phase 5 suite (**14/14 PASSED in 28.96s**) and full multi-phase regression suite (**73/73 PASSED in 66.99s**).

---

## 2. Deliverables & Technical Changes

### A. Structured Claim-Checker Engine (`agent/verifier.py`)
- Built `CLAIM_CHECKER_SYSTEM_PROMPT` returning structured JSON:
  ```json
  {
    "claims_action_done": false,
    "which_action": null,
    "claims_external_fact": false,
    "sourced_by_tool": false,
    "reason": "assessment explanation"
  }
  ```
- Implemented [`audit_claims_with_llm()`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/agent/verifier.py#L186-L255) using `llm_provider.chat(...)` with `response_format={"type": "json_object"}` and a graceful offline fallback.
- Streamlined [`deterministic_verify()`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/agent/verifier.py#L110-L184):
  - Retained deterministic checks only for verifiable tool-result facts (Google Meet URLs, GitHub repositories from tool listings, tool errors vs claimed success, regional script normalization).
  - Purged all hardcoded mall keyword lists (`['mohan mall', 'madhav mall', 'ratan mall', ...]`) and fabricated highway strings.
- Upgraded [`verify_and_ground_reply()`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/agent/verifier.py#L258-L340):
  - Fast-paths non-claiming conversational phrases (greetings, clarifying questions ending in `?`, explicit refusals).
  - Intercepts any unverified action completion or unsourced external live facts, returning honest fallback text.

### B. Agent Loop Grounding Interception & Re-run (`agent/loop.py`)
- Removed keyword list gating (`any(w in low_prompt for w in ("mall", "route", "highway", ...))`).
- In [`agent/loop.py`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/agent/loop.py#L536-L552):
  - If the model generates a direct text reply on iteration 0 without calling tools, the candidate reply is audited by `verify_and_ground_reply`.
  - If ungrounded (claiming an unexecuted action or external fact), the loop re-runs once with `payload["tool_choice"] = "required"` within the strict 2-call LLM budget.
  - The model is forced to invoke the tool; the tool executes, and synthesis produces verified output.
  - If tool execution fails or facts remain unverified, the hallucinated claim is replaced with honest refusal.

### C. Reschedule vs Create Disambiguation (`agent/tools.py`)
- Updated `create_reminder` description: Explicitly mandates that creation is ONLY for brand-new items and forbids rescheduling or moving existing items.
- Updated `update_reminder` description: Explicitly mandates that rescheduling, moving, shifting, or updating times/durations of existing items MUST call `update_reminder`.
- Added `from_time: Optional[str]` to `UpdateReminderArgs`.
- Fixed matching logic in `execute_tool("update_reminder")`: Rather than incorrectly matching on `due_time` (which is the target NEW time), it matches on `from_time` / original time mentioned in user context, followed by title keyword and single active item fallback.

### D. Connected Integration Database Grounding (`agent/tools.py` & `db.py`)
- Configured [`check_integrations_status`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/agent/tools.py#L2613-L2720) in `CORE_TOOL_NAMES`.
- Added cross-dialect SQLite initialization for `user_integrations` and `email_links` in [`db.py`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/db.py#L2265-L2326), with parameter-safe `now_expr` and `true_expr`.
- Ensured integration status questions are answered directly from the DB (`google_tokens`, `email_links`, `user_integrations`), eliminating false refusals (Failure 22) and guessed connections (Failure 12).

---

## 3. Verification & Test Evidence

### A. Dedicated Phase 5 Test Suite (`tests/test_phase5_grounding.py`)
Command: `.\venv\Scripts\python.exe -m pytest tests/test_phase5_grounding.py -v`
```
tests/test_phase5_grounding.py::test_claim_checker_schema_and_structured_json PASSED [  7%]
tests/test_phase5_grounding.py::test_failure_7_claimed_create_reminder_without_tool_execution PASSED [ 14%]
tests/test_phase5_grounding.py::test_failure_8_claimed_delete_reminder_without_tool_execution PASSED [ 21%]
tests/test_phase5_grounding.py::test_failures_9_and_10_fabricated_malls_blocked PASSED [ 28%]
tests/test_phase5_grounding.py::test_failure_20_fabricated_order_history_blocked PASSED [ 35%]
tests/test_phase5_grounding.py::test_failure_24_fabricated_highway_and_distance_blocked PASSED [ 42%]
tests/test_phase5_grounding.py::test_failure_12_and_22_connected_accounts_answered_from_db PASSED [ 50%]
tests/test_phase5_grounding.py::test_failure_19_reschedule_updates_existing_item[meeting ko 4pm ke badle 5pm shift kar do] PASSED [ 57%]
tests/test_phase5_grounding.py::test_failure_19_reschedule_updates_existing_item[reschedule 4pm meeting to 5pm] PASSED [ 64%]
tests/test_phase5_grounding.py::test_failure_19_reschedule_updates_existing_item[move meeting from 16:00 to 17:00] PASSED [ 71%]
tests/test_phase5_grounding.py::test_failure_19_reschedule_updates_existing_item[shift my meeting to 5pm] PASSED [ 78%]
tests/test_phase5_grounding.py::test_failure_19_reschedule_updates_existing_item[4 baje wali meeting 5 baje kardo] PASSED [ 85%]
tests/test_phase5_grounding.py::test_agent_loop_grounding_interception_and_rerun PASSED [ 92%]
tests/test_phase5_grounding.py::test_general_chat_and_math_pass_grounding_without_tools PASSED [100%]

====================== 14 passed, 24 warnings in 28.96s =======================
```

### B. Full Multi-Phase Regression Suite
Command: `.\venv\Scripts\python.exe -m pytest tests/test_phase1_time_and_clash.py tests/test_crashes_and_fallback.py tests/test_phase2_llm_adapter.py tests/test_phase3_memory.py tests/test_phase4_prompt_and_routing.py tests/test_phase5_grounding.py tests/test_verifier.py tests/test_phase3_tools_and_verifier.py tests/test_nlu_and_parsing.py -v`
```
================= 73 passed, 41 warnings in 66.99s (0:01:06) ==================
```
All 73 tests across Phases 1, 2, 3, 4, 5, Verifier, and NLU parsing passed cleanly with 0 failures.
Raw output saved to: [`docs/verification/phase5/pytest_phase5.txt`](file:///c:/Users/neera/OneDrive/ai agent/ai-agent/docs/verification/phase5/pytest_phase5.txt).
