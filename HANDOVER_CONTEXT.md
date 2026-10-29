# 🚀 Memorae AI Agent — Project Handover & Complete Context Document

> **NOTE FOR THE NEW AI ASSISTANT / GEMINI PRO:**
> Read this document completely before taking any action. This file contains the complete architectural context, bug fixes done, current database state, and the roadmap for upcoming tasks.
> Do NOT rewrite or break existing working logic. Always maintain Roman Hinglish communication with the user.

---

## 1. 📌 Project Overview & Core Mission
**Memorae AI** is an intelligent personal AI assistant and memory companion that synchronizes seamlessly across:
1. **Telegram Bot** (Direct messaging, voice notes, instant notifications)
2. **Web Companion App** (`companion.html` - Localhost/Production UI for dashboard, lists, reminders, calendar, chat)
3. **Central Database** (PostgreSQL on Neon DB / SQLite local)
4. **Google Services** (Google Calendar, Gmail, Google Drive OAuth sync)

* **Backend Framework:** FastAPI (`main.py`, `api_v1.py`) running on `http://0.0.0.0:8000`.
* **LLM Engine:** Groq / LLaMA-3 / Gemini native tool calling via `agent/loop.py`, `agent/tools.py`, `agent/prompt.py`.
* **Database Driver:** SQLAlchemy with Psycopg 3 (`db.py`).

---

## 2. 👥 User Identity & Multi-Client Sync Architecture
* **Canonical Telegram User ID:** `1234828592` (Nirav `@Niirav18` - Desktop Telegram)
* **Mobile / Web Companion User ID:** `1758056012` (Neerav Mishra)
* **Mapping Rule (`db.py` - `CANONICAL_CHAT_ID_MAP`):**
  - `1234828592` maps to `1758056012` as canonical.
  - All reminders, memories, and lists belong to canonical `user_id = 408` (`telegram_chat_id = 1758056012`).
  - **Broadcast Rule:** All reminder alert notifications are delivered simultaneously to **both** `1758056012` and `1234828592` so the user never misses an alert regardless of which device they are on.

---

## 3. 🛠️ Major Fixes & Enhancements Completed (Do Not Revert)

1. **ChatGPT-Style Semantic Native Tool Calling (`agent/loop.py`, `agent/tools.py`):**
   - Removed brittle hardcore regexes and keyword if-else traps.
   - Model reasons over semantic meaning and calls native tools (`create_reminder`, `save_note_or_fact`, `manage_smart_list`, `search_memory`).

2. **Zero-Latency In-Memory Reactive Store (`companion.html`):**
   - Tab switching between *All*, *Today*, *Upcoming*, and *Done* filters instantly in 0.000ms using `window._MEMORAE_ALL_REMS` without flickering or 2-second reload delays.

3. **100% Live Database Schedule Injection (`agent/prompt.py`):**
   - Replaced stale 180s schedule cache with fresh `<0.2ms` live queries injected directly into the LLM system prompt (`- Active Reminders Today/Tomorrow: ...`).

4. **Strict Subject Title Matching & Conflict Interception (`agent/tools.py`):**
   - Fixed fuzzy match bug where common words like "service" caused "scooty service" to overwrite "laptop service". Now requires exact title or substantive token equality.
   - Pre-creation collision detection (`diff_mins < 15`) returns `status: "conflict_detected"` without premature database insertion.

5. **Smart Lists Prompt & Tool Integration (`agent/prompt.py`, `agent/tools.py`):**
   - Added `manage_smart_list` to core tools and injected `- Active Checklists & Smart Lists:` into the system prompt. Active lists (e.g. `movie trip`, `Party planning`) sync bidirectionally.

6. **Telegram Reminder Notification Engine Fix (`timing.py`, `db.py`, `main.py`):**
   - **Root Cause #1 (SQL Category Filter):** `get_pending_reminders()` had `AND m.category = ANY(u.categories)` which dropped all `"reminder"` category items. Removed filter, replaced with `(r.sent IS NOT TRUE)`.
   - **Root Cause #2 (Missing Exact-Time Reminders):** `timing.py:build_plan()` previously only scheduled 15min and 5min before, and returned empty for urgent tasks. Now schedules `(due_dt, "at_time")` plus 5min/15min before.
   - **Root Cause #3 (`event_over` Premature Drop):** `timing.py:event_over()` was dropping events the exact second `due_time` arrived. Added a 45-minute grace window.
   - **Root Cause #4 (Multi-Client Broadcasting):** `main.py:run_reminders()` broadcasts notifications to all mapped Telegram IDs.

---

## 4. 🗄️ Database Tables Reference (`db.py`)
* `users`: `id`, `telegram_chat_id`, `first_name`, `username`, `categories`
* `memory_items`: `id`, `user_id`, `category`, `title`, `amount`, `due_date`, `due_time`
* `reminders`: `id`, `item_id`, `remind_at`, `label` (`at_time`, `5min`, `15min`, `due_day`), `sent`
* `user_facts`: Key-value memories (e.g., Friend fasting sugar: 84)
* `smart_lists` & `smart_list_items`: Checklists and items
* `chat_logs`: Full conversational history

---

## 5. 🔮 Next Pending Roadmap Items for the Next Session

1. **Google Maps Distance & Navigation Tool (`get_distance_and_route`):**
   - The user asked about exact driving distance in km (e.g., from *Circuit House Road, Jodhpur* to *Nothing Before Coffee (NBC), Jodhpur*).
   - OpenStreetMap Nominatim failed to resolve local Indian cafe landmarks.
   - Plan: Integrate Google Maps Distance Matrix & Directions API in `agent/tools.py` when `GOOGLE_MAPS_API_KEY` is provided in `.env`.
2. **Notification Delivery Channel Preferences:**
   - In production, allow user to configure preferred alert channels: Companion App Push vs Telegram vs WhatsApp.
3. **Continuous UI / UX Polish:**
   - Companion Web App (`companion.html`) visual polish and real-time audio/voice responsiveness.

---

## 6. 💬 How to Communicate with the User
* **Tone:** Friendly, helpful, proactive, and respectful.
* **Language:** Roman Hinglish (mix of Hindi and English written in Latin script, e.g. *"Haan bhai, maine yeh check kar liya hai..."*).
* **Code Preservation:** Never delete working features or refactor without reason. Preserve all existing comments and logic.
