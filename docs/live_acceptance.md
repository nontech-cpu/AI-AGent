# PeculiarAI Live Acceptance Test Scenarios (Telegram Production Validation)

This document provides human-executable and automated acceptance scenarios mapped directly to the live bugs captured in the user's Telegram screenshots.

---

## Scenario 1: Past-Time Daily Reminder Protection (Bug 7e-a)
- **Reported Bug**: At 12:32 PM IST, user texted: *"mere daily reminder set krdo joggina ka 10am ka"*. Agent created a reminder for today (10 Nov 2026 10:00 AM), which had already passed 2.5 hours prior.
- **Expected Golden Behavior**:
  1. The agent detects that 10:00 AM today is in the past.
  2. Because the recurrence is "daily", the agent schedules the first occurrence for tomorrow (11 Nov 2026 at 10:00 AM).
  3. Zero rows are created in `memory_items` or `reminders` for 10 Nov 10:00 AM.
- **Verification Command**:
  ```bash
  pytest tests/test_part_c_calendar_recurrence.py -k "test_c4_recurrence_delivery_and_sleep_catchup"
  ```
- **Live User Input**: `mere daily reminder set krdo joggina ka 10am ka` (at > 10:00 AM IST)
- **Expected Agent Reply**: Confirms reminder set for tomorrow 10:00 AM with recurrence 'daily'.

---

## Scenario 2: Calendar Reminder Sync vs. Video Meeting (Bug 7e-c / 7e-d)
- **Reported Bug**: User asked: *"joggin wala reminder calender pr b lga do"*. Agent asked: *"Please confirm — create this Google Calendar meeting with a Google Meet link? Title: Jogging"*.
- **Expected Golden Behavior**:
  1. Simple personal reminders synced to Google Calendar must never generate Google Meet video conference links.
  2. Confirmation prompt formatted deterministically via `core/i18n.py`:
     - Event Title
     - Date with Weekday
     - Time & Duration
     - Google Meet: None
- **Verification Command**:
  ```bash
  pytest tests/test_part_c_calendar_recurrence.py -k "test_c3_confirmation_required_fields"
  ```
- **Live User Input**: `joggin wala reminder calender pr b lga do`
- **Expected Agent Reply**: Presents structured event confirmation without any mention of Google Meet or conference links.

---

## Scenario 3: Language Preference Persistence (Bug 7e-e)
- **Reported Bug**: User asked: *"now from the conversation talk to me in english only"*. Agent agreed, answered weather in English, but switched back to Hinglish for Bollywood movie showtimes.
- **Expected Golden Behavior**:
  1. When user states a language preference, the agent calls `set_user_language(language='english')`.
  2. Preference is persisted in database (`users.language = 'en'`).
  3. Dynamic system prompt locks language instruction to English for all subsequent turns.
  4. Movie recommendations, weather, and reminders remain 100% in natural English.
- **Verification Command**:
  ```bash
  pytest tests/test_no_hardcoding.py -k "test_b2_language_mandate_respects_user_profile"
  ```
- **Live User Input**:
  - Turn 1: `now from the conversation talk to me in english only`
  - Turn 2: `like what the movies from bolywood currenlty in threater?`
- **Expected Agent Reply**: 100% English response listing movies with citations.

---

## Scenario 4: Trace 1233 Incident Reproduction (Zero Accidental Deletions)
- **Reported Bug**: Assistant asked if user wants to delete meetings; user replied with greeting *"hi"*; bot wiped reminders.
- **Expected Golden Behavior**:
  1. Model-supplied `confirmed=True` is rejected by `agent/loop.py` and `agent/tools.py`.
  2. Destructive execution requires pending confirmation with explicit matching action id.
  3. Replying with *"hi"* executes zero database mutations.
- **Verification Command**:
  ```bash
  pytest tests/test_adversarial_and_security.py -k "test_trace_1233_incident_reproduction"
  ```
- **Live User Input**: Send `hi` after an agent cancellation offer.
- **Expected Agent Reply**: Friendly greeting; zero items deleted.
