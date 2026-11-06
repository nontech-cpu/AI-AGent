# Phase 7b: Event Memory Architecture & Design Specification

## 1. Executive Summary & Problem Context
In Step 1 diagnosis, we verified that:
1. The historical `action_events` table referenced in legacy design documents does not exist in PostgreSQL.
2. In Phase 3, the hardcoded keyword injection for "Recent Action Ledger" was removed from `memory/retrieval.py`.
3. When actions occurred (such as outbound emails, calendar meetings, or reminder creations), they were either stored in ephemeral `pending_actions` or scattered across tool outputs in `turn_traces`, without being persisted to the cognitive memory pipeline.
4. Consequently, when the user asked *"mene aaravmishra ko ek mail send kra rh tum bta skte ho vo"*, the system had zero memory rows to retrieve and failed over to Gmail tool search.

This document specifies the end-to-end design for **Event Memory**: every successful connector action writes a first-class memory item to the unified `memories` table, fully indexed for hybrid semantic + lexical search, with zero regex-gating and strict privacy enforcement.

---

## 2. Event Memory Schema Contract

All event memories live in the existing `memories` table under `memory_type = 'event'`.

| Column | Type | Value & Constraint |
|---|---|---|
| `user_id` | `INTEGER` | Resolved user ID (FK to `users.id`) |
| `memory_type` | `VARCHAR(32)` | `'event'` |
| `content` | `TEXT` | Concise, 1-line summary strictly formatted as `[Action] [Entity/Recipient] - [Subject/Title] at [Timestamp/Date]` (Max 200 chars). Full bodies, email contents, OTPs, passwords, and tokens are strictly banned. |
| `structured` | `JSONB` | Structured metadata for exact filtering and foreign-key linking: `{ "action": "...", "recipient": "...", "subject": "...", "source_ids": { ... } }` |
| `subject_entity_id`| `INTEGER` | Optional FK to `entities.id` (e.g. contact ID for recipient Aarav Mishra) |
| `source_type` | `VARCHAR(32)` | Connector identifier: `'gmail'`, `'google_calendar'`, `'notion'`, `'github'`, `'reminder'` |
| `importance` | `INTEGER` | Default `4` (high cognitive salience for past user actions) |
| `status` | `VARCHAR(16)` | `'active'` (or `'archived'`) |
| `created_at` | `TIMESTAMPTZ` | Timestamp when the action was performed |

### Example Row
```json
{
  "user_id": 408,
  "memory_type": "event",
  "content": "Sent email to aaravmishra619@gmail.com with subject 'weather' on 31 Oct 2026",
  "structured": {
    "action": "email_sent",
    "recipient": "aaravmishra619@gmail.com",
    "subject": "weather",
    "source_ids": {
      "gmail_message_id": "192e22c9bb0d58aa",
      "thread_id": "192e22c9bb0d58aa"
    }
  },
  "source_type": "gmail",
  "importance": 4,
  "status": "active",
  "created_at": "2026-10-31T16:31:58.339691Z"
}
```

---

## 3. Synchronous Emission Pipeline

Event memory insertion occurs **synchronously** immediately upon confirmed execution of any connector action:

1. **Outbound Gmail**:
   - Location: `connectors/gmail.py:send_email()` and `state/pending.py:confirm_pending_action()`
   - Emits: `action='email_sent'`, `content="Sent email to {to} with subject '{subject}'"`, `source_ids={"gmail_message_id": msg_id}`.
2. **Google Calendar**:
   - Location: `agent/tools.py:create_meeting()`, `reschedule_calendar_event()`, `cancel_meeting()`
   - Emits: `action='calendar_created'|'calendar_updated'|'calendar_deleted'`, `content="Scheduled calendar meeting '{title}' for {start_time}"`.
3. **Notion**:
   - Location: `agent/tools.py:create_notion_page()`, `notion_append_block()`
   - Emits: `action='notion_page_created'`, `content="Created Notion page '{title}'"`, `source_ids={"notion_page_id": page_id}`.
4. **GitHub**:
   - Location: `agent/tools.py:update_github_file()`, `github_create_issue()`, `github_create_pr()`
   - Emits: `action='github_commit'|'github_issue_created'`, `content="Created GitHub issue #{num} '{title}' in {repo}"`.
5. **Reminders**:
   - Location: `agent/tools.py:create_reminder()`, `update_reminder()`, `delete_reminder()`
   - Emits: `action='reminder_created'|'reminder_updated'|'reminder_deleted'`.

### Failure Policy:
- If saving the memory row encounters a database transient failure, it is placed into `embedding_retry_queue` / `db_failure_log` with the current `trace_id`. It is **never** silently ignored or swallowed.

---

## 4. Retrieval Integration (Hybrid Search)

Hybrid retrieval in `memory/retrieval.py` indexes `memories` rows where `memory_type IN ('fact', 'preference', 'event', 'commitment')`.

1. **Semantic Embedding**:
   - When an event row is inserted, its summary text (e.g. *"Sent email to Aarav Mishra with subject weather"*) is queued for vector embedding.
2. **BM25 / Trigram Lexical Search**:
   - Lexical search naturally matches queries like *"aarav ko mail"*, *"kisko kya bheja tha"*, *"last email to rahul"*.
3. **Context Formatting**:
   - Event memory rows are rendered cleanly in the prompt under `[Recent Confirmed Events & Actions]` alongside other retrieved memories, giving the LLM immediate knowledge of prior actions without needing an explicit tool call.

---

## 5. Privacy, Retention & `/forget` Support

1. **Content Minimization**: Full email bodies, meeting descriptions, attachments, and secrets are strictly forbidden in `content`. Only the subject, recipient, date, and title are preserved.
2. **Cascade Deletion**:
   - `/forget <topic>` or `/forget Aarav`: Deletes or archives all entities, facts, and event rows associated with that recipient/topic.
   - Master Wipe (`POST /api/user/wipe` or `/reset`): Purges all rows from `memories` where `user_id = :uid`, including all `memory_type='event'` rows.

---

## 6. Backfill DRY-RUN Summary (Awaiting Approval)

A dry-run backfill script was generated and run against current production read-only records:
- **Output File**: [`docs/verification/phase7b/dry_run_event_memory_backfill.json`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/docs/verification/phase7b/dry_run_event_memory_backfill.json)
- **Total Rows Found**: 8 rows
  - `reminder`: 6 rows (past reminders created/deleted)
  - `gmail`: 1 row (sent email to `aaravmishra619@gmail.com` with subject `weather`)
  - `google_calendar`: 1 row (calendar meeting `Sprint Planning`)

> [!IMPORTANT]
> **Status**: Dry-run verified. Zero rows have been written to the production database. Awaiting user approval in Step 8 before applying backfill.

