# Step 8: Pending Approvals (Do Not Execute)

> [!IMPORTANT]
> **No changes have been applied to production database or file deletions.**
> This document lists all proposed actions and exact specifications for user review and explicit approval.

---

## 1. Candidate Files Proposed for Deletion

| File | Lines | Purpose / History | Remaining Importers | Safe Deletion Plan |
|---|---|---|---|---|
| **`llm/legacy.py`** | 813 lines | Pre-agent heuristic extraction and regex intent-parsing logic from early iterations. | `llm/__init__.py:30` (`from . import legacy as _legacy`) | Remove re-export from `llm/__init__.py`. All active components call `llm.provider.chat()`. |
| **`nlu.py`** | 396 lines | Rule-based regex NLP (`yes_no`, `match_persona`, `PERSONA_LABELS`). | `main.py:43` (legacy setup flows: consent, persona), `tests/test_adversarial_and_security.py:13`, `tests/test_nlu_and_parsing.py:8` | Migrate setup consent/persona confirmations to `classify_confirmation_intent_with_llm` in `state/pending.py`, then remove file and obsolete unit tests. |
| **`lang.py`** | 361 lines | Legacy language detection heuristics and Whisper language code mapping. | `main.py:48` (used in legacy onboarding flows) | Consolidate language selection into user preferences table (`users.language`) and prompt dynamic language mirroring. |
| **`main.py:2470-3225` (v1 Branch)** | ~755 lines | Dead v1 monolithic if-else routing branch for Notion, GitHub, Meet links, weather, and reminders. | Dead code; `handle_active` returns early at line 2435 via `AGENT_CORE=v2`. | Excise lines 2470-3225 from `main.py` once `AGENT_CORE=v2` is hardened as the sole production engine. |

---

## 2. Proposed Production Database Changes (Awaiting Approval)

### A. Event Memory Backfill
- **Target Table**: `memories`
- **Candidate Rows Found**: 8 rows (from confirmed `pending_actions` and `turn_traces`)
- **DRY-RUN Output File**: [`docs/verification/phase7b/dry_run_event_memory_backfill.json`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/dry_run_event_memory_backfill.json)

### Proposed SQL Statements (DRY-RUN):
```sql
-- 1. Sent Email to Aarav Mishra
INSERT INTO memories (user_id, memory_type, content, source_type, structured, importance, status, created_at)
VALUES (
    408,
    'event',
    'Sent email to aaravmishra619@gmail.com, neeravmishra1805@gmail.com with subject ''weather''',
    'gmail',
    '{"action": "email_sent", "recipient": "aaravmishra619@gmail.com, neeravmishra1805@gmail.com", "subject": "weather", "pending_action_id": 37}'::jsonb,
    4,
    'active',
    '2026-10-31 16:31:58.339691+00'
);

-- 2. Calendar Meeting 'Sprint Planning'
INSERT INTO memories (user_id, memory_type, content, source_type, structured, importance, status, created_at)
VALUES (
    408,
    'event',
    'Created calendar meeting ''Sprint Planning'' scheduled for 2026-10-22T14:00:00+05:30',
    'google_calendar',
    '{"action": "calendar_created", "title": "Sprint Planning", "start_time": "2026-10-22T14:00:00+05:30", "pending_action_id": 5}'::jsonb,
    4,
    'active',
    '2026-10-22 07:36:34.144859+00'
);

-- 3. Reminder 'Movie' Creation
INSERT INTO memories (user_id, memory_type, content, source_type, structured, importance, status, created_at)
VALUES (
    408,
    'event',
    'Created reminder ''Movie'' for 2026-11-06 at 13:00',
    'reminder',
    '{"action": "reminder_created", "title": "Movie", "due_date": "2026-11-06", "due_time": "13:00", "trace_id": 1078}'::jsonb,
    4,
    'active',
    '2026-11-05 17:10:48.349079+00'
);

-- 4. Reminder 'Movie jana hai, Movie (3 hours long)' Deletion
INSERT INTO memories (user_id, memory_type, content, source_type, structured, importance, status, created_at)
VALUES (
    408,
    'event',
    'Deleted reminder ''Movie jana hai, Movie (3 hours long)''',
    'reminder',
    '{"action": "reminder_deleted", "title": "Movie jana hai, Movie (3 hours long)", "trace_id": 1062}'::jsonb,
    3,
    'active',
    '2026-11-05 16:29:41.139626+00'
);
```

---

## 3. Remaining Pre-LLM Shortcuts Tracker

Updated in [`docs/KNOWN_ISSUES.md`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/KNOWN_ISSUES.md):
1. **Notion Token Direct Pasting Fast-Path**: [`agent/loop.py:151-170`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L151-L170)
2. **GitHub Token Direct Pasting Fast-Path**: [`agent/loop.py:172-192`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L172-L192)
3. **Pending Confirmation Intercept**: [`agent/loop.py:201-261`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L201-L261)
4. **Telegram Slash Commands Fast-Path**: [`main.py:120-210`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L120-L210)

