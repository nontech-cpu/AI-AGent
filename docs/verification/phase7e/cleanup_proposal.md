# Phase 7e: Step 7 - Production Database Duplicate Rows Cleanup Proposal

## Background & Trace Evidence
During live testing on 10 Nov 2026, two duplicate 5:00 PM reminders were created for user `[REDACTED_CHAT_PRIMARY]` (db `user_id=306`):

1. **Row 1 (`title="Meeting"`, 17:00 IST)**:
   - **Trace ID**: `1206` (`tr_a35f18ed4b66`)
   - **Created At**: `2026-11-10 02:16:48.004805+00:00` (07:46:48 AM IST)
   - **Prompt**: `"bhy aaj reminder lgaa dena 5pm ka meeting h 1hour ke"`
   - **Action**: Correctly created Telegram reminder for Meeting at 17:00.

2. **Row 2 (`title="Meeting Reminder"`, 17:00 IST)**:
   - **Trace ID**: `1210` (`tr_0e239efed057`)
   - **Created At**: `2026-11-10 02:20:50.502370+00:00` (07:50:50 AM IST)
   - **Prompt**: `"bhy new meetinf set nhi krni h uska he bol rha hu m kr iss meeting jo phele s 5pm pr h uska reminder tumn idhr lgya h uska calnder or reminder lga do"`
   - **Why created**: User intended to sync the existing 5 PM meeting to Google Calendar ("uska calnder or reminder lga do"). Because the system lacked clean calendar separation, the model invoked `create_reminder` with `title="Meeting Reminder"`, causing a duplicate row at 17:00.

---

## Proposed Remediation SQL (Read-Only Proposal - Not Executed)
To safely clean up the orphaned duplicate row while preserving user's intended state:

```sql
-- 1. Inspect existing active rows at 17:00 for user 306
SELECT id, title, category, due_date, due_time, duration_minutes, recurrence
FROM memory_items
WHERE user_id = 306 
  AND due_date = '2026-11-10' 
  AND due_time = '17:00:00';

-- 2. Proposed targeted cleanup: Remove the duplicate 'Meeting Reminder' created by Trace 1210:
-- DELETE FROM memory_items 
-- WHERE user_id = 306 
--   AND title = 'Meeting Reminder' 
--   AND due_date = '2026-11-10' 
--   AND due_time = '17:00:00';
```

Per strict Phase 7e instructions, no write operations are performed against production database without explicit administrative confirmation.

