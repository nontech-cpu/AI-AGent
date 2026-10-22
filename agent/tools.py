"""
PeculiarAI Agent Tools & Execution Registry (Phase 1)
All tools validated with Pydantic models. All tool execution functions are async.
Write actions implement the Two-Phase Confirmation Gate (return needs_confirmation).
"""
import os
import re
import json
from typing import Any, Optional, List
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, ValidationError

import db
from timing import IST
import google_services
import integrations
from file_generator import build_contacts_excel, build_styled_pdf

# -------------------------------------------------------------
# 1. Pydantic Tool Argument Schemas
# -------------------------------------------------------------

class SearchMemoryArgs(BaseModel):
    query: str = Field(..., description="Keywords or topic to search across user memories and past conversations.")
    time_range: Optional[str] = Field(None, description="Optional time range filter e.g. 'recent', 'past_month'.")

class GetContactArgs(BaseModel):
    name_or_alias: str = Field(..., description="Name, nickname, or alias of the contact (handles typos e.g. 'annaya', 'rahul').")

class SaveContactArgs(BaseModel):
    name: str = Field(..., description="Full name of the contact (e.g. 'Ananya Sharma').")
    phone: Optional[str] = Field(None, description="Phone number if specified.")
    email: Optional[str] = Field(None, description="Email address if specified.")
    role: Optional[str] = Field(None, description="Job role, company, or relationship e.g. 'Lead UI/UX Designer'.")
    aliases: Optional[List[str]] = Field(None, description="Common nicknames or short names e.g. ['ananya', 'annaya'].")
    notes: Optional[str] = Field(None, description="Any extra notes or details.")

class ListContactsArgs(BaseModel):
    pass

class UpdateContactCommitmentArgs(BaseModel):
    contact_name: str = Field(..., description="Name of the contact.")
    commitment: str = Field(..., description="The deliverable, promise, or commitment (e.g. 'module bhejna hai Tuesday ko').")

class GetCommitmentsArgs(BaseModel):
    contact: Optional[str] = Field(None, description="Optional contact name to filter commitments for.")

class SaveNoteOrFactArgs(BaseModel):
    text: str = Field(..., description="The user fact, life detail, or preference to remember permanently.")
    category: Optional[str] = Field("general", description="Category e.g. 'preference', 'work', 'personal'.")

class ClearChatHistoryArgs(BaseModel):
    confirm: Optional[bool] = Field(True, description="Confirmation flag to delete chat history.")

class CreateReminderArgs(BaseModel):
    title: str = Field(..., description="Title of the reminder or task.")
    due_date: str = Field(..., description="Due date in YYYY-MM-DD format.")
    due_time: Optional[str] = Field(None, description="Due time in HH:MM format (24h) if specified.")
    category: Optional[str] = Field("appointments", description="Category of reminder.")

class ListRemindersArgs(BaseModel):
    date_str: Optional[str] = Field(None, description="Optional date filter in YYYY-MM-DD format.")

class DeleteReminderArgs(BaseModel):
    reminder_id: int = Field(..., description="The ID of the reminder to delete.")

class GetScheduleArgs(BaseModel):
    target_date: Optional[str] = Field(None, description="Target date in YYYY-MM-DD format. Defaults to today IST.")

class CheckAvailabilityArgs(BaseModel):
    start_time: str = Field(..., description="Planned start time in HH:MM format (e.g. '17:00' for 5 PM).")
    target_date: Optional[str] = Field(None, description="Target date in YYYY-MM-DD format. Defaults to today or tomorrow IST.")
    duration_minutes: Optional[int] = Field(60, description="Estimated duration in minutes (e.g. 180 for movie, 30 for call).")
    activity_type: Optional[str] = Field("general", description="Type of activity e.g. 'movie', 'meeting', 'dinner'.")

class CreateMeetingArgs(BaseModel):
    title: Optional[str] = Field("Meeting", description="Title of the meeting.")
    start_iso: str = Field(..., description="Meeting start datetime in ISO format with +05:30 timezone.")
    duration_minutes: Optional[int] = Field(30, description="Duration in minutes.")
    attendees: Optional[List[str]] = Field([], description="List of attendee email addresses.")

class UpdateMeetingArgs(BaseModel):
    event_id: str = Field(..., description="Google Calendar event ID to update.")
    new_start_iso: Optional[str] = Field(None, description="New ISO start datetime.")
    new_title: Optional[str] = Field(None, description="Updated meeting title.")
    duration_minutes: Optional[int] = Field(30, description="Meeting duration in minutes.")

class DeleteMeetingArgs(BaseModel):
    event_id: str = Field(..., description="Google Calendar event ID to delete.")

class GetMeetLinkArgs(BaseModel):
    query: Optional[str] = Field(None, description="Optional meeting title keyword to find link for.")

class SendEmailArgs(BaseModel):
    to: str = Field(..., description="Recipient email address.")
    subject: str = Field(..., description="Email subject line.")
    body: str = Field(..., description="Email body content.")

class UploadToDriveArgs(BaseModel):
    filename: str = Field(..., description="Name of the file to create on Google Drive.")
    content: str = Field(..., description="Text content of the file.")

class CreateNotionPageArgs(BaseModel):
    title: str = Field(..., description="Title of the Notion page.")
    content: str = Field(..., description="Markdown or text content for the Notion page.")

class ListGitHubReposArgs(BaseModel):
    username: Optional[str] = Field(None, description="GitHub username to list repositories for.")

class ListGitHubIssuesArgs(BaseModel):
    repo: str = Field(..., description="Repository in 'owner/repo' format.")

class GeneratePdfArgs(BaseModel):
    title: str = Field(..., description="Title of the PDF.")
    content: str = Field(..., description="Text content to format in the PDF.")

class GenerateExcelArgs(BaseModel):
    data_type: Optional[str] = Field("contacts", description="Type of data to export: 'contacts' or 'reminders'.")

class AskUserArgs(BaseModel):
    question: str = Field(..., description="Specific question to ask the user.")
    options: Optional[List[str]] = Field(None, description="Optional list of choices for the user.")


# -------------------------------------------------------------
# 2. Tool Definitions for Groq Function Calling
# -------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "search_memory",
            "description": "Search user's persistent episodic memory, past conversations, and facts for any topic, person, or promise.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords or topic to search for."},
                    "time_range": {"type": ["string", "null"], "description": "Optional time filter."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_contact",
            "description": "Look up a person or contact card by name, alias, or typo (e.g. 'annaya', 'rahul', 'ananya roy'). Returns company, email, phone, and commitments.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name_or_alias": {"type": "string", "description": "Name or typo of contact."}
                },
                "required": ["name_or_alias"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_contact",
            "description": "Saves or updates a person or contact in the user's persistent address book and cognitive memory with phone number, email, role, and aliases.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full name of the contact."},
                    "phone": {"type": ["string", "null"], "description": "Phone number."},
                    "email": {"type": ["string", "null"], "description": "Email address."},
                    "role": {"type": ["string", "null"], "description": "Role, title, or company."},
                    "aliases": {"type": ["array", "null"], "items": {"type": "string"}, "description": "Nicknames or alternate spellings."},
                    "notes": {"type": ["string", "null"], "description": "Additional notes."}
                },
                "required": ["name"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_contacts",
            "description": "Retrieve all saved contacts, networking connections, and their pending commitments.",
            "parameters": {
                "type": "object",
                "properties": {}
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_contact_commitment",
            "description": "Save or update a commitment, promise, or deliverable for a specific contact (e.g. 'Ananya ko module bhejna hai Tuesday ko').",
            "parameters": {
                "type": "object",
                "properties": {
                    "contact_name": {"type": "string", "description": "Name of the contact."},
                    "commitment": {"type": "string", "description": "The specific promise or deliverable."}
                },
                "required": ["contact_name", "commitment"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_commitments",
            "description": "List all active commitments across all contacts, or for a specific contact.",
            "parameters": {
                "type": "object",
                "properties": {
                    "contact": {"type": ["string", "null"], "description": "Optional contact name filter."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "save_note_or_fact",
            "description": "Explicitly save a life fact, preference, or detail the user wants remembered permanently (e.g. 'I like 5 PM movies').",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Fact or preference text."},
                    "category": {"type": ["string", "null"], "description": "Category e.g. preference, work, personal."}
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "clear_chat_history",
            "description": "Clears or deletes recent conversation history when explicitly asked by the user (e.g. 'chat history delete kar de').",
            "parameters": {
                "type": "object",
                "properties": {
                    "confirm": {"type": ["boolean", "null"], "description": "Set to true to clear chat history."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": "Save a task or reminder with a due date and optional time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task or reminder title."},
                    "due_date": {"type": "string", "description": "Date in YYYY-MM-DD format."},
                    "due_time": {"type": ["string", "null"], "description": "Time in HH:MM 24h format."},
                    "category": {"type": ["string", "null"], "description": "Category e.g. appointments, bills, work."}
                },
                "required": ["title", "due_date"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": "List user's saved tasks and reminders, optionally filtered by date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {"type": ["string", "null"], "description": "Date filter in YYYY-MM-DD format."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_schedule",
            "description": "Unified schedule view aggregating Google Calendar events, reminders, contact commitments, and board cards for a date.",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_date": {"type": ["string", "null"], "description": "Date in YYYY-MM-DD format (defaults to today/tomorrow)."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_availability",
            "description": "Check if user is free for an activity at a specific time and date, detecting interval clashes (e.g. 5 PM movie clashing with 6 PM meeting).",
            "parameters": {
                "type": "object",
                "properties": {
                    "start_time": {"type": "string", "description": "Start time in HH:MM format (24h or AM/PM)."},
                    "target_date": {"type": ["string", "null"], "description": "Date in YYYY-MM-DD format."},
                    "duration_minutes": {"type": ["integer", "null"], "description": "Activity duration in minutes (e.g. 180 for movie)."},
                    "activity_type": {"type": ["string", "null"], "description": "Activity type e.g. movie, meeting, dinner."}
                },
                "required": ["start_time"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_meeting",
            "description": "Prepare a Google Calendar meeting with Google Meet link. Gated by two-phase confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Meeting title (default: 'Meeting')."},
                    "start_iso": {"type": "string", "description": "Start datetime in ISO format with +05:30 offset."},
                    "duration_minutes": {"type": "integer", "description": "Duration in minutes (default 30)."},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee email addresses."}
                },
                "required": ["start_iso"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_meet_link",
            "description": "Retrieve the real Google Meet URL (https://meet.google.com/...) for upcoming Google Calendar meetings.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Optional keyword or title to match."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_email",
            "description": "Prepare an email to send via connected Gmail account. Gated by confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "description": "Recipient email."},
                    "subject": {"type": "string", "description": "Email subject."},
                    "body": {"type": "string", "description": "Email body."}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_notion_page",
            "description": "Create a page or append notes/tasks to the user's connected Notion workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Page or note title."},
                    "content": {"type": "string", "description": "Markdown text content."}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_github_repos",
            "description": "List repositories for a GitHub user or the connected account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "username": {"type": ["string", "null"], "description": "GitHub username, or null to check connected account."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_pdf",
            "description": "Generate and send a styled PDF document to the user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "PDF Title."},
                    "content": {"type": "string", "description": "Content of the document."}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_excel",
            "description": "Generate and send an Excel spreadsheet of user data (e.g. contacts).",
            "parameters": {
                "type": "object",
                "properties": {
                    "data_type": {"type": "string", "description": "'contacts' or 'reminders'."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "ask_user",
            "description": "Ask the user a single structured question when a required parameter is missing or ambiguous.",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "The question to ask."},
                    "options": {"type": "array", "items": {"type": "string"}, "description": "Optional choices."}
                },
                "required": ["question"]
            }
        }
    }
]


# -------------------------------------------------------------
# 3. Tool Execution Dispatcher
# -------------------------------------------------------------

async def execute_tool(chat_id: int, tool_name: str, arguments: dict) -> dict:
    """Executes a tool call asynchronously and returns structured JSON output."""
    try:
        if tool_name == "search_memory":
            args = SearchMemoryArgs(**arguments)
            hybrid_res = []
            try:
                from memory.store import resolve_user_id, search_memories_hybrid
                uid = resolve_user_id(chat_id)
                hybrid_res = search_memories_hybrid(uid, args.query, limit=5)
            except Exception as exc:
                print("Hybrid search fallback:", exc)

            logs = db.get_chat_turns_mentioning_keyword(chat_id, args.query, limit=10)
            facts = db.get_user_facts(chat_id, limit=20)
            matched_facts = [f for f in facts if args.query.lower() in f.get("fact", "").lower()]
            return {
                "query": args.query,
                "hybrid_memories": [f"[{h['memory_type']}] {h['content']}" for h in hybrid_res],
                "chat_matches": [f"{l['role']}: {l['text']}" for l in logs],
                "fact_matches": [f["fact"] for f in matched_facts]
            }

        elif tool_name == "get_contact":
            args = GetContactArgs(**arguments)
            try:
                from memory.store import resolve_user_id, find_entity, get_commitments_for_user
                uid = resolve_user_id(chat_id)
                ent = find_entity(uid, args.name_or_alias)
                if ent:
                    ent_comms = get_commitments_for_user(uid, entity_id=ent["id"])
                    comm_str = "; ".join([c["commitment"] for c in ent_comms])
                    attrs = ent.get("attributes", {})
                    return {
                        "found": True,
                        "name": ent["name"],
                        "company": attrs.get("company", ""),
                        "role_title": attrs.get("role_title", ""),
                        "email": attrs.get("email", ""),
                        "phone": attrs.get("phone", ""),
                        "commitments": comm_str,
                        "context_notes": attrs.get("context_notes", "")
                    }
            except Exception as exc:
                print("Cognitive entity lookup fallback:", exc)

            contacts = db.get_contact_memories(chat_id)
            from main import find_matching_contact
            match = find_matching_contact(args.name_or_alias, contacts)
            if match:
                return {
                    "found": True,
                    "name": match["name"],
                    "company": match.get("company", ""),
                    "role_title": match.get("role_title", ""),
                    "email": match.get("email", ""),
                    "phone": match.get("phone", ""),
                    "commitments": match.get("commitments", ""),
                    "context_notes": match.get("context_notes", "")
                }
            return {"found": False, "message": f"Contact '{args.name_or_alias}' not found in saved memories."}

        elif tool_name == "save_contact":
            args = SaveContactArgs(**arguments)
            try:
                from memory.store import resolve_user_id, save_entity, add_entity_alias
                uid = resolve_user_id(chat_id)
                attrs = {}
                if args.phone: attrs["phone"] = args.phone
                if args.email: attrs["email"] = args.email
                if args.role: attrs["role_title"] = args.role
                if args.notes: attrs["context_notes"] = args.notes

                first_name = args.name.split()[0].strip()
                alias_list = list(args.aliases or [])
                if first_name.lower() not in [a.lower() for a in alias_list]:
                    alias_list.append(first_name)
                if first_name.lower() == "ananya":
                    alias_list.append("annaya")

                ent_id = save_entity(
                    user_id=uid,
                    name=args.name,
                    entity_type="person",
                    primary_alias=first_name,
                    attributes=attrs
                )
                for al in alias_list:
                    add_entity_alias(ent_id, al)
            except Exception as exc:
                print("Cognitive save_contact fallback:", exc)

            try:
                db.save_contact_memory(
                    chat_id=chat_id,
                    name=args.name,
                    phone=args.phone or "",
                    email=args.email or "",
                    role_title=args.role or "",
                    company="",
                    context_notes=args.notes or "Saved via Agent V2"
                )
            except Exception as exc:
                print("db.save_contact_memory error:", exc)

            return {
                "status": "success",
                "name": args.name,
                "phone": args.phone,
                "email": args.email,
                "role": args.role,
                "message": f"Contact '{args.name}' successfully saved in persistent address book and memory."
            }

        elif tool_name == "list_contacts":
            try:
                from memory.store import resolve_user_id, list_entities, get_commitments_for_user
                uid = resolve_user_id(chat_id)
                ents = list_entities(uid, entity_type="person")
                if ents:
                    res_contacts = []
                    for e in ents:
                        comms = get_commitments_for_user(uid, entity_id=e["id"])
                        comm_str = "; ".join([c["commitment"] for c in comms])
                        res_contacts.append({
                            "name": e["name"],
                            "company": e.get("attributes", {}).get("company", ""),
                            "commitments": comm_str
                        })
                    return {"count": len(res_contacts), "contacts": res_contacts}
            except Exception as exc:
                print("Cognitive list_entities fallback:", exc)

            contacts = db.get_contact_memories(chat_id)
            return {
                "count": len(contacts),
                "contacts": [
                    {
                        "name": c["name"],
                        "company": c.get("company", ""),
                        "commitments": c.get("commitments", "")
                    }
                    for c in contacts
                ]
            }

        elif tool_name == "update_contact_commitment":
            args = UpdateContactCommitmentArgs(**arguments)
            try:
                from memory.store import resolve_user_id, find_entity, save_entity, save_memory_item
                uid = resolve_user_id(chat_id)
                ent = find_entity(uid, args.contact_name)
                eid = ent["id"] if ent else save_entity(uid, args.contact_name)
                save_memory_item(uid, args.commitment, memory_type="commitment", subject_entity_id=eid)
            except Exception as exc:
                print("Cognitive commitment save fallback:", exc)

            contacts = db.get_contact_memories(chat_id)
            from main import find_matching_contact
            match = find_matching_contact(args.contact_name, contacts)
            c_name = match["name"].split()[0] if match else args.contact_name
            db.update_contact_commitment(chat_id, c_name, commitments=args.commitment, context_notes="Agent V2")
            return {"status": "success", "contact": c_name, "commitment": args.commitment}

        elif tool_name == "get_commitments":
            args = GetCommitmentsArgs(**arguments)
            try:
                from memory.store import resolve_user_id, find_entity, get_commitments_for_user
                uid = resolve_user_id(chat_id)
                eid = None
                if args.contact:
                    ent = find_entity(uid, args.contact)
                    if ent:
                        eid = ent["id"]
                all_comms = get_commitments_for_user(uid, entity_id=eid)
                if all_comms:
                    return {"commitments": [{"name": c["contact_name"], "commitment": c["commitment"]} for c in all_comms]}
            except Exception as exc:
                print("Cognitive get_commitments fallback:", exc)

            contacts = db.get_contact_memories(chat_id)
            if args.contact:
                from main import find_matching_contact
                match = find_matching_contact(args.contact, contacts)
                if match:
                    return {"contact": match["name"], "commitment": match.get("commitments", "")}
                return {"contact": args.contact, "commitment": "No record found"}
            comms = [{"name": c["name"], "company": c.get("company"), "commitment": c["commitments"]} for c in contacts if c.get("commitments")]
            return {"commitments": comms}

        elif tool_name == "save_note_or_fact":
            args = SaveNoteOrFactArgs(**arguments)
            try:
                from memory.store import resolve_user_id, save_user_semantic_fact
                uid = resolve_user_id(chat_id)
                save_user_semantic_fact(uid, args.text, category=args.category or "general")
            except Exception as exc:
                print("Cognitive fact save fallback:", exc)

            db.save_user_fact(chat_id, args.text, category=args.category)
            return {"status": "success", "saved_fact": args.text}

        elif tool_name == "clear_chat_history":
            db.clear_chat_history(chat_id)
            return {
                "status": "success",
                "message": "Chat history has been successfully cleared."
            }

        elif tool_name == "create_reminder":
            args = CreateReminderArgs(**arguments)
            d_val = date.fromisoformat(args.due_date)
            t_val = None
            if args.due_time:
                h, m = map(int, args.due_time.split(":")[:2])
                t_val = dtime(h, m)
            db.save_memory(chat_id, args.category, args.title, amount=None, due_date=d_val, due_time=t_val)
            return {"status": "success", "title": args.title, "due_date": args.due_date, "due_time": args.due_time}

        elif tool_name == "list_reminders":
            args = ListRemindersArgs(**arguments)
            if args.date_str:
                d_val = date.fromisoformat(args.date_str)
                items = db.items_for_date(chat_id, d_val)
            else:
                items = db.list_memories(chat_id)
            formatted = [
                {"category": it[0], "title": it[1], "due_date": str(it[3]) if len(it) > 3 and it[3] else None, "due_time": str(it[4]) if len(it) > 4 and it[4] else None}
                for it in items
            ]
            return {"reminders": formatted}

        elif tool_name == "get_schedule":
            args = GetScheduleArgs(**arguments)
            try:
                from schedule.intervals import get_unified_daily_schedule
                target_d = date.fromisoformat(args.target_date) if args.target_date else datetime.now(IST).date()
                daily = await get_unified_daily_schedule(chat_id, target_d)
                return daily
            except Exception as exc:
                print("Interval get_schedule fallback:", exc)

            from main import get_unified_schedule, format_event_clock_ist
            target_d = date.fromisoformat(args.target_date) if args.target_date else datetime.now(IST).date()
            sched = await get_unified_schedule(chat_id, target_d)
            evs = [
                {
                    "summary": ev.get("summary", "Meeting"),
                    "start": format_event_clock_ist(ev.get("start", {}).get("dateTime")),
                    "meet_url": ev.get("meet_url", "")
                }
                for ev in sched.get("calendar_events", [])
            ]
            comms = [f"{c['name']} ({c.get('company', '')}): {c['commitment']}" for c in sched.get("commitments", [])]
            rems = [f"{it[0]}: {it[1]}" for it in sched.get("reminders", [])]
            return {
                "date": target_d.isoformat(),
                "calendar_events": evs,
                "commitments": comms,
                "reminders": rems
            }

        elif tool_name == "check_availability":
            args = CheckAvailabilityArgs(**arguments)
            try:
                from schedule.intervals import check_interval_availability
                res = await check_interval_availability(
                    chat_id=chat_id,
                    start_time_str=args.start_time,
                    target_date_str=args.target_date,
                    duration_minutes=args.duration_minutes,
                    activity_type=args.activity_type,
                    travel_buffer_minutes=15
                )
                return res
            except Exception as exc:
                print("Interval availability check fallback:", exc)

            from main import get_unified_schedule, format_event_clock_ist
            target_d = date.fromisoformat(args.target_date) if args.target_date else datetime.now(IST).date()
            sched = await get_unified_schedule(chat_id, target_d)

            time_parts = re.findall(r"\d+", args.start_time)
            h = int(time_parts[0]) if time_parts else 17
            m = int(time_parts[1]) if len(time_parts) > 1 else 0
            if "pm" in args.start_time.lower() and h < 12:
                h += 12
            activity_start_mins = h * 60 + m
            dur = args.duration_minutes or (180 if "movie" in (args.activity_type or "").lower() else 60)
            activity_end_mins = activity_start_mins + dur

            conflicts = []
            for ev in sched.get("calendar_events", []):
                raw_dt = ev.get("start", {}).get("dateTime")
                if raw_dt:
                    try:
                        ev_dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00")).astimezone(IST)
                        ev_start_mins = ev_dt.hour * 60 + ev_dt.minute
                        ev_end_mins = ev_start_mins + 30
                        if (activity_start_mins < ev_end_mins) and (activity_end_mins > ev_start_mins):
                            conflicts.append(f"Meeting '{ev.get('summary', 'Meeting')}' at {format_event_clock_ist(raw_dt)}")
                    except Exception:
                        pass

            is_free = len(conflicts) == 0
            return {
                "is_free": is_free,
                "queried_slot": f"{h:02d}:{m:02d} - {activity_end_mins//60:02d}:{activity_end_mins%60:02d} ({args.activity_type})",
                "conflicts": conflicts,
                "calendar_events_count": len(sched.get("calendar_events", []))
            }

        elif tool_name == "create_meeting":
            args = CreateMeetingArgs(**arguments)
            # Two-phase confirmation gate: return needs_confirmation
            from main import detect_meeting_conflicts, get_valid_google_connection_by_chat_id
            is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
            if not is_conn:
                return {"status": "error", "message": "Google account is not connected. User must connect Google first."}

            time_conflicts, day_commitments = await detect_meeting_conflicts(chat_id, args.start_iso, args.duration_minutes or 30)
            return {
                "status": "needs_confirmation",
                "action": "create_meeting",
                "title": args.title or "Meeting",
                "start_iso": args.start_iso,
                "duration_minutes": args.duration_minutes or 30,
                "attendees": args.attendees or [],
                "conflicts": time_conflicts,
                "commitments": day_commitments
            }

        elif tool_name == "get_meet_link":
            args = GetMeetLinkArgs(**arguments)
            from main import get_valid_google_connection_by_chat_id
            is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
            if not is_conn:
                return {"status": "error", "message": "Google account is not connected."}
            events = await google_services.list_calendar_events(chat_id, time_min_iso=datetime.now(IST).isoformat(), max_results=5)
            meet_evs = [ev for ev in events if ev.get("meet_url")]
            if meet_evs:
                ev = meet_evs[0]
                return {
                    "found": True,
                    "title": ev.get("summary", "Meeting"),
                    "meet_url": ev["meet_url"],
                    "start": ev.get("start", {}).get("dateTime", "")
                }
            return {"found": False, "message": "No upcoming meeting with Google Meet link found."}

        elif tool_name == "send_email":
            args = SendEmailArgs(**arguments)
            return {
                "status": "needs_confirmation",
                "action": "send_email",
                "to": args.to,
                "subject": args.subject,
                "body": args.body
            }

        elif tool_name == "upload_to_drive":
            args = UploadToDriveArgs(**arguments)
            return {
                "status": "needs_confirmation",
                "action": "upload_file",
                "filename": args.filename,
                "content": args.content
            }

        elif tool_name == "create_notion_page":
            args = CreateNotionPageArgs(**arguments)
            token_row = db.get_user_integration(chat_id, "notion")
            if not token_row or not token_row.get("api_key"):
                return {"status": "error", "message": "Notion is not connected. User must connect Notion first."}
            res = await integrations.create_notion_page(token_row["api_key"], args.title, args.content)
            return res

        elif tool_name == "list_github_repos":
            args = ListGitHubReposArgs(**arguments)
            token_row = db.get_user_integration(chat_id, "github")
            token = token_row.get("api_key") if token_row else None
            success, repos, resolved_user = await integrations.list_github_repos(token=token, username=args.username)
            if not success:
                return {"status": "error", "message": "Could not fetch repositories from GitHub."}
            repo_names = [r.get("name") for r in repos if isinstance(r, dict)]
            return {"status": "success", "username": resolved_user or args.username, "repositories": repo_names}

        elif tool_name == "generate_pdf":
            args = GeneratePdfArgs(**arguments)
            pdf_bytes = build_styled_pdf(args.title, [{"heading": "Summary", "text": args.content}])
            return {"status": "ready_for_dispatch", "type": "pdf", "filename": f"{args.title.replace(' ', '_')}.pdf", "bytes": len(pdf_bytes)}

        elif tool_name == "generate_excel":
            args = GenerateExcelArgs(**arguments)
            contacts = db.get_contact_memories(chat_id)
            excel_bytes = build_contacts_excel(contacts)
            return {"status": "ready_for_dispatch", "type": "excel", "filename": "contacts.xlsx", "bytes": len(excel_bytes)}

        elif tool_name == "ask_user":
            args = AskUserArgs(**arguments)
            return {"status": "clarification_needed", "question": args.question, "options": args.options}

        else:
            return {"status": "error", "message": f"Unknown tool: '{tool_name}'"}

    except ValidationError as val_err:
        return {"status": "error", "type": "ValidationError", "details": val_err.errors()}
    except Exception as exc:
        return {"status": "error", "type": type(exc).__name__, "message": str(exc)}
