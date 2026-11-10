# Phase 7e: Step 0 - Production Trace Investigation Report

Investigation of Telegram live failure incidents occurring on 10 Nov 2026 (~12:32 PM - 1:18 PM IST / 07:02 - 07:48 UTC) extracted directly from `turn_traces` and `chat_logs` in the live PostgreSQL database.

---

## Turn Trace Breakdown

### Turn a: "mere daily reminder set krdo joggina ka 10am ka"
- **Timestamp**: `2026-11-10 07:02:11.554984+00:00` (12:32:11 PM IST)
- **Trace ID**: `1216` (`tr_81b98a2b4d9d`)
- **User Message**: `"mere daily reminder set krdo joggina ka 10am ka"`
- **Reply Path**: `tool_calling_v2`
- **Tools Called & Args**: 
  - `create_reminder`: `{"title": "Jogging", "due_date": "2026-11-10", "due_time": "10:00", "duration_minutes": 30}`
- **Tool Results**: 
  - `{"action": "create_reminder", "status": "success", "title": "Jogging", "due_date": "2026-11-10", "due_time": "10:00", "duration_minutes": 30, "conflicts": [], "message": "Done bhai! 'Jogging' ka reminder 10 Nov 2026 ko 10:00 AM ke liye set kar diya hai. (Telegram reminder set ✅)"}`
- **Provider / Model**: `Gemini` / `gemini-3.1-flash-lite-preview`
- **LLM Calls**: `1`
- **Reply Source**: **String returned by tool** (`tool_result["message"]`). Fast-path in `agent/loop.py` lines 573-588 directly returned the hardcoded tool string without LLM synthesis.
- **Root Cause**: The model omitted recurrence support (not present in deployed schema), and server had zero temporal validation checking whether 10:00 AM was already past at 12:32 PM.

---

### Turn b: Gym (8 PM daily) & Meeting (1 PM daily)

#### Sub-turn b1: Gym 8 PM
- **Timestamp**: `2026-11-10 07:36:16.919733+00:00` (1:06:16 PM IST)
- **Trace ID**: `1227` (`tr_41093563e6e9`)
- **User Message**: `"bhy mere toh roj ka 8pm ka gym ka remidner get krdo gym ka 1hour ka session rhyga roj ka"`
- **Reply Path**: `tool_calling_v2`
- **Tools Called & Args**: 
  - `create_reminder`: `{"title": "Gym session", "due_date": "2026-11-10", "due_time": "20:00", "duration_minutes": 60}`
- **Tool Results**: 
  - `{"action": "create_reminder", "status": "success", "title": "Gym session", "due_date": "2026-11-10", "due_time": "20:00", "duration_minutes": 60, "conflicts": [], "message": "Done bhai! 'Gym session' ka reminder 10 Nov 2026 ko 08:00 PM ke liye set kar diya hai. (Telegram reminder set ✅)"}`
- **Provider / Model**: `Gemini` / `gemini-3.1-flash-lite-preview`
- **LLM Calls**: `1`
- **Reply Source**: **String returned by tool** (`tool_result["message"]`) via `agent/loop.py` line 587.

#### Sub-turn b2: Meeting 1 PM (past time)
- **Timestamp**: `2026-11-10 07:38:02.003380+00:00` (1:08:02 PM IST)
- **Trace ID**: `1229` (`tr_aede69603dc0`)
- **User Message**: `"bhy mere toh 1pm ka reminder lgaa dena meeting ka roj daily"`
- **Reply Path**: `tool_calling_v2`
- **Tools Called & Args**: 
  - `create_reminder`: `{"title": "Meeting", "due_date": "2026-11-10", "due_time": "13:00", "duration_minutes": 30}`
- **Tool Results**: 
  - `{"action": "create_reminder", "status": "success", "title": "Meeting", "due_date": "2026-11-10", "due_time": "13:00", "duration_minutes": 30, "conflicts": [], "message": "Done bhai! 'Meeting' ka reminder 10 Nov 2026 ko 01:00 PM ke liye set kar diya hai. (Telegram reminder set ✅)"}`
- **Provider / Model**: `Gemini` / `gemini-3.1-flash-lite-preview`
- **LLM Calls**: `1`
- **Reply Source**: **String returned by tool** (`tool_result["message"]`).
- **Root Cause**: Requested 13:00 at 13:08 IST. Server lacked time-of-day validation; blindly scheduled in the past.

---

### Turn c: "joggin wala reminder calender pr b lga do"
- **Timestamp**: `2026-11-10 07:08:36.016900+00:00` (12:38:36 PM IST)
- **Trace ID**: `1220` (`tr_2c1eb7a00f27`)
- **User Message**: `"bhy joggin wala remidner calnder pr b lga do"`
- **Reply Path**: `tool_calling_v2`
- **Tools Called & Args**: 
  - `create_meeting`: `{"title": "Jogging", "start_iso": "2026-11-11T10:00:00", "duration_minutes": 30}`
- **Tool Results**: 
  - `{"action": "create_meeting", "status": "needs_confirmation", "title": "Jogging", "start_iso": "2026-11-11T10:00:00", "duration_minutes": 30, "attendees": []}`
- **Provider / Model**: `Gemini` / `gemini-3.1-flash-lite-preview`
- **LLM Calls**: `1`
- **Reply Source**: **String returned by state handler** (`agent/loop.py` line 740):
  `"Please confirm — create this Google Calendar meeting with a Google Meet link?\nTitle: Jogging\nTime: 10:00 AM (India time)\nDuration: 30 minutes\nAttendees: None\n\nReply yes to create or no to cancel."`
- **Which Tool & Why Meet?**: 
  The LLM called `create_meeting`. It did this because the deployed codebase had no standalone `create_calendar_event` tool. `create_meeting` was hardcoded to prepare a video conference meeting with Google Meet link gated by two-phase confirmation.

---

### Turn d: "meetinv nhi toh banani h sirf jogging ka reminder lgana h" (Sent Twice)

#### Part d1 (First Attempt - Intercepted & Cancelled):
- **User Message**: `"meetinv nhi toh banani h sirf jogging ka reminder lgana"`
- **Trace in turn_traces**: None.
- **Why no trace?**: In `agent/loop.py` lines 210-240, active pending state handler intercepted the message. The classifier mapped the negative prefix to `intent="cancel"`, executed `cancel_pending_action(chat_id)`, and returned hardcoded string: `"Action cancel kar diya gaya hai. Kuch bhi schedule ya send nahi kiya gaya."` directly, dropping the rest of the user prompt without executing the agent loop or logging to `turn_traces`.

#### Part d2 (Second Attempt - Fallback to Reminder):
- **Timestamp**: `2026-11-10 07:09:17.701883+00:00` (12:39:17 PM IST)
- **Trace ID**: `1221` (`tr_94a788a2b2cd`)
- **User Message**: `"meetinv nhi toh banani h sirf jogging ka reminder lgana h"`
- **Reply Path**: `tool_calling_v2`
- **Tools Called & Args**: 
  - `create_reminder`: `{"title": "Jogging", "due_date": "2026-11-10", "due_time": "10:00", "duration_minutes": 30}`
- **Tool Results**: 
  - `{"action": "create_reminder", "status": "success", "title": "Jogging", "due_date": "2026-11-10", "due_time": "10:00", "rescheduled": True, "message": "Done bhai! 'Jogging' ka reminder 10 Nov 2026 ko 10:00 AM ke liye shift kar diya hai. (Telegram reminder set ✅)"}`
- **Provider / Model**: `Gemini` / `gemini-3.1-flash-lite-preview`
- **LLM Calls**: `1`
- **Reply Source**: **String returned by tool** (`tool_result["message"]`).
- **Root Cause**: Since pending state was cleared by d1, d2 was parsed by LLM as setting a reminder for Jogging at 10:00 AM. It set it for 10 Nov 10:00 AM (2.5 hours in past) instead of calendar sync.

---

### Turn e: "now from the conversation talk to me in english only" & Bollywood Movies Hinglish Regression

#### Sub-turn e1: Preference Command
- **Timestamp**: `2026-11-10 07:11:24.206591+00:00` (12:41:24 PM IST)
- **Trace ID**: `1222` (`tr_f9a62a69f27f`)
- **User Message**: `"now from the cinversatonn talk in the me in english only"`
- **Reply Path**: `tool_calling_v2`
- **Tools Called & Args**: 
  - `save_note_or_fact`: `{"fact": "The user prefers to communicate in English only."}`
- **Tool Results**: 
  - `{"action": "save_note_or_fact", "status": "success", "saved_fact": "The user prefers to communicate in English only."}`
- **Provider / Model**: `Gemini` / `gemini-3.1-flash-lite-preview`
- **LLM Calls**: `2`
- **Reply Source**: **LLM Generated Text**:
  `"Understood. I have updated my settings to ensure that I respond to you in English from now on, regardless of the language you use. How can I assist you today?"`
- **Root Cause**: The model hallucinated updating its language settings. `set_user_language` or `set_user_preference` did not exist in tools. `users.language` remained `'hinglish'`.

#### Sub-turn e2: Bollywood Movies Turn
- **Timestamp**: `2026-11-10 07:24:45.188555+00:00` (12:54:45 PM IST)
- **Trace ID**: `1226` (`tr_7a5f7319fc43`)
- **User Message**: `"like what the movies from bolywood currenlty in threater?"`
- **Reply Path**: `tool_calling_v2`
- **Tools Called & Args**: 
  - `get_movie_showtimes`: `{"city": "Jodhpur"}`
- **Tool Results**: 
  - List of movies from BookMyShow.
- **Provider / Model**: `Gemini` / `gemini-3.1-flash-lite-preview`
- **LLM Calls**: `2`
- **Reply Source**: **LLM Generated Text**:
  `"Bhai, Jodhpur mein abhi ye Bollywood movies theatres mein chal rahi hain: ... Inmein se koi pasand aaye toh batao, main showtimes check kar sakta hoon!"`
- **Root Cause**: Because `db.get_user_language(chat_id)` returned `'hinglish'`, `agent/prompt.py` injected:
  `"Language Mandate: Respond in friendly Roman Hinglish (Hindi/English mix in Latin script)."`
  The prompt's Hinglish mandate overrode the previous conversational turns, forcing the LLM back to Hinglish.

---

### Turn f: "what about the reminder u set" & Origin of the Two 5 PM Rows

#### Turn f1: Status Question Erroneously Calling Write Tool
- **Timestamp**: `2026-11-10 07:47:50.299625+00:00` (1:17:50 PM IST)
- **Trace ID**: `1231` (`tr_015cd97c8afb`)
- **User Message**: `"what about the reminder u set"`
- **Reply Path**: `tool_calling_v2`
- **Tools Called & Args**: 
  - `update_reminder`: `{"query": "Meeting", "new_time": "17:00", "duration_minutes": 30}`
- **Tool Results**: 
  - `{"action": "update_reminder", "status": "conflict_detected", "message": "Time Conflict: 05:00 PM par already 'Meeting' (17:00 - 18:00) scheduled hai. Kya aap confirm karna chahenge ya iska time change karna hai?", "conflicts": ["'Meeting' (17:00 - 18:00)"]}`
- **Provider / Model**: `Gemini` / `gemini-3.1-flash-lite-preview`
- **LLM Calls**: `1`
- **Reply Source**: **String returned by tool** (`tool_result["message"]`) via fast-path line 587.
- **Root Cause**: The user asked a read-only status inquiry (`"what about the reminder u set"`), but the LLM hallucinated and invoked a write tool (`update_reminder`). The tool detected a conflict with existing 5 PM rows and returned a hardcoded Hinglish string.

#### Next Turn: Unprompted Deletion Offer & Bulk Delete
- **Trace ID**: `1232` (User: `"i didnt the said to set up meeting"`)
  - Assistant: `"I apologize for the confusion. I see that there are multiple entries for 5:00 PM in your schedule: a 'Meeting' and a 'Meeting Reminder.' Since you did not ask for these, would you like me to delete them for you?"`
- **Trace ID**: `1233` (User simply said: `"hi"`)
  - Tools called: `delete_reminder`, `delete_reminder` for all 6 meeting rows without explicit confirmation!

#### Origin of the Two 5 PM Rows:
Who created them? Exact database trace evidence:
1. **Row 1 (`title="Meeting"`, 17:00 - 18:00)**:
   - **Trace ID**: `1206` (`tr_a35f18ed4b66`)
   - **Created At**: `2026-11-10 02:16:48.004805+00:00` (07:46:48 AM IST)
   - **User Message**: `"bhy aaj reminder lgaa dena 5pm ka meeting h 1hour ke"`
   - **Tool Called**: `create_reminder`
   - **Arguments**: `{"title": "Meeting", "due_date": "2026-11-10", "due_time": "17:00", "duration_minutes": 60}`
2. **Row 2 (`title="Meeting Reminder"`, 17:00 - 17:30)**:
   - **Trace ID**: `1210` (`tr_0e239efed057`)
   - **Created At**: `2026-11-10 02:20:50.502370+00:00` (07:50:50 AM IST)
   - **User Message**: `"bhy new meetinf set nhi krni h uska he bol rha hu m kr iss meeting jo phele s 5pm pr h uska reminder tumn idhr lgya h uska calnder or reminder lga do"`
   - **Tool Called**: `create_reminder`
   - **Arguments**: `{"title": "Meeting Reminder", "due_date": "2026-11-10", "due_time": "17:00", "duration_minutes": 30}`
   - **Why created**: User meant "sync the existing 5 PM meeting to calendar", but bot called `create_reminder` with title `"Meeting Reminder"`, creating a second row at 17:00!

---

## Key Architectural Findings for Phase 7e Implementation

1. **Tool Prose Pollution (Step 1)**: `create_reminder`, `update_reminder`, `delete_reminder`, and confirmation handlers return hardcoded strings (`"Done bhai!"`, `"Time Conflict:"`, `"Action cancel kar diya"`). The loop fast-path (`agent/loop.py:587`) short-circuited the LLM synthesis, returning tool prose directly to the user.
2. **Preference Storage (Step 2)**: Preference was stored as an unstructured fact (`save_note_or_fact`), leaving DB `users.language` untouched. A dedicated `set_user_preference` tool is required.
3. **Recurrence Architecture (Step 3)**: Recurrence was not in the deployed DB schema; models could not pass structured recurrence or RRULE.
4. **Time-of-Day Validation (Step 4)**: Server-side validation with configurable tolerance is required to reject past times instead of silently writing.
5. **Tool Orthogonality (Step 5)**: Split `create_calendar_event` (no Meet) from `create_meeting` (Meet).
6. **Status Question Guard with `request_quote` (Step 6)**: Write tools must require `request_quote` matching user text to prevent status questions from executing writes.
7. **Destructive Action Guards (Step 7)**: Bulk/unprompted deletions must be prohibited; explicit target ID required.

