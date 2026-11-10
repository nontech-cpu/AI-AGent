# Step 1: Email Recall Diagnosis Report

**Date:** 2026-11-06  
**Target User:** `user_id = 408` (`chat_id = [REDACTED_CHAT_SECONDARY]`)  
**Investigated Turn:** 11:22 PM IST (2026-11-05 17:52:09 UTC)  
**Query:** *"Bhy mene aaravmishra ko ek mail send kra rh tum bta skte ho vo"*

---

## 1. Raw Diagnostic Evidence

### a. `turn_traces` Rows for 11:22 PM Turns
From `turn_traces` table in Neon PostgreSQL:
- **Turn ID 1082** (`2026-11-05 17:52:28.890888+00:00` / 11:22:28 PM IST):
  - **User Message**: `"Bhy mene aaravmishra ko ek mail send kra rh tum bta skte ho vo"`
  - **Tools Called**: `['search_user_emails', 'check_integrations_status', 'search_user_emails', 'search_user_emails']`
  - **Tool Results (Redacted)**:
    ```json
    [
      {
        "type": "UnboundLocalError",
        "action": "search_user_emails",
        "status": "error",
        "message": "cannot access local variable 'google_services' where it is not associated with a value",
        "tool_name": "search_user_emails"
      },
      {
        "action": "check_integrations_status",
        "status": "success",
        "service": "google",
        "connected": true,
        "email": "<REDACTED_EMAIL>",
        "tool_name": "check_integrations_status"
      },
      {
        "type": "UnboundLocalError",
        "action": "search_user_emails",
        "status": "error",
        "message": "cannot access local variable 'google_services' where it is not associated with a value",
        "tool_name": "search_user_emails"
      }
    ]
    ```
  - **Memory Status**: `None`
  - **Provider / Model**: `None / None` (Render was running old commit `3c27a2e` where telemetry columns were unpopulated)
  - **Error Type**: `None` | **LLM Calls**: 0 | **Latency**: 18,499 ms
- **Turn ID 1083** (`2026-11-05 17:53:24.883370+00:00` / 11:23:24 PM IST):
  - Same query, same exact `UnboundLocalError` on `search_user_emails`.
- **Turn ID 1084** (`2026-11-06 01:54:12.894054+00:00` / 07:24:12 AM IST):
  - Message: `"Bhy mene toh aaravmishra ko ek mail bhja th vo btna"`
  - Same exact `UnboundLocalError`.

---

### b. `action_events` Analysis & Origin of the Sent Mail
- **Table Existence**: The table named `action_events` **does not exist** in the PostgreSQL schema.
- **Where Action Events Live**: `record_action_event` in `memory/store.py:1419` is designed to insert into the unified `memories` table with `memory_type = 'action_event'`.
- **Querying `memories` for `action_event`**:
  ```sql
  SELECT COUNT(*) FROM memories WHERE memory_type = 'action_event';
  -- Result: 0 rows in entire database
  ```
- **Historical Chat Log Check for Sent Mail**:
  Searching `chat_logs` for `aarav`:
  - **ID 2581** (`2026-10-31 16:31:56 UTC`): User requested: `"send a mail to aaravmishra619@gmail.com and <REDACTED> subject:-weather body to hot in jodhpur"`
  - **ID 2586** (`2026-10-31 16:32:06 UTC`): Bot confirmed: `"✅ Email sent successfully! To: aaravmishra619@gmail.com... Subject: weather"`
- **Why It Was Never Logged**:
  On 2026-10-31 (5 days before Phase 1), `main.py:1848` directly invoked `google_send_email(...)`. Neither `record_action_event` nor any database action ledger was hooked into that execution path. The email lived purely in the raw text of `chat_logs` row 2586 and the user's Gmail Sent folder.

---

### c. Google OAuth Scopes Stored for the Account
Querying `google_tokens` for `user_id = 408`:
- **Connected Email**: `neeravmishrarealme1@gmail.com`
- **Stored Scopes**:
  ```
  https://www.googleapis.com/auth/calendar.events
  https://www.googleapis.com/auth/gmail.send
  https://www.googleapis.com/auth/userinfo.email
  https://www.googleapis.com/auth/drive.file
  openid
  https://www.googleapis.com/auth/gmail.readonly
  ```
- **Finding**: **The token DOES contain `gmail.readonly`!**
  The user does NOT need to re-consent for Gmail read access. The failure was NOT a Google OAuth 403 or missing scope.

---

### d. Exact Error & Code Path That Produced "technical dikkat"
1. In [`agent/tools.py:3391`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/tools.py#L3391):
   ```python
   # Check if this chat is known to lack Gmail read scope
   if int(chat_id) in getattr(google_services, "_CHAT_GMAIL_INSUFFICIENT_SCOPES", set()):
   ```
2. `google_services` was never imported in `agent/tools.py` (either at top-level or locally within the `search_user_emails` handler).
3. Python raised:
   `UnboundLocalError: cannot access local variable 'google_services' where it is not associated with a value`
4. In [`agent/tools.py:3640`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/tools.py#L3640), `execute_tool` caught the exception:
   ```python
   except Exception as exc:
       return {"status": "error", "error": "tool_execution_error", "type": type(exc).__name__, "message": str(exc)}
   ```
5. On the subsequent synthesis iteration in [`agent/loop.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py), the LLM received this error dictionary and synthesized:
   > *"Bhai, lagta hai email search karne mein kuch technical dikkat aa rahi hai. Main dobara koshish karta hoon, lekin tab tak aap mujhe ye bata sakte hain ki aapne email mein kya bheja tha ya kab bheja tha?"*
6. Additionally, even if `google_services` had been imported, `search_user_emails` in `agent/tools.py:3406` sets `gmail_query = user_q if user_q else "in:inbox"`, which queries only the Inbox and cannot find emails in `in:sent`.

---

### e. Prompt Injection Analysis: Sent Email History
- In `3c27a2e` (before Phase 3), `memory/retrieval.py` had a keyword-gated block:
  ```python
  # Global Recent Actions Lookup (When user asks about sent emails or past actions without naming entity)
  if any(w in low for w in ("mail", "email", "sent", "bheja", "bheji", "send", "subject", "kya bheja")):
      from memory.store import get_recent_actions_for_user
      global_acts = get_recent_actions_for_user(user_id, limit=3)
      if global_acts:
          lines.append("[Recent Action Ledger (Emails & Tasks Executed by Bot)]:")
  ```
- In Phase 3, this keyword block was eliminated as part of eradicating brittle stopword heuristics in favor of the single unified `memories` hybrid RAG.
- Currently, **zero lines in `agent/prompt.py` or `memory/retrieval.py` inject sent email history into the prompt**. Sent email recall depends entirely on tool execution (`search_user_emails`) or finding a memory row in `memories`.

---

## 2. Conclusion

Which of the three root causes is true?

| Statement | True / False | Evidence |
| :--- | :--- | :--- |
| **(i) Recall regression from Phase 3** | **TRUE** | Phase 3 cleanly eradicated the fragile `[Recent Action Ledger]` keyword gate from `memory/retrieval.py`, but connector action writing to `memories` was not unified, so sent emails are no longer passively injected into system context. |
| **(ii) Gmail scope/auth failure with a swallowed error** | **PARTIAL (Code Crash Swallowed, NOT Scope Failure)** | The token actually has `https://www.googleapis.com/auth/gmail.readonly`. The error was an `UnboundLocalError` (`google_services` not imported in `agent/tools.py:3391`), which was caught and swallowed into a vague conversational reply asking the user for details. |
| **(iii) The mail was never logged** | **TRUE** | On 2026-10-31 when the email was sent, `main.py:1848` only invoked `google_send_email`. No row was inserted into any action table or `memories`. |

