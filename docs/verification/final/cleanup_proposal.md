# Production Database Cleanup & Incident Restore Proposal (Read-Only)

> **SAFETY NOTICE**: THIS DOCUMENT CONTAINS PROPOSED SQL ONLY. ZERO WRITES, RESTORES, OR DELETIONS HAVE BEEN EXECUTED AGAINST THE PRODUCTION DATABASE.

---

## 1. Safety Incident Forensic Background (Trace 1233)

On **10 Nov 2026 at 08:17:31 UTC** (1:47 PM IST), during live Telegram testing with user `Nirav` (`telegram_chat_id = [REDACTED_CHAT_ID_PRIMARY]`, internal `user_id = 306`), an unauthorized deletion occurred in **Trace 1233** (`tr_29e766296ff8`).

### Sequence of Events:
1. **Prior Turn (Trace 1232 / chat_log [4468] at 07:48:41 UTC)**:
   - User: `"i didnt the said to set up meeting"`
   - Assistant: `"I apologize for the confusion. I see that there are multiple entries for 5:00 PM in your schedule: a 'Meeting' and a 'Meeting Reminder.' Since you did not ask for these, would you like me to delete them for you?"`
2. **Incident Turn (Trace 1233 at 08:17:31 UTC)**:
   - User sent a casual greeting: `"hi"`
   - The LLM (`gemini-3.1-flash-lite-preview`) hallucinated that `"hi"` was affirmative consent to delete the meetings, calling:
     `delete_reminder(title="Meeting, Meeting Reminder, Meeting, Meeting, Meeting, Meeting")`
   - The legacy `delete_reminder` implementation split tokens, matched `LIKE '%meeting%'`, and issued a hard SQL `DELETE FROM memory_items` without confirmation.
   - **6 meeting rows** belonging to `user_id = 306` were permanently hard-deleted.

---

## 2. Re-Query Verification: Current State of Production DB

Re-running read-only inspection against production Neon PostgreSQL confirms that the duplicate rows targeted by Phase 7e are **already gone** from `memory_items` due to the Trace 1233 incident:

```sql
-- Read-Only Verification Query
SELECT id, user_id, category, title, due_date, due_time, duration_minutes, recurrence, created_at 
FROM memory_items 
WHERE user_id = 306 
  AND title ILIKE '%meeting%';
```

**Current Result**:
Only one newly created meeting reminder exists (created in Trace 1243 *after* the incident):
- `id = 257`: `"Meeting with Devender"`, `due_date = 2026-11-11`, `due_time = 09:00:00`.
All prior 6 meeting rows (including the duplicate 5 PM meetings) are confirmed absent.

---

## 3. ID-Based Transactional Cleanup Proposal (If Any Ghost Rows Exist)

If any orphaned records exist in linked tables (`reminders` or `event_memory`), cleanup must be transactional, strictly ID-based (never title-based), and previewed first:

```sql
BEGIN;

-- Step 1: Preview exact rows by ID before deletion
SELECT id, user_id, category, title, due_date, due_time 
FROM memory_items 
WHERE id IN (/* Explicit list of IDs verified by user */)
  AND user_id = 306;

-- Step 2: Targeted deletion by exact primary key ID only
-- DELETE FROM reminders WHERE item_id IN (/* Explicit IDs */);
-- DELETE FROM memory_items WHERE id IN (/* Explicit IDs */) AND user_id = 306;

-- Step 3: Verify count deleted matches expected count exactly
-- If count != expected, ROLLBACK; otherwise COMMIT;
ROLLBACK; -- Safe default in proposal
```

---

## 4. Reconstructive Restore Proposal (Read-Only)

Using provenance records preserved in `turn_traces`, `chat_logs`, and immutable event memories in the `memories` table, the 6 deleted rows have been forensically reconstructed.

### Provenance Audit Trail:
1. **Event 403 (Trace 1163)**: Set reminder: `'Meeting'` for `2026-11-08 17:00:00` (duration 30m)
2. **Event 404 (Trace 1169)**: Set reminder: `'Meeting'` for `2026-11-08 18:00:00` (duration 30m)
3. **Event 405 (Trace 1171)**: Set reminder: `'Meeting'` for `2026-11-07 19:00:00` (duration 60m)
4. **Event 420 (Trace 1206)**: Set reminder: `'Meeting'` for `2026-11-10 17:00:00` (duration 60m)
5. **Event 421 (Trace 1210)**: Set reminder: `'Meeting Reminder'` for `2026-11-10 17:00:00` (duration 30m - duplicate)
6. **Event 430 (Trace 1229)**: Set reminder: `'Meeting'` for `2026-11-10 13:00:00` (duration 30m)

### Proposed Restoration SQL (For Human Review - NOT Executed):

```sql
BEGIN;

-- Restore valid past and intended meetings (excluding duplicate 'Meeting Reminder')
INSERT INTO memory_items (user_id, category, title, due_date, due_time, duration_minutes, recurrence, status, created_at)
VALUES
  (306, 'appointments', 'Meeting', '2026-11-08', '17:00:00', 30, 'none', 'active', '2026-11-07 12:05:30+00'),
  (306, 'appointments', 'Meeting', '2026-11-08', '18:00:00', 30, 'none', 'active', '2026-11-07 12:12:52+00'),
  (306, 'appointments', 'Meeting', '2026-11-07', '19:00:00', 60, 'none', 'active', '2026-11-07 12:15:07+00'),
  (306, 'appointments', 'Meeting', '2026-11-10', '17:00:00', 60, 'none', 'active', '2026-11-10 02:16:46+00'),
  (306, 'appointments', 'Meeting', '2026-11-10', '13:00:00', 30, 'none', 'active', '2026-11-10 07:37:57+00');

-- Commit only upon explicit user instruction
ROLLBACK;
```

---

## 5. Row 257 & Devender Meeting Forensic Investigation (A5.8)

### Forensic Findings:
- **Row Details**: `memory_items.id = 257`, `user_id = 306`, `category = 'appointments'`, `title = 'Meeting with Devender'`, `due_date = 2026-11-11`, `due_time = 09:00:00`, `created_at = 2026-11-10 08:46:48 GMT`.
- **Linked Google Calendar Event**: Event ID `r704lglg3t37seu596q8rqfm4o` (`'🔔 Meeting with Devender'`), logged in `memories.id = 445` at `08:46:50 GMT`.
- **Trigger Turn (Trace 1243 / `tr_1d5118f088c0`)**:
  - **User Prompt**: `"tommorow i the have meeting with devender remind me for that"`
  - **Model Arguments Emitted**: `create_reminder(title='Meeting with Devender', due_date='2026-11-11', due_time='09:00')`
  - **Root Cause**: **Ungrounded Value Invention**. The user never provided a time for the meeting. Instead of asking the user for the time, the model hallucinated `09:00` out of thin air and executed `create_reminder`, which simultaneously auto-synced the event to Google Calendar.

### Proposed Cleanup SQL for Row 257 (Read-Only):
```sql
BEGIN;

-- Preview row 257
SELECT id, user_id, title, due_date, due_time, status 
FROM memory_items 
WHERE id = 257 AND user_id = 306;

-- Soft-delete in memory_items (if user confirms removal):
-- UPDATE memory_items SET status = 'deleted' WHERE id = 257 AND user_id = 306;

-- Associated Google Calendar event to delete via API: 'r704lglg3t37seu596q8rqfm4o'

ROLLBACK; -- Safe default in proposal
```

