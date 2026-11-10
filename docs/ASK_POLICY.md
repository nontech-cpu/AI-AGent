# PeculiarAI Clarification, Missing-Information & No-Invention Policy (Step 9)

**Standard**: Golden Standard Architecture & Universal Multi-Tool Risk Engine  
**Status**: Active Enforcement  
**Applicability**: All tools, handlers, and synthesis pipelines (Zero Scenario-Specific Code)

---

## 1. Core Operating Principles

1. **Ask Only If a Wrong Action Costs More Than the Interruption**:
   - Do not interrupt the user with redundant questions if the action is read-only, safe, or fully inferable from established context.
   - For destructive or external irreversible actions (e.g. deleting items, sending emails to external recipients, clearing data), precision and confirmation take absolute priority over uninterrupted execution.

2. **Look Before Asking**:
   - When a required parameter appears to be missing from the immediate user prompt, the system **must** first consult candidate sources declared in the tool schema (`can_be_found_in`: `calendar`, `reminders`, `memory`, `contacts`, `event_memory`, `email`, `notion`, `github`).
   - If exactly one unambiguous match exists in the user's records, use it directly and cite the source.
   - If multiple competing candidates exist, ask the user to clarify between the specific candidate options.
   - If no candidate exists, ask ONE combined question covering all missing essential parameters.

3. **Ask ONE Combined Question**:
   - Never interrogate the user sequentially across multiple turns for missing fields.
   - Combine all missing essential parameters into a single, concise clarifying question.
   - Deliver the question in the user's saved/preferred language (`db.get_user_language`).

4. **Never Invent a Value (Zero Hallucination)**:
   - Under no circumstances may the model or runtime generate a synthetic default value (e.g. inventing `09:00 AM` when no time was stated, or picking a random attendee/email).
   - If essential information is missing and unfindable in context, execution must halt safely with `{status: "needs_info", missing: [...]}`.

5. **Suggestions Come Strictly from User History**:
   - Suggestions offered in clarifying prompts must be derived from verified user history (e.g. past meeting times, usual workout hours, saved contacts).
   - With no verified history, no speculative suggestion may be appended.

---

## 2. Universal Tool Risk Classification & Policy Matrix

| Tool Category | Risk Level | Execution Policy | Confirmation Requirement |
|---|---|---|---|
| **Read Tools** (`list_reminders`, `get_schedule`, `search_memory`, `search_web`, `get_weather`, etc.) | `read` | Execute immediately. Never ask unless an essential query parameter is missing and completely unfindable in context. | None. Pure reads and conversational inquiries are never blocked. |
| **Reversible Write Tools** (`create_reminder`, `update_reminder`, `manage_smart_list`, `save_note_or_fact`) | `reversible_write` | Execute or stage when all required fields are validated. State any documented schema defaults used in the final reply. | Soft confirmation or post-execution status display with immediate undo/restore option (`restore_reminder`). |
| **External / Irreversible Tools** (`create_meeting`, `send_email`, `delete_reminder`, `delete_meeting`, `upload_file`, `request_master_privacy_wipe`) | `external_or_irreversible` | **Mandatory Two-Phase Confirmation Gate**. Pre-execution validator stages pending action in database. Side effect is executed **only** after explicit user confirmation (`confirm` or button callback). | Explicit two-phase confirmation showing full payload (title, date with weekday, time, duration, recurrence, attendees, target IDs). |

---

## 3. Evidence & Provenance Verification

1. **Evidence Object**:
   - Every write tool call accepts structured parameter provenance:
     ```json
     "evidence": {
       "<param_name>": {
         "source": "user_message" | "conversation" | "memory" | "tool_result" | "schema_default",
         "quote": "<exact substring>"
       }
     }
     ```
2. **Deterministic String Containment**:
   - Server-side dispatcher verifies quotes against normalized (Unicode/whitespace/case) recent user messages and turn memory.
   - Any parameter lacking verified provenance is treated as ungrounded and missing, triggering a structured `needs_info` response with zero DB/API mutations.

---

## 4. Prompt Directives (Zero Scenario Words)

Injected into system prompt:
> *"Ask only if a wrong action costs more than the interruption. Ask ONE combined question covering every missing essential, in the user's saved language, with a suggested value when a sensible one exists. Look before asking. Never ask for what is already known or findable. Never invent a value."*
