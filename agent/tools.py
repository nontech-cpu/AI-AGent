"""
PeculiarAI Agent Tools & Execution Registry (Phase 1)
All tools validated with Pydantic models. All tool execution functions are async.
Write actions implement the Two-Phase Confirmation Gate (return needs_confirmation).
"""
import os
import json
import logging
from typing import Any, Optional, List, Dict
from datetime import datetime, date, time as dtime, timedelta
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, ValidationError

logger = logging.getLogger("peculiar_tools")

import db
from timing import IST
import google_services
import integrations
from file_generator import build_contacts_excel, build_styled_pdf

async def _resolve_user_timezone(chat_id: int):
    """Dynamically resolves the user's primary calendar timezone or defaults gracefully to IST."""
    try:
        user_tz_str = await google_services.get_user_calendar_timezone(chat_id)
        return ZoneInfo(user_tz_str), user_tz_str
    except Exception:
        return IST, "Asia/Kolkata"

def _extract_time_from_text(text: str) -> Optional[dtime]:
    """Safely parses 12h/24h time strings from text without brittle regexes."""
    if not text:
        return None
    tokens = text.lower().replace(".", ":").split()
    is_pm_hint = any(w in tokens for w in ("pm", "shaam", "sham", "raat", "night", "dophar", "evening", "afternoon"))
    is_am_hint = any(w in tokens for w in ("am", "subah", "subha", "morning"))

    for tok in tokens:
        clean_tok = tok.strip(" ,.!?")
        if ":" in clean_tok:
            parts = clean_tok.split(":")
            h_part = "".join(c for c in parts[0] if c.isdigit())
            m_part = "".join(c for c in parts[1][:2] if c.isdigit())
            if h_part:
                h = int(h_part)
                m = int(m_part) if m_part else 0
                if "pm" in clean_tok or (is_pm_hint and h < 12):
                    if h != 12:
                        h += 12
                elif "am" in clean_tok or (is_am_hint and h == 12):
                    h = 0
                if 0 <= h <= 23 and 0 <= m <= 59:
                    return dtime(h, m)

    for idx, tok in enumerate(tokens):
        clean_tok = tok.strip(" ,.!?")
        digits = "".join(c for c in clean_tok if c.isdigit())
        if digits and digits.isdigit():
            val = int(digits)
            if 1 <= val <= 23:
                next_tok = tokens[idx + 1] if idx + 1 < len(tokens) else ""
                has_pm = "pm" in clean_tok or (next_tok in ("pm", "baje", "bje") and is_pm_hint)
                has_am = "am" in clean_tok or (next_tok in ("am",)) or (next_tok in ("baje", "bje") and is_am_hint)
                has_baje = "baje" in clean_tok or "bje" in clean_tok or next_tok in ("baje", "bje")

                if has_pm or (has_baje and is_pm_hint):
                    if val < 12:
                        val += 12
                elif has_am:
                    if val == 12:
                        val = 0
                elif has_baje and 1 <= val <= 7:
                    val += 12

                if 0 <= val <= 23:
                    return dtime(val, 0)
    return None

def _safe_int(val: Any, default: int = 30) -> int:
    """Safely converts string/float/int durations into integer minutes without regex."""
    if val is None:
        return default
    if isinstance(val, (int, float)):
        return int(val)
    s = str(val).strip().lower()
    if not s:
        return default
    if "hour" in s or "hr" in s:
        part = s.split("hour")[0].split("hr")[0].strip()
        num_str = "".join(ch for ch in part if ch.isdigit() or ch == ".")
        if num_str:
            try:
                return int(float(num_str) * 60)
            except Exception:
                pass
    digits = "".join(ch for ch in s if ch.isdigit())
    if digits:
        try:
            return int(digits)
        except Exception:
            return default
    return default

# -------------------------------------------------------------
# 1. Pydantic Tool Argument Schemas
# -------------------------------------------------------------


def _get_row_val(r: Any, key: str, idx: Optional[int] = None, default: Any = None) -> Any:
    if r is None:
        return default
    if isinstance(r, dict):
        return r.get(key, default)
    if hasattr(r, "_mapping"):
        try:
            val = r._mapping.get(key)
            return val if val is not None else default
        except Exception:
            pass
    if idx is not None:
        try:
            if len(r) > idx and r[idx] is not None:
                return r[idx]
        except Exception:
            pass
    try:
        val = getattr(r, key, default)
        return val if val is not None else default
    except Exception:
        return default

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

class RequestMasterPrivacyWipeArgs(BaseModel):
    confirm: Optional[bool] = Field(True, description="Confirmation flag to initiate master privacy wipe and permanent memory deletion.")

class DeleteNoteOrFactArgs(BaseModel):
    query: str = Field(..., description="Keywords or topic of the note, fact, booking, or ticket to delete from memory e.g. 'ticket', 'flight', 'hotel'.")

class SetUserTimezoneArgs(BaseModel):
    timezone: str = Field(..., description="Valid IANA timezone identifier e.g. 'America/New_York', 'Europe/London', 'Asia/Kolkata'.")

class UpdateGitHubFileArgs(BaseModel):
    repo: str = Field(..., description="Repository in 'owner/repo' format or repo name e.g. 'ml-project' or 'username/ml-project'.")
    path: Optional[str] = Field("README.md", description="File path within the repository e.g. 'README.md'.")
    content: str = Field(..., description="New or updated content for the file.")
    commit_message: Optional[str] = Field("Update file via PeculiarAI", description="Git commit message.")

class ManageSmartListArgs(BaseModel):
    action: str = Field(..., description="Action to perform: 'create', 'add_items', 'get', 'list_all', 'delete'.")
    list_name: Optional[str] = Field(None, description="Name of the checklist/smart list e.g. 'Groceries', 'Packing', 'Work Deliverables'.")
    items: Optional[List[str]] = Field(None, description="List of item titles to add or include.")
    category: Optional[str] = Field("general", description="Category of list e.g. 'groceries', 'travel', 'work', 'personal'.")

class CreatePackingListArgs(BaseModel):
    trip_title: str = Field(..., description="Title of the trip or packing list e.g. 'Goa Trip'.")
    items: List[str] = Field(..., description="List of items to pack e.g. ['Passport', 'Aadhaar Card', 'Photo', 'Charger'].")
    trip_date: Optional[str] = Field(None, description="Date of the trip in YYYY-MM-DD format if known.")

class CreateReminderArgs(BaseModel):
    title: str = Field(..., description="Title of the reminder, meeting, or task.")
    due_date: Optional[str] = Field(None, description="Due date in YYYY-MM-DD format. If omitted, defaults to today's date.")
    due_time: Optional[str] = Field(
        None,
        description=(
            "Due time in 24-hour HH:MM format (e.g. '23:00' for 11pm, '22:30' for 10:30pm, '15:00' for 3pm, '10:30' for 10:30am). "
            "MANDATORY: When the user mentions any specific time of day in natural language "
            "(e.g. '11pm' -> '23:00', '10:30pm' -> '22:30', '3 baje' -> '15:00', 'at 11' -> '23:00' or '11:00', 'dinner at 9' -> '21:00'), "
            "you MUST extract and convert it into 24-hour HH:MM format. NEVER leave null when the user specifies a time."
        )
    )
    duration_minutes: Optional[Any] = Field(
        None,
        description=(
            "Duration in minutes if the user specifies how long the meeting, event, or call lasts "
            "(e.g. '1 hour' -> 60, '1 hour ki meeting' -> 60, '30 min' -> 30, '2 hours' -> 120, '45 min' -> 45)."
        )
    )
    category: Optional[str] = Field("appointments", description="Category of reminder.")
    recurrence: Optional[str] = Field("none", description="Recurrence pattern: 'daily' (for roj, daily, har din, jbtk m na khu), 'weekly', or 'none'.")
    force: Optional[bool] = Field(False, description="Set to true if user explicitly confirmed or insists to schedule despite a time conflict or tight schedule.")

class UpdateReminderArgs(BaseModel):
    title: Optional[str] = Field(None, description="Title or keyword of the reminder, meeting, or task to update.")
    reminder_id: Optional[int] = Field(None, description="ID of the reminder if known.")
    due_date: Optional[str] = Field(None, description="Updated date in YYYY-MM-DD format if date is being changed.")
    due_time: Optional[str] = Field(None, description="Updated time in 24-hour HH:MM format if time is being changed.")
    duration_minutes: Optional[Any] = Field(None, description="Updated duration in minutes (e.g. 60 for 1 hour, 30 for 30 mins).")
    force: Optional[bool] = Field(False, description="Set to true if user insists on updating despite a time conflict.")

class ListRemindersArgs(BaseModel):
    date_str: Optional[str] = Field(None, description="Optional date filter in YYYY-MM-DD format.")

class DeleteReminderArgs(BaseModel):
    title: Optional[str] = Field(None, description="Title or keyword of the reminder or meeting to delete.")
    reminder_id: Optional[int] = Field(None, description="The ID of the reminder to delete if known.")

class GetScheduleArgs(BaseModel):
    target_date: Optional[str] = Field(None, description="Target date in YYYY-MM-DD format. Defaults to today IST.")

class CheckAvailabilityArgs(BaseModel):
    start_time: str = Field(..., description="Planned start time in HH:MM format (e.g. '17:00' for 5 PM).")
    target_date: Optional[str] = Field(None, description="Target date in YYYY-MM-DD format. Defaults to today or tomorrow IST.")
    duration_minutes: Optional[Any] = Field(60, description="Estimated duration in minutes (e.g. 180 for movie, 30 for call).")
    activity_type: Optional[str] = Field("general", description="Type of activity e.g. 'movie', 'meeting', 'dinner'.")

class CreateMeetingArgs(BaseModel):
    title: Optional[str] = Field("Meeting", description="Title of the meeting.")
    start_iso: str = Field(..., description="Meeting start datetime in ISO format (e.g. YYYY-MM-DDTHH:MM:SS with user's timezone offset or local time).")
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
    target_section: Optional[str] = Field(None, description="Optional target page or section to append into.")
    formatting: Optional[str] = Field(None, description="Optional formatting guidance: 'bullets', 'headings_and_bullets', or 'text'.")

class UpdateNotionPageArgs(BaseModel):
    content: str = Field(..., description="Markdown or code content to add to the Notion page.")
    page_title_or_id: Optional[str] = Field(None, description="Title, ID, or URL of the page.")
    section_title: Optional[str] = Field(None, description="Optional heading above the added content.")
    formatting: Optional[str] = Field(None, description="Optional formatting guidance.")

class ListGitHubReposArgs(BaseModel):
    username: Optional[str] = Field(None, description="GitHub username to list repositories for.")

class ListGitHubIssuesArgs(BaseModel):
    repo: Optional[str] = Field(None, description="Repository in 'owner/repo' or 'repo' format.")
    username: Optional[str] = Field(None, description="Optional GitHub username.")

class GetGitHubFileArgs(BaseModel):
    repo: str = Field(..., description="Repository in 'owner/repo' or 'repo' format (e.g. 'php-project' or 'nontech-cpu/php-project').")
    path: Optional[str] = Field("README.md", description="File or directory path to check/read (e.g. 'README.md', 'index.php', or '' for root).")
    username: Optional[str] = Field(None, description="Optional GitHub username.")

class UpdateGitHubFileArgs(BaseModel):
    repo: Optional[str] = Field(None, description="Repository in 'owner/repo' or 'repo' format (defaults to active repo from context).")
    path: Optional[str] = Field("README.md", description="File path to create or update (e.g. 'README.md').")
    content: Optional[str] = Field(None, description="The complete text content to write and commit into the file.")
    commit_message: Optional[str] = Field("Update file via PeculiarAI", description="Git commit message.")
    message: Optional[str] = Field(None, description="Alternative field for commit message.")
    username: Optional[str] = Field(None, description="Optional GitHub username.")

class GeneratePdfArgs(BaseModel):
    title: str = Field(..., description="Title of the PDF.")
    content: str = Field(..., description="Text content to format in the PDF.")

class GenerateExcelArgs(BaseModel):
    data_type: Optional[str] = Field("contacts", description="Type of data to export: 'contacts' or 'reminders'.")

class SearchUserEmailsArgs(BaseModel):
    query: Optional[str] = Field("in:inbox", description="Query to filter emails or documents e.g. 'ticket', 'flight', 'train', 'interview', 'bill', or 'in:inbox'.")
    max_results: Optional[int] = Field(5, description="Number of recent emails to retrieve and inspect.")

class CheckIntegrationsStatusArgs(BaseModel):
    service: Optional[str] = Field(None, description="Optional service name filter e.g. 'github', 'notion', 'google'.")

class ConnectIntegrationArgs(BaseModel):
    service: str = Field(..., description="Service to connect: 'google', 'github', or 'notion'.")
    value: Optional[str] = Field("connect", description="GitHub profile URL (e.g. 'https://github.com/nontech-cpu'), GitHub username, access token, Notion token, or 'connect' for Google.")

class DisconnectIntegrationArgs(BaseModel):
    service: str = Field(..., description="Service to disconnect: 'google', 'github', 'notion', or 'all'.")

class AskUserArgs(BaseModel):
    question: str = Field(..., description="Specific question to ask the user.")
    options: Optional[List[str]] = Field(None, description="Optional list of choices for the user.")

class GetWeatherArgs(BaseModel):
    city: Optional[str] = Field(None, description="Name of the city, town, or location (e.g. 'Manali', 'Delhi', 'Mumbai', 'London', 'Jodhpur'). If omitted, automatically resolves from context.")

class ConvertCurrencyArgs(BaseModel):
    amount: float = Field(..., description="Amount of money to convert (e.g. 92, 100, 50.5).")
    from_currency: str = Field(..., description="Currency symbol or code converting from (e.g. 'USD', '$', 'dollar', 'EUR', 'INR').")
    to_currency: Optional[str] = Field("INR", description="Currency code converting to (defaults to 'INR' or e.g. 'USD', 'EUR').")

class GetMarketPriceArgs(BaseModel):
    item: str = Field(..., description="Commodity or asset name (e.g. 'gold', 'silver', 'bitcoin', 'ethereum', 'solana').")
    currency: Optional[str] = Field("inr", description="Currency for the price quote e.g. 'inr' or 'usd'. Defaults to 'inr'.")

class CalculateArgs(BaseModel):
    expression: str = Field(..., description="Math expression or percentage calculation (e.g. '92 * 83.5', '18% of 4500', '(500 + 200) / 4').")

class SearchWebArgs(BaseModel):
    query: str = Field(..., description="Search query keywords e.g. 'Jodhpur to Udaipur bike toll distance' or 'latest movie release date'.")

class DeepResearchArgs(BaseModel):
    topic: str = Field(..., description="The subject or question to deeply research.")
    goal: Optional[str] = Field(None, description="Specific desired outcome or target.")

class LearnCorrectionArgs(BaseModel):
    wrong_concept: str = Field(..., description="What was incorrect or falsely stated (e.g. 'Dasaprakash in Jodhpur').")
    correct_rule: str = Field(..., description="The permanent true rule learned from the user.")
    category: Optional[str] = Field("user_feedback", description="Category e.g. 'place_correction', 'user_preference'.")

class FindNearbyPlacesArgs(BaseModel):
    category: str = Field(..., description="Category of places e.g. 'cafe', 'coffee shop', 'restaurant', 'gym'.")
    area: Optional[str] = Field(None, description="Local neighborhood or area e.g. 'Sardarpura', 'Ratanada'.")
    city: Optional[str] = Field("Jodhpur", description="City name, defaults to Jodhpur.")

class GetMovieShowtimesArgs(BaseModel):
    city: Optional[str] = Field("Jodhpur", description="City to look up movies for, defaults to Jodhpur.")
    area: Optional[str] = Field("Sardarpura", description="Local area or neighbourhood e.g. Sardarpura.")
    movie: Optional[str] = Field(None, description="Optional specific movie title to filter showtimes for.")

class ReadWebpageArgs(BaseModel):
    url: str = Field(..., description="Webpage or article URL to read, inspect, or summarize.")
    max_chars: Optional[int] = Field(4000, description="Maximum characters to extract (defaults to 4000).")

class ManageCommitmentArgs(BaseModel):
    action: str = Field(..., description="Action to perform: 'create', 'list', 'update', 'complete', 'cancel'.")
    title: Optional[str] = Field(None, description="Title or promise of the commitment (e.g. 'check with user after ₹1000 spend', 'send proposal to Rahul').")
    description: Optional[str] = Field(None, description="Additional context or details.")
    target_date: Optional[str] = Field(None, description="Target date in YYYY-MM-DD or ISO format.")
    condition_type: Optional[str] = Field("date", description="Condition trigger type: 'date', 'threshold', 'follow_up', 'manual'.")
    condition_details: Optional[Dict[str, Any]] = Field(None, description="Condition details e.g. {'amount': 1000, 'metric': 'expense'}.")
    commitment_id: Optional[int] = Field(None, description="Commitment ID when completing, updating, or cancelling.")
    status: Optional[str] = Field(None, description="Status when updating: 'pending', 'completed', 'cancelled', 'dismissed'.")

class WebSearchArgs(BaseModel):
    query: str = Field(..., description="Search query keywords to look up on the live web.")
    num_results: Optional[int] = Field(5, description="Number of results to return.")

class FetchUrlArgs(BaseModel):
    url: str = Field(..., description="URL of the webpage to fetch and read.")
    max_chars: Optional[int] = Field(4000, description="Max characters to extract.")


class GetMeasurementsArgs(BaseModel):
    subject: Optional[str] = Field(None, description="Who the measurement belongs to: 'user', 'friend', person's name, or null.")
    metric: Optional[str] = Field(None, description="Metric or reading type: 'glucose', 'sugar', 'blood_pressure', 'weight', etc.")
    date_from: Optional[str] = Field(None, description="Start date filter in YYYY-MM-DD format.")
    date_to: Optional[str] = Field(None, description="End date filter in YYYY-MM-DD format.")
    limit: Optional[int] = Field(10, description="Max readings to return (default 10).")


# -------------------------------------------------------------
# 2. Tool Definitions for Groq Function Calling
# -------------------------------------------------------------

TOOL_DEFINITIONS = [
    {
        "type": "function",
        "function": {
            "name": "execute_python_code",
            "description": "Universal Python Code Interpreter. Runs Python code to generate files (PDF via reportlab, PPTX via pptx, Excel/CSV via pandas, charts via matplotlib) or perform calculations. Created files are sent directly to Telegram.",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Complete executable Python script with imports. Save output file in current directory."
                    },
                    "purpose": {
                        "type": "string",
                        "description": "Short explanation of the file or task, e.g. 'ML vs DL comparison PDF'."
                    }
                },
                "required": ["code"]
            }
        }
    },
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
            "name": "get_measurements",
            "description": "Exact SQL lookup for numerical health readings (glucose/sugar, blood pressure, weight, etc.) by metric, subject ('user', 'friend', or person's name), and date range. ALWAYS use this instead of fuzzy vector search for health numbers, daily logs, trends, or 'kal ki reading'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "subject": {"type": ["string", "null"], "description": "Subject: 'user', 'friend', or name of person."},
                    "metric": {"type": ["string", "null"], "description": "Reading metric: 'glucose', 'sugar', 'blood_pressure', 'weight', etc."},
                    "date_from": {"type": ["string", "null"], "description": "Start date in YYYY-MM-DD format."},
                    "date_to": {"type": ["string", "null"], "description": "End date in YYYY-MM-DD format."},
                    "limit": {"type": ["integer", "null"], "description": "Max readings to fetch (default 10)."}
                }
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
            "description": "Save or update a contact in persistent address book with phone, email, role, and context notes or promised tasks.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Full name of the contact."},
                    "phone": {"type": ["string", "null"], "description": "Phone number."},
                    "email": {"type": ["string", "null"], "description": "Email address."},
                    "role": {"type": ["string", "null"], "description": "Role, title, or company."},
                    "aliases": {"type": ["array", "null"], "items": {"type": "string"}, "description": "Nicknames or alternate spellings."},
                    "notes": {"type": ["string", "null"], "description": "Any context, purpose, project, reason for contacting, or commitments mentioned by the user."}
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
            "name": "request_master_privacy_wipe",
            "description": "Initiate permanent master privacy wipe to delete all user memories, facts, notes, contacts, and reminders across all platforms with secure OTP confirmation. Use this whenever the user asks to delete all memory or wipe saved data.",
            "parameters": {
                "type": "object",
                "properties": {
                    "confirm": {"type": ["boolean", "null"], "description": "Confirmation flag."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_user_timezone",
            "description": "Configure user's local timezone (e.g. 'America/New_York', 'Europe/London', 'Asia/Kolkata') when user states their city, country, or timezone.",
            "parameters": {
                "type": "object",
                "properties": {
                    "timezone": {"type": "string", "description": "Valid IANA timezone e.g. 'America/New_York', 'Europe/London', 'Asia/Kolkata'."}
                },
                "required": ["timezone"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_note_or_fact",
            "description": "Delete a specific note, memory fact, flight ticket, or booking when user asks to remove it (e.g. 'is ticket ko memory se hata do').",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Keywords of the ticket, flight, booking, or fact to delete from memory."}
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_smart_list",
            "description": "Create, view, add items to, or delete user checklists and smart lists (e.g. 'Grocery list', 'Shopping list', 'Work items'). Fully synchronized with the Web Companion Lists tab.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": ["create", "add_items", "get", "list_all", "delete"], "description": "Action to perform."},
                    "list_name": {"type": ["string", "null"], "description": "Name of the checklist/smart list e.g. 'Groceries', 'Shopping'."},
                    "items": {"type": ["array", "null"], "items": {"type": "string"}, "description": "List of item names e.g. ['Milk', 'Eggs', 'Bread']."},
                    "category": {"type": ["string", "null"], "description": "Optional category e.g. 'groceries', 'work', 'travel', 'personal'."}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_packing_list",
            "description": "Create a smart packing/travel checklist for a trip (e.g. 'Goa trip packing: passport, photo, Aadhaar card') and sets a reminder the evening before.",
            "parameters": {
                "type": "object",
                "properties": {
                    "trip_title": {"type": "string", "description": "Trip or packing list name e.g. 'Goa Trip'."},
                    "items": {"type": "array", "items": {"type": "string"}, "description": "Items to pack e.g. ['Passport', 'Aadhaar Card', 'Photo']."},
                    "trip_date": {"type": ["string", "null"], "description": "Optional trip date in YYYY-MM-DD format."}
                },
                "required": ["trip_title", "items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_reminder",
            "description": (
                "Save a task, meeting, appointment, dinner date, shopping trip, workout, or reminder with a due date and time. "
                "CRITICAL: If the user mentions setting a reminder for any activity (e.g. 'shopping ke liye jana hai reminder laga do', 'meeting reminder', 'gym reminder') with a time, ALWAYS call this tool. "
                "Extract and convert time into due_time in 24-hour 'HH:MM' format (e.g. '23:00'). "
                "If user specifies duration (e.g. '1 hour'), pass duration_minutes=60. "
                "If no date is mentioned, due_date defaults to today's date."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task, meeting, or reminder title."},
                    "due_date": {"type": ["string", "null"], "description": "Date in YYYY-MM-DD format. Defaults to today's date if omitted."},
                    "due_time": {
                        "type": ["string", "null"],
                        "description": (
                            "Due time in 24-hour HH:MM format (e.g. '23:00', '10:30', '15:00'). "
                            "MANDATORY: Convert natural times ('11pm' -> '23:00', '10:30pm' -> '22:30', '3 baje' -> '15:00'). "
                            "NEVER leave null when the user mentions a time!"
                        )
                    },
                    "duration_minutes": {"type": ["integer", "null"], "description": "Duration in minutes if this is a meeting or timed event (e.g. 30, 45, 60, 120)."},
                    "category": {"type": ["string", "null"], "description": "Category e.g. appointments, bills, work."},
                    "recurrence": {"type": ["string", "null"], "description": "Recurrence pattern: 'daily' for every day (roj, daily, har din, jbtk m na khu), 'weekly' for every week, or 'none' for single occurrence."},
                    "force": {"type": ["boolean", "null"], "description": "Set to true to force scheduling even if a time conflict or tight schedule exists."}
                },
                "required": ["title"]
            }
        }
    },
    
    {
        "type": "function",
        "function": {
            "name": "update_reminder",
            "description": (
                "Update or reschedule an existing reminder, meeting, or event (e.g. updating duration, time, or date). "
                "Use when user says 'meeting 1 hour ki hai', 'meeting ko 3pm shift kar do', 'meeting duration 60 mins kar do'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": ["string", "null"], "description": "Title or keyword of the reminder or meeting to update (e.g. 'Meeting')."},
                    "reminder_id": {"type": ["integer", "null"], "description": "ID of the reminder if known."},
                    "due_date": {"type": ["string", "null"], "description": "Updated date in YYYY-MM-DD format if date is being changed."},
                    "due_time": {"type": ["string", "null"], "description": "Updated time in 24-hour HH:MM format if time is being changed."},
                    "duration_minutes": {"type": ["integer", "null"], "description": "Updated duration in minutes (e.g. 60 for 1 hour, 30 for 30 mins)."},
                    "force": {"type": ["boolean", "null"], "description": "Set to true to force update even if a time conflict exists."}
                }
            }
        }
    },
    
    {
        "type": "function",
        "function": {
            "name": "delete_reminder",
            "description": "Delete or cancel a saved reminder, meeting, or task by title keyword or ID. Use ONLY when explicitly asked to delete/cancel.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title or keyword of the reminder/event to delete."},
                    "reminder_id": {"type": ["integer", "null"], "description": "Optional specific reminder ID to delete."}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": (
                "List saved tasks, reminders, and appointments. Call this whenever the user asks about their reminders, "
                "tasks, or today's schedule (e.g. 'mere aajke reminder btana', 'aaj ke reminders dikhao', 'reminders check karo'). "
                "NEVER refuse reminder view requests."
            ),
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
                    "start_iso": {"type": "string", "description": "Start datetime in ISO format (e.g. YYYY-MM-DDTHH:MM:SS with user's timezone offset or local time)."},
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
            "description": "Send an email via connected Gmail. Triggers system confirmation prompt.",
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
            "description": "Create a documentation page in Notion with title and markdown content (supports ```code``` blocks, headings, lists). Only call when user explicitly asks for Notion.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Page title."},
                    "content": {"type": "string", "description": "Markdown text content."},
                    "target_section": {"type": ["string", "null"], "description": "Optional target page/section name to append into, or null for new."}
                },
                "required": ["title", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_notion_page",
            "description": "Append or add new content, code blocks, or notes to an existing Notion page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "Markdown content or code block to add."},
                    "page_title_or_id": {"type": ["string", "null"], "description": "Title, ID, or URL of page to update, or null for active page."},
                    "section_title": {"type": ["string", "null"], "description": "Optional section heading."},
                    "formatting": {"type": ["string", "null"], "description": "Optional formatting guidance e.g. 'code', 'bullets', 'text'."}
                },
                "required": ["content"]
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
            "name": "get_github_file",
            "description": "Inspect, check existence, or read a file (like README.md, code files) or list directory contents from a GitHub repository.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name in 'owner/repo' or 'repo' format (e.g. 'php-project' or 'nontech-cpu/php-project')."},
                    "path": {"type": ["string", "null"], "description": "File or folder path to inspect (defaults to 'README.md', or '' for root directory)."},
                    "username": {"type": ["string", "null"], "description": "Optional GitHub username."}
                },
                "required": ["repo"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "update_github_file",
            "description": "Create or update a file (like README.md or code) in a GitHub repository with an autonomous commit.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": "string", "description": "Repository name in 'owner/repo' or 'repo' format."},
                    "path": {"type": "string", "description": "File path to create or update (e.g. 'README.md')."},
                    "content": {"type": "string", "description": "The complete text content to write and commit to the file."},
                    "commit_message": {"type": ["string", "null"], "description": "Git commit message."},
                    "username": {"type": ["string", "null"], "description": "Optional GitHub username."}
                },
                "required": ["repo", "path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_github_issues",
            "description": "List open issues for a specific GitHub repository or the authenticated user.",
            "parameters": {
                "type": "object",
                "properties": {
                    "repo": {"type": ["string", "null"], "description": "Repository name in 'owner/repo' or 'repo' format."},
                    "username": {"type": ["string", "null"], "description": "Optional GitHub username."}
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
            "name": "search_user_emails",
            "description": "Search and inspect recent emails and attached documents (train/flight tickets with PNR, booking confirmations, interview invites, bills, receipts) from the user's connected Gmail inbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": ["string", "null"], "description": "Search query or category e.g. 'ticket', 'train', 'flight', 'interview', 'bill', or 'in:inbox'."},
                    "max_results": {"type": ["integer", "null"], "description": "Number of emails to inspect (default 5)."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_integrations_status",
            "description": "Check whether user third-party integrations (GitHub, Notion, Google Calendar/Meet/Gmail/Drive) or email accounts are connected/linked, and retrieve which email address is linked to the user account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": ["string", "null"], "description": "Optional service name filter e.g. 'github', 'notion', 'google', 'email'."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "connect_integration",
            "description": "Connect a user integration like Google (Calendar/Meet/Gmail/Drive), GitHub, or Notion. For Google, returns the secure OAuth authorization link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Service to connect: 'google', 'github', or 'notion'."},
                    "value": {"type": ["string", "null"], "description": "GitHub profile URL, username, personal access token, or 'connect' for Google."}
                },
                "required": ["service"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "disconnect_integration",
            "description": "Disconnect or unlink a user integration like Google, GitHub, or Notion. Removes credentials and revokes access.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Service to disconnect: 'google', 'github', 'notion', or 'all'."}
                },
                "required": ["service"]
            }
        }
    },

    {
        "type": "function",
        "function": {
            "name": "list_notion_pages",
            "description": "Lists or searches existing pages and notes in the user's connected Notion workspace.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": ["string", "null"], "description": "Optional search term to filter pages by title."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_notion_page_content",
            "description": "Reads and returns the text, headings, code blocks, and notes inside a specific Notion page or section.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id_or_title": {"type": "string", "description": "The title, ID, or URL of the Notion page to read (e.g. 'FastAPI Quick Notes', 'Notes', 'DSA NOTES')."}
                },
                "required": ["page_id_or_title"]
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
                    "options": {"type": ["array", "null"], "items": {"type": "string"}, "description": "Optional choices or null."}
                },
                "required": ["question"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Fetch live real-time weather, temperature, humidity, wind, and forecast conditions for any city or location globally. If city is not explicitly given, pass null to auto-resolve from user's memory, facts, or ticket.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {"type": ["string", "null"], "description": "City or location name (e.g. 'Manali', 'Delhi', 'Mumbai', 'London', 'Jodhpur'). Optional."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "convert_currency",
            "description": "Convert currency amounts using live real-time foreign exchange market rates (e.g. USD to INR, EUR to USD, GBP to INR).",
            "parameters": {
                "type": "object",
                "properties": {
                    "amount": {"type": "number", "description": "Amount to convert (e.g. 92)."},
                    "from_currency": {"type": "string", "description": "Currency converting from (e.g. 'USD', 'dollar', 'EUR')."},
                    "to_currency": {"type": ["string", "null"], "description": "Currency converting to (e.g. 'INR', 'USD'). Defaults to 'INR'."}
                },
                "required": ["amount", "from_currency"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_market_price",
            "description": "Fetch live real-time market prices STRICTLY for precious metals (Gold 24K & 22K per gram/10g, Silver) and major cryptocurrencies (Bitcoin, Ethereum, Solana). NEVER call this tool for travel fares, vehicle tolls, road costs, product prices, or general estimates.",
            "parameters": {
                "type": "object",
                "properties": {
                    "item": {
                        "type": "string",
                        "enum": ["gold", "silver", "bitcoin", "ethereum", "solana"],
                        "description": "Asset name strictly one of: 'gold', 'silver', 'bitcoin', 'ethereum', 'solana'."
                    },
                    "currency": {"type": ["string", "null"], "description": "Currency code 'inr' or 'usd'. Defaults to 'inr'."}
                },
                "required": ["item"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "calculate",
            "description": "Safely evaluates mathematical expressions, percentages (e.g. '18% of 4500'), and arithmetic calculations without code execution risk.",
            "parameters": {
                "type": "object",
                "properties": {
                    "expression": {"type": "string", "description": "Math expression or percentage to calculate (e.g. '92 * 86.5' or '18% of 5000')."}
                },
                "required": ["expression"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "deep_research",
            "description": "Trigger an autonomous 3-agent research team for complex queries, trip planning, market comparisons, or multi-faceted evaluations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "topic": {
                        "type": "string",
                        "description": "The subject or question to deeply research."
                    },
                    "goal": {
                        "type": "string",
                        "description": "Specific outcome or goal e.g. 'Compare budget options with prices'."
                    }
                },
                "required": ["topic"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "learn_user_correction",
            "description": "Permanently learn and record a user correction or feedback rule so the assistant never repeats the mistake in any future conversation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "wrong_concept": {
                        "type": "string",
                        "description": "What was incorrect or falsely claimed."
                    },
                    "correct_rule": {
                        "type": "string",
                        "description": "The exact permanent rule learned to avoid this mistake."
                    },
                    "category": {
                        "type": "string",
                        "description": "Category e.g. 'place_correction', 'preference'."
                    }
                },
                "required": ["wrong_concept", "correct_rule"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_nearby_places",
            "description": "Discover real, verified local places, shopping malls, markets, cafes, coffee shops, restaurants, and amenities in a specific city or neighborhood. Zero hallucination. Do NOT use this tool when the user is asking to set a reminder or schedule an activity.",
            "parameters": {
                "type": "object",
                "properties": {
                    "category": {
                        "type": "string",
                        "description": "Category e.g. 'mall', 'shopping mall', 'market', 'coffee shop', 'cafe', 'restaurant'."
                    },
                    "area": {
                        "type": ["string", "null"],
                        "description": "Area or neighbourhood e.g. 'Sardarpura', 'Ratanada', or null for city-wide."
                    },
                    "city": {
                        "type": ["string", "null"],
                        "description": "City name e.g. 'Jodhpur', 'Jaipur', or null for default city."
                    }
                },
                "required": ["category"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_movie_showtimes",
            "description": "Fetch live BookMyShow cinema listings, latest running movies, and showtimes for nearby theatres in Jodhpur / user's city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "City name, defaults to Jodhpur."
                    },
                    "area": {
                        "type": "string",
                        "description": "Local neighborhood e.g. Sardarpura."
                    },
                    "movie": {
                        "type": "string",
                        "description": "Optional specific movie name."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_web",
            "description": "Perform live internet web search for real-time external knowledge, current events, driving routes & highway tolls, places, technical documentation, train/flight status, or general facts not in memory.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Clean, concise search terms e.g. 'Jodhpur to Udaipur bike toll distance', 'latest budget highlights', 'who is CEO of X'."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "Perform live internet web search for real-time external knowledge, current events, flights, weather, places, or general facts.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Search query terms."
                    },
                    "num_results": {
                        "type": ["integer", "null"],
                        "description": "Number of results to return (default 5)."
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "fetch_url",
            "description": "Fetch and extract readable text from any public web page URL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full URL to fetch."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_commitment",
            "description": "Create, list, update, complete, or cancel commitments and proactive follow-up promises (e.g. 'follow up after spending ₹1000', 'call Rahul on Monday').",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "update", "complete", "cancel"],
                        "description": "Action to perform."
                    },
                    "title": {
                        "type": ["string", "null"],
                        "description": "Title or promise of the commitment."
                    },
                    "description": {
                        "type": ["string", "null"],
                        "description": "Additional context or details."
                    },
                    "target_date": {
                        "type": ["string", "null"],
                        "description": "Target date in YYYY-MM-DD or ISO format."
                    },
                    "condition_type": {
                        "type": ["string", "null"],
                        "description": "Condition type: 'date', 'threshold', 'follow_up', 'manual'."
                    },
                    "condition_details": {
                        "type": ["object", "null"],
                        "description": "Condition details e.g. {'amount': 1000, 'metric': 'expense'}."
                    },
                    "commitment_id": {
                        "type": ["integer", "null"],
                        "description": "Commitment ID to update, complete, or cancel."
                    },
                    "status": {
                        "type": ["string", "null"],
                        "description": "New status: 'pending', 'completed', 'cancelled', 'dismissed'."
                    }
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_webpage",
            "description": "Fetch, inspect, and extract readable text from any public article, blog post, documentation, or webpage URL for summarization or Q&A.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "Full webpage URL including https://"
                    },
                    "max_chars": {
                        "type": ["integer", "null"],
                        "description": "Max characters to extract (default 4000)."
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_relationship",
            "description": "People CRM & Kinship Graph: Add, update, resolve, or list user relationships, family members, friends, or managers along with birthdays and contact details.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "get"],
                        "description": "Action to perform: 'add' (save/update), 'list' (view all), or 'get' (lookup person)"
                    },
                    "name": {"type": "string", "description": "Full or first name of the person (e.g. 'Ashok Kumar Sharma', 'Devendra')"},
                    "relationship": {"type": "string", "description": "Kinship or role (e.g. 'Father', 'Mother', 'Manager', 'Brother', 'Friend')"},
                    "phone": {"type": ["string", "null"], "description": "Phone number if provided"},
                    "birthday": {"type": ["string", "null"], "description": "Birthday date or string (e.g. '15 August', '1990-08-15')"},
                    "notes": {"type": ["string", "null"], "description": "Any additional context or notes"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "manage_subscription",
            "description": "Subscription & Free-Trial Watchdog: Track, query, or check upcoming renewals and trial expirations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["add", "list", "check_expiring"],
                        "description": "Action: 'add' (track new sub/trial), 'list' (view active), 'check_expiring' (check next N days)"
                    },
                    "service_name": {"type": ["string", "null"], "description": "Name of the service (e.g. 'Netflix', 'ChatGPT Plus', 'Cursor', 'Spotify')"},
                    "amount": {"type": ["number", "null"], "description": "Price/Amount in currency"},
                    "billing_cycle": {"type": ["string", "null"], "enum": ["monthly", "annual", "quarterly", "recurring"], "description": "Cycle"},
                    "renewal_date": {"type": ["string", "null"], "description": "Next renewal or trial end date (YYYY-MM-DD or readable)"},
                    "is_trial": {"type": ["boolean", "null"], "description": "True if this is a free trial"},
                    "cancel_url": {"type": ["string", "null"], "description": "URL link to cancel or manage"}
                },
                "required": ["action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_memory_health_radar",
            "description": "Memory Health Score & Cognitive Decay Radar: Computes real-time clarity score (0-100) and highlights overdue commitments.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


# -------------------------------------------------------------
# 3. Tool Execution Dispatcher
# -------------------------------------------------------------

async def execute_tool(chat_id: int, tool_name: str, arguments: dict, user_context: Optional[dict] = None) -> dict:
    """Executes a tool call asynchronously and returns structured JSON output."""
    try:
        # 1. Unified Connector Architecture Dispatch
        from connectors.registry import get_connector_by_tool
        from connectors.errors import format_connector_error
        conn = get_connector_by_tool(tool_name)
        if conn:
            tool_res = await conn.execute(chat_id, tool_name, arguments)
            if not tool_res.ok:
                err = tool_res.error or {}
                err_msg = format_connector_error(
                    error_type=err.get("type", "api_error"),
                    connector_name=conn.display_name,
                    instructions=conn.connect_instructions(chat_id),
                    details=err.get("message", "")
                )
                return {
                    "status": "error",
                    "error_type": err.get("type", "api_error"),
                    "connector": conn.name,
                    "message": err_msg
                }
            if tool_name in ("create_reminder", "delete_reminder", "create_meeting", "delete_meeting", "manage_smart_list"):
                try:
                    from agent.prompt import invalidate_prompt_schedule_cache
                    invalidate_prompt_schedule_cache(chat_id)
                except Exception:
                    pass
            if isinstance(tool_res.data, dict):
                return tool_res.data
            return {"status": "success", "result": tool_res.data}

        if tool_name == "execute_python_code":
            from agent.sandbox import run_python_sandbox
            code = arguments.get("code") or ""
            purpose = arguments.get("purpose") or ""
            return await run_python_sandbox(chat_id, code, purpose)

        if tool_name == "validate_ticket_date":
            from state.active_context import get_active_ticket, parse_ticket_datetime
            tkt = get_active_ticket(chat_id)
            dt_str = (arguments.get("datetime_str") or (tkt.get("datetime_str") if tkt else None))
            tkt_dt = parse_ticket_datetime(dt_str) if dt_str else None
            now_ist = datetime.now(IST)
            is_past = (tkt_dt < now_ist) if tkt_dt else False
            return {
                "status": "success",
                "is_past": is_past,
                "ticket": tkt,
                "message": "Ticket date is in the past." if is_past else "Ticket date is valid."
            }

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

        elif tool_name == "get_measurements":
            args = GetMeasurementsArgs(**arguments)
            try:
                from memory.store import resolve_user_id, find_entity, get_measurements
                uid = resolve_user_id(chat_id)
                eid = None
                if args.subject and args.subject.lower() not in ("user", "self", "me", "mera", "meri", "mere"):
                    ent = find_entity(uid, args.subject)
                    if ent:
                        eid = ent["id"]

                readings = get_measurements(
                    user_id=uid,
                    metric=args.metric,
                    subject_entity_id=eid,
                    date_from=args.date_from,
                    date_to=args.date_to,
                    limit=args.limit or 10
                )
                # Fallback between sugar / glucose
                if not readings and args.metric:
                    m_low = args.metric.lower()
                    if "sugar" in m_low:
                        readings = get_measurements(
                            user_id=uid,
                            metric="glucose",
                            subject_entity_id=eid,
                            date_from=args.date_from,
                            date_to=args.date_to,
                            limit=args.limit or 10
                        )
                    elif "glucose" in m_low:
                        readings = get_measurements(
                            user_id=uid,
                            metric="sugar",
                            subject_entity_id=eid,
                            date_from=args.date_from,
                            date_to=args.date_to,
                            limit=args.limit or 10
                        )

                # Format readings cleanly for model consumption
                formatted_readings = []
                for r in readings:
                    sub = r.get("subject_name") or args.subject or "user"
                    m_at = str(r.get("measured_at") or r.get("created_at") or "")
                    ctx = f" ({r['context']})" if r.get("context") else ""
                    formatted_readings.append({
                        "id": r.get("id"),
                        "subject": sub,
                        "metric": r.get("metric"),
                        "value": float(r.get("value")) if r.get("value") is not None else None,
                        "unit": r.get("unit"),
                        "measured_at": m_at,
                        "context": r.get("context"),
                        "summary": f"{sub}: {r.get('metric')} = {r.get('value')} {r.get('unit', '')}{ctx} on {m_at[:10]}"
                    })

                return {
                    "status": "success",
                    "subject": args.subject or "user",
                    "metric": args.metric,
                    "date_from": args.date_from,
                    "date_to": args.date_to,
                    "count": len(formatted_readings),
                    "readings": formatted_readings
                }
            except Exception as exc:
                return {"status": "error", "message": f"Failed to retrieve measurements: {exc}"}

        elif tool_name == "get_contact":
            args = GetContactArgs(**arguments)
            contacts = []
            try:
                contacts = db.get_contact_memories(chat_id)
            except Exception:
                contacts = []
            from main import find_matching_contact
            match = find_matching_contact(args.name_or_alias, contacts) if contacts else None

            cognitive_data = {}
            try:
                from memory.store import resolve_user_id, find_entity, get_commitments_for_user
                uid = resolve_user_id(chat_id)
                ent = find_entity(uid, args.name_or_alias)
                if ent:
                    ent_comms = get_commitments_for_user(uid, entity_id=ent["id"])
                    comm_str = "; ".join([c["commitment"] for c in ent_comms])
                    attrs = ent.get("attributes", {})
                    cognitive_data = {
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

            if match or cognitive_data:
                name = (match.get("name") if match else None) or cognitive_data.get("name") or args.name_or_alias
                phone = (match.get("phone") if match and match.get("phone") else None) or cognitive_data.get("phone") or ""
                email = (match.get("email") if match and match.get("email") else None) or cognitive_data.get("email") or ""
                role_title = (match.get("role_title") if match and match.get("role_title") else None) or cognitive_data.get("role_title") or ""
                company = (match.get("company") if match and match.get("company") else None) or cognitive_data.get("company") or ""

                m_comm = match.get("commitments", "") if match else ""
                c_comm = cognitive_data.get("commitments", "")
                commitments = "; ".join(filter(None, [m_comm, c_comm])) if (m_comm != c_comm) else m_comm

                return {
                    "found": True,
                    "name": name,
                    "company": company,
                    "role_title": role_title,
                    "email": email,
                    "phone": phone,
                    "commitments": commitments,
                    "context_notes": (match.get("context_notes") if match else "") or cognitive_data.get("context_notes", "")
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

                if args.notes and ent_id:
                    try:
                        from memory.store import save_memory_item
                        save_memory_item(
                            user_id=uid,
                            content=f"Contact purpose for {args.name}: {args.notes}",
                            memory_type="commitment",
                            subject_entity_id=ent_id
                        )
                    except Exception as comm_err:
                        print("Contact commitment save err:", comm_err)
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
                "notes": args.notes,
                "message": f"Contact '{args.name}' successfully saved in persistent address book and memory."
            }

        elif tool_name == "list_contacts":
            from memory.store import resolve_user_id
            uid = resolve_user_id(chat_id)

            contacts_by_name = {}
            EXCLUDED_NAMES = {"user", "assistant", "bot", "me", "myself", "friend", "neerav", "nirav", "admin", "ai", "companion"}

            # Primary: fetch from dedicated contact_memories across unified identities
            raw_cms = db.get_contact_memories(chat_id)
            for cm in raw_cms:
                c_name = cm["name"].strip()
                if c_name.lower() in EXCLUDED_NAMES or "@" in c_name:
                    continue
                k = c_name.lower()
                contacts_by_name[k] = {
                    "name": c_name,
                    "phone": cm.get("phone") or "",
                    "email": cm.get("email") or "",
                    "role": cm.get("role_title") or cm.get("role") or "",
                    "company": cm.get("company") or "",
                    "notes": cm.get("context_notes") or "",
                    "commitments": cm.get("commitments") or ""
                }

            # Secondary: check cognitive entities for any additional contacts with actual contact info
            try:
                from memory.store import list_entities, get_commitments_for_user
                ents = list_entities(uid, entity_type="person") or []
                for e in ents:
                    e_name = (e.get("name") or "").strip()
                    if not e_name or e_name.lower() in EXCLUDED_NAMES or "@" in e_name:
                        continue
                    attrs = e.get("attributes") or {}
                    phone = attrs.get("phone") or ""
                    email = attrs.get("email") or ""
                    role = attrs.get("role_title") or attrs.get("role") or ""
                    notes = attrs.get("context_notes") or ""
                    # MUST have contact details (phone, email, role, or explicit contact notes)
                    if not (phone or email or role or (notes and "contact" in notes.lower())):
                        continue
                    comms = get_commitments_for_user(uid, entity_id=e["id"])
                    comm_str = "; ".join([c["commitment"] for c in comms if c.get("commitment")])

                    k = e_name.lower()
                    if k in contacts_by_name:
                        if phone and not contacts_by_name[k]["phone"]:
                            contacts_by_name[k]["phone"] = phone
                        if email and not contacts_by_name[k]["email"]:
                            contacts_by_name[k]["email"] = email
                        if role and not contacts_by_name[k]["role"]:
                            contacts_by_name[k]["role"] = role
                        if notes and (not contacts_by_name[k]["notes"] or contacts_by_name[k]["notes"] == "Saved via Cognitive Extractor"):
                            contacts_by_name[k]["notes"] = notes
                        if comm_str and not contacts_by_name[k]["commitments"]:
                            contacts_by_name[k]["commitments"] = comm_str
                    else:
                        contacts_by_name[k] = {
                            "name": e_name,
                            "phone": phone,
                            "email": email,
                            "role": role,
                            "company": attrs.get("company") or "",
                            "notes": notes,
                            "commitments": comm_str
                        }
            except Exception as exc:
                logger.warning(f"Cognitive list_entities fallback: {exc}")

            final_contacts = list(contacts_by_name.values())
            return {"count": len(final_contacts), "contacts": final_contacts}

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
            lower_fact = args.text.lower()
            if any(w in lower_fact for w in ("nahi hai", "not", "never", "galat", "wrong", "fake", "dont", "mat")):
                db.save_user_correction(chat_id, args.text, args.text, "user_correction")
            return {"status": "success", "saved_fact": args.text}

        elif tool_name == "clear_chat_history":
            db.clear_chat_history(chat_id)
            return {
                "status": "success",
                "message": "Chat history has been successfully cleared."
            }

        elif tool_name == "set_user_timezone":
            args = SetUserTimezoneArgs(**arguments)
            db.set_user_timezone(chat_id, args.timezone)
            google_services._USER_TZ_CACHE.pop(chat_id, None)
            return {
                "status": "success",
                "message": f"User timezone successfully updated to {args.timezone}.",
                "timezone": args.timezone
            }

        elif tool_name == "request_master_privacy_wipe":
            db.set_state(chat_id, "confirm_master_wipe")
            return {
                "status": "confirmation_required",
                "message": (
                    "⚠️ **Security & Privacy Warning**:\n\n"
                    "Kya aap sach me apni saari memories, chat history, notes, reminders, smart lists aur saved contacts permanently delete karna chahte hain? "
                    "Ye action irreversible hai aur data wapas recover nahi hoga.\n\n"
                    "Reply **yes/haan** to confirm ya **no/cancel** to abort."
                )
            }

        elif tool_name == "delete_note_or_fact":
            args = DeleteNoteOrFactArgs(**arguments)
            deleted = db.delete_matching_user_facts(chat_id, args.query)
            try:
                from memory.store import resolve_user_id
                uid = resolve_user_id(chat_id)
                with db.engine.begin() as conn:
                    conn.execute(
                        db.text("DELETE FROM memory_items WHERE user_id = :u AND LOWER(content) LIKE LOWER(:q)"),
                        {"u": uid, "q": f"%{args.query.strip()}%"}
                    )
            except Exception:
                pass
            return {
                "status": "success",
                "deleted": True,
                "query": args.query,
                "message": f"Ticket/Fact matching '{args.query}' has been successfully deleted from memory."
            }

        elif tool_name == "manage_smart_list":
            args = ManageSmartListArgs(**arguments)
            act = args.action.lower()
            if act in ("create", "add_items"):
                lname = args.list_name or "My List"
                lid = db.find_or_create_smart_list(chat_id, lname, category=args.category or "general")
                added = []
                if args.items:
                    for itm in args.items:
                        db.add_smart_list_item(chat_id, lid, itm)
                        added.append(itm)
                try:
                    from api_v1 import _invalidate_all_app_caches
                    _invalidate_all_app_caches(chat_id)
                except Exception:
                    pass
                return {"status": "success", "message": f"List '{lname}' saved with {len(added)} items.", "list_name": lname, "items": added}
            elif act == "get":
                lists = db.get_smart_lists(chat_id)
                target = None
                lname = (args.list_name or "").lower()
                for l in lists:
                    if lname in l["name"].lower():
                        target = l
                        break
                if target:
                    return {"status": "success", "list": target}
                return {"status": "error", "message": f"List '{args.list_name}' not found.", "available_lists": [l["name"] for l in lists]}
            elif act == "list_all":
                lists = db.get_smart_lists(chat_id)
                return {"status": "success", "lists": [{"name": l["name"], "items_count": len(l.get("items", []))} for l in lists]}
            elif act == "delete":
                lists = db.get_smart_lists(chat_id)
                lname = (args.list_name or "").lower()
                for l in lists:
                    if lname in l["name"].lower():
                        db.delete_smart_list(chat_id, l["id"])
                        try:
                            from api_v1 import _invalidate_all_app_caches
                            _invalidate_all_app_caches(chat_id)
                        except Exception:
                            pass
                        return {"status": "success", "message": f"List '{l['name']}' deleted."}
                return {"status": "error", "message": f"List '{args.list_name}' not found."}

        elif tool_name == "create_packing_list":
            args = CreatePackingListArgs(**arguments)
            # Create a persistent Smart List for the trip
            list_name = f"{args.trip_title} Packing"
            lid = db.find_or_create_smart_list(chat_id, list_name, category="travel")
            for itm in args.items:
                db.add_smart_list_item(chat_id, lid, itm)

            # If trip date specified, schedule reminder for 1 day prior at 7:30 PM
            if args.trip_date:
                try:
                    t_date = date.fromisoformat(args.trip_date)
                    day_before = t_date - timedelta(days=1)
                    db.save_memory(
                        chat_id,
                        category="appointments",
                        title=f"Check {list_name}: {', '.join(args.items[:3])}",
                        due_date=day_before,
                        due_time=dtime(19, 30)
                    )
                except Exception:
                    pass

            return {
                "status": "success",
                "list_id": lid,
                "list_name": list_name,
                "items_count": len(args.items),
                "items": args.items,
                "message": f"Smart packing list '{list_name}' created with {len(args.items)} items."
            }

        elif tool_name == "update_reminder":
            args = UpdateReminderArgs(**arguments)
            u_tz, _ = await _resolve_user_timezone(chat_id)
            today_d = datetime.now(u_tz).date()

            target_kw = (args.title or "").strip().lower()
            rem_id = args.reminder_id

            user_msg = ""
            if user_context and isinstance(user_context, dict):
                user_msg = (user_context.get("user_text") or "").lower()

            # Find matching item from DB
            matched_row = None
            if rem_id:
                with db.engine.connect() as conn:
                    row = conn.execute(
                        db.text("SELECT id, title, due_date, due_time, COALESCE(duration_minutes, 30) AS duration_minutes FROM memory_items WHERE id = :id AND user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c)"),
                        {"id": rem_id, "c": chat_id}
                    ).mappings().first()
                    if row:
                        matched_row = dict(row)
            else:
                existing_items = db.items_for_date(chat_id, today_d)

                # 1. If time hint or args.due_time is present, match by time first
                if args.due_time:
                    for r in existing_items:
                        r_tm = _get_row_val(r, "due_time", 3)
                        if r_tm and str(r_tm).startswith(args.due_time[:5]):
                            matched_row = r
                            break

                # 2. If user mentions "new", "latest", "recent", "dusri", inspect in reverse
                is_latest_intent = any(w in user_msg for w in ("new", "latest", "recent", "dusri", "naya", "nayi", "abhi wali"))
                search_list = list(reversed(existing_items)) if is_latest_intent else list(existing_items)

                # 3. Match by title keyword
                if not matched_row and target_kw:
                    for r in search_list:
                        r_title = (_get_row_val(r, "title", 1, "") or "").strip().lower()
                        if target_kw == r_title or target_kw in r_title or r_title in target_kw:
                            matched_row = r
                            break

                # 4. If still not matched, search upcoming 7 days
                if not matched_row and target_kw:
                    range_items = db.items_for_date_range(chat_id, today_d, today_d + timedelta(days=7))
                    for r in range_items:
                        r_title = (_get_row_val(r, "title", 1, "") or "").strip().lower()
                        if target_kw == r_title or target_kw in r_title or r_title in target_kw:
                            matched_row = r
                            break

                # 5. If title keyword was omitted or generic and only one/latest item exists today
                if not matched_row and existing_items:
                    matched_row = search_list[0]

            if not matched_row:
                return {
                    "action": "update_reminder",
                    "tool_name": "update_reminder",
                    "status": "error",
                    "message": f"Aapka '{args.title or 'item'}' se related koi active reminder nahi mila jise update kiya ja sake."
                }

            row_id = _get_row_val(matched_row, "id", 4)
            orig_title = _get_row_val(matched_row, "title", 1, "")
            curr_date = _get_row_val(matched_row, "due_date", 6, today_d) or today_d
            curr_time = _get_row_val(matched_row, "due_time", 3)
            curr_dur = _safe_int(_get_row_val(matched_row, "duration_minutes", 5, 30), default=30)

            new_title = (args.title or orig_title).strip()
            new_date = date.fromisoformat(args.due_date) if args.due_date else curr_date
            if isinstance(new_date, str):
                new_date = date.fromisoformat(new_date)

            new_time = curr_time
            if args.due_time:
                h, m = map(int, args.due_time.split(":")[:2])
                new_time = dtime(h, m)
            elif isinstance(new_time, str):
                parts = list(map(int, new_time.split(":")[:2]))
                new_time = dtime(parts[0], parts[1])

            new_dur = _safe_int(args.duration_minutes, default=curr_dur) if args.duration_minutes is not None else curr_dur

            # Mathematical Collision Check
            conflicts = []
            if new_time and not args.force:
                cand_start = new_time.hour * 60 + new_time.minute
                cand_end = cand_start + new_dur

                day_items = db.items_for_date(chat_id, new_date)
                for r in day_items:
                    if _get_row_val(r, "id", 4) == row_id:
                        continue
                    r_tm = _get_row_val(r, "due_time", 3)
                    if not r_tm:
                        continue
                    r_h = r_tm.hour if hasattr(r_tm, "hour") else int(str(r_tm).split(":")[0])
                    r_m = r_tm.minute if hasattr(r_tm, "minute") else int(str(r_tm).split(":")[1])
                    r_dur = _safe_int(_get_row_val(r, "duration_minutes", 5, 30), default=30)
                    ex_start = r_h * 60 + r_m
                    ex_end = ex_start + r_dur

                    # Pure mathematical interval collision: max(s1, s2) < min(e1, e2)
                    if max(cand_start, ex_start) < min(cand_end, ex_end):
                        r_title = _get_row_val(r, "title", 1, "Existing task")
                        h_end = ex_end // 60
                        m_end = ex_end % 60
                        conflicts.append(f"'{r_title}' ({r_h:02d}:{r_m:02d} - {h_end:02d}:{m_end:02d})")

                # Also check Google Calendar
                try:
                    from main import get_unified_schedule
                    sched = await get_unified_schedule(chat_id, new_date)
                    for ev in sched.get("calendar_events", []):
                        raw_start = ev.get("start", {}).get("dateTime")
                        if raw_start:
                            ev_dt = datetime.fromisoformat(raw_start.replace("Z", "+00:00")).astimezone(u_tz)
                            ev_raw_end = ev.get("end", {}).get("dateTime")
                            ev_end_dt = datetime.fromisoformat(ev_raw_end.replace("Z", "+00:00")).astimezone(u_tz) if ev_raw_end else (ev_dt + timedelta(minutes=45))
                            ev_summ = (ev.get("summary") or "").strip()
                            clean_ev = ev_summ.replace("🔔", "").replace("⏰", "").strip().lower()
                            if clean_ev == orig_title.lower() or orig_title.lower() in clean_ev:
                                continue
                            ev_s = ev_dt.hour * 60 + ev_dt.minute
                            ev_e = ev_end_dt.hour * 60 + ev_end_dt.minute
                            if max(cand_start, ev_s) < min(cand_end, ev_e):
                                conflicts.append(f"Calendar Event '{ev_summ}' ({ev_dt.strftime('%I:%M %p')} - {ev_end_dt.strftime('%I:%M %p')})")
                except Exception:
                    pass

            if conflicts and not args.force:
                time_disp = new_time.strftime("%I:%M %p") if new_time else "specified time"
                return {
                    "action": "update_reminder",
                    "tool_name": "update_reminder",
                    "status": "conflict_detected",
                    "has_conflict": True,
                    "conflict_type": "direct_overlap",
                    "conflicts": conflicts,
                    "title": new_title,
                    "due_date": new_date.isoformat(),
                    "due_time": args.due_time or (new_time.strftime("%H:%M") if new_time else None),
                    "duration_minutes": new_dur,
                    "message": f"Time Conflict: {time_disp} par already {', '.join(conflicts)} scheduled hai. Kya aap confirm karna chahenge ya iska time change karna hai?"
                }

            # Update database cleanly
            db.update_memory_item_full(chat_id, row_id, title=new_title, due_date=new_date, due_time=new_time, duration_minutes=new_dur)

            # Rebuild reminders
            try:
                from timing import build_plan
                from db import add_reminders
                with db.engine.begin() as conn:
                    conn.execute(db.text("DELETE FROM reminders WHERE item_id = :id"), {"id": row_id})
                if new_date and new_time:
                    plan = build_plan(new_date, new_time)
                    if plan.get("reminders"):
                        add_reminders(row_id, plan["reminders"])
            except Exception:
                pass

            # Invalidate prompt schedule cache immediately!
            try:
                from agent.prompt import invalidate_prompt_schedule_cache
                invalidate_prompt_schedule_cache(chat_id)
            except Exception:
                pass

            time_str = new_time.strftime("%I:%M %p") if new_time else "specified time"
            date_str = new_date.strftime("%d %b %Y") if new_date else "specified date"
            return {
                "action": "update_reminder",
                "tool_name": "update_reminder",
                "status": "success",
                "title": new_title,
                "due_date": new_date.isoformat(),
                "due_time": new_time.strftime("%H:%M") if new_time else None,
                "duration_minutes": new_dur,
                "rescheduled": True,
                "conflicts": conflicts,
                "has_conflict": len(conflicts) > 0,
                "message": f"Done! '{new_title}' ka reminder {date_str} ko {time_str} ({new_dur} min duration) ke liye update kar diya hai."
            }

        elif tool_name in ("create_reminder", "add_reminder"):
            args = CreateReminderArgs(**arguments)
            u_tz, _ = await _resolve_user_timezone(chat_id)
            today_d = datetime.now(u_tz).date()

            # Date resolution
            if not args.due_date:
                d_val = today_d
                args.due_date = d_val.isoformat()
            else:
                d_val = date.fromisoformat(args.due_date)

            # Time resolution
            t_val = None
            if args.due_time:
                h, m = map(int, args.due_time.split(":")[:2])
                t_val = dtime(h, m)
            else:
                fallback_text = (args.title or "")
                if user_context and isinstance(user_context, dict) and user_context.get("user_text"):
                    fallback_text += " " + user_context["user_text"]
                extracted_tm = _extract_time_from_text(fallback_text)
                if extracted_tm:
                    t_val = extracted_tm
                    args.due_time = f"{t_val.hour:02d}:{t_val.minute:02d}"

            dur_mins = _safe_int(args.duration_minutes, default=30)

            # Check if this is an explicit reschedule of an existing reminder
            existing_items = db.items_for_date(chat_id, d_val)
            clean_title = args.title.strip()
            target_low = clean_title.lower()
            matched_id = None
            generic_titles = {"meeting", "call", "reminder", "task", "event", "appointment", "class", "session"}

            user_msg = ""
            if user_context and isinstance(user_context, dict):
                user_msg = (user_context.get("user_text") or "").lower()

            is_explicit_reschedule = any(k in user_msg for k in ("shift", "reschedule", "badal", "change", "update", "move", "kardo"))

            # Only match existing item for overwrite if:
            # 1. User explicitly requested shift/reschedule/update, OR
            # 2. The title is non-generic (e.g. "Meeting with Devender")
            if is_explicit_reschedule or target_low not in generic_titles:
                for r in existing_items:
                    r_title = (_get_row_val(r, "title", 1, "") or "").strip().lower()
                    if target_low == r_title or (target_low not in generic_titles and ((len(target_low) >= 4 and target_low in r_title) or (len(r_title) >= 4 and r_title in target_low))):
                        matched_id = _get_row_val(r, "id", 4)
                        break

            # Pure Math Collision Check
            conflicts = []
            tight_buffers = []
            if t_val:
                cand_start = t_val.hour * 60 + t_val.minute
                cand_end = cand_start + dur_mins

                for r in existing_items:
                    if matched_id and _get_row_val(r, "id", 4) == matched_id:
                        continue
                    r_tm = _get_row_val(r, "due_time", 3)
                    if not r_tm:
                        continue
                    r_h = r_tm.hour if hasattr(r_tm, "hour") else int(str(r_tm).split(":")[0])
                    r_m = r_tm.minute if hasattr(r_tm, "minute") else int(str(r_tm).split(":")[1])
                    r_dur = _safe_int(_get_row_val(r, "duration_minutes", 5, 30), default=30)
                    ex_start = r_h * 60 + r_m
                    ex_end = ex_start + r_dur

                    # Pure mathematical interval collision: max(s1, s2) < min(e1, e2)
                    if max(cand_start, ex_start) < min(cand_end, ex_end):
                        r_title = _get_row_val(r, "title", 1, "Existing task")
                        h_end = ex_end // 60
                        m_end = ex_end % 60
                        conflicts.append(f"'{r_title}' ({r_h:02d}:{r_m:02d} - {h_end:02d}:{m_end:02d})")
                    else:
                        # Tight buffer checking
                        gap = min(abs(cand_start - ex_end), abs(ex_start - cand_end))
                        r_title = _get_row_val(r, "title", 1, "Existing task")
                        is_heavy_transit = any(k in clean_title.lower() for k in ("movie", "theatre", "flight", "bus", "train", "travel", "doctor"))
                        buffer_limit = 30 if is_heavy_transit else 20
                        if gap <= buffer_limit and (is_heavy_transit or (gap < 20 and any(k in (r_title + " " + clean_title).lower() for k in ("meeting", "sync", "call")))):
                            tight_buffers.append(f"'{r_title}' (gap sirf {gap} min hai)")

                # Also check Google Calendar
                try:
                    from main import get_unified_schedule
                    sched = await get_unified_schedule(chat_id, d_val)
                    for ev in sched.get("calendar_events", []):
                        raw_start = ev.get("start", {}).get("dateTime")
                        if raw_start and t_val:
                            ev_dt = datetime.fromisoformat(raw_start.replace("Z", "+00:00")).astimezone(u_tz)
                            ev_raw_end = ev.get("end", {}).get("dateTime")
                            ev_end_dt = datetime.fromisoformat(ev_raw_end.replace("Z", "+00:00")).astimezone(u_tz) if ev_raw_end else (ev_dt + timedelta(minutes=45))
                            ev_summ = (ev.get("summary") or "").strip()
                            clean_ev = ev_summ.replace("🔔", "").replace("⏰", "").strip().lower()
                            if clean_ev == target_low or target_low in clean_ev:
                                continue
                            ev_s = ev_dt.hour * 60 + ev_dt.minute
                            ev_e = ev_end_dt.hour * 60 + ev_end_dt.minute
                            if max(cand_start, ev_s) < min(cand_end, ev_e):
                                conflicts.append(f"Calendar Meeting '{ev_summ}' ({ev_dt.strftime('%I:%M %p')} - {ev_end_dt.strftime('%I:%M %p')})")
                except Exception:
                    pass

            if conflicts and not args.force and not matched_id:
                time_disp = t_val.strftime("%I:%M %p") if t_val else "specified time"
                return {
                    "action": "create_reminder",
                    "tool_name": "create_reminder",
                    "status": "conflict_detected",
                    "has_conflict": True,
                    "conflict_type": "direct_overlap",
                    "conflicts": conflicts,
                    "title": clean_title,
                    "due_date": args.due_date,
                    "due_time": args.due_time,
                    "duration_minutes": dur_mins,
                    "message": f"Time Conflict: {time_disp} par already {', '.join(conflicts)} scheduled hai. Kya aap iska time aage-peeche karna chahenge ya confirm karna chahenge?"
                }

            if tight_buffers and not args.force and not matched_id:
                time_disp = t_val.strftime("%I:%M %p") if t_val else "specified time"
                return {
                    "action": "create_reminder",
                    "tool_name": "create_reminder",
                    "status": "conflict_detected",
                    "has_conflict": True,
                    "conflict_type": "tight_buffer",
                    "conflicts": tight_buffers,
                    "title": clean_title,
                    "due_date": args.due_date,
                    "due_time": args.due_time,
                    "duration_minutes": dur_mins,
                    "message": f"Schedule Conflict / Tight Buffer: Aapki {', '.join(tight_buffers)}, aur '{clean_title}' {time_disp} par scheduled ho rahi hai. Dono ke beech transition gap bahut tight hai. Kya aap time shift karna chahenge?"
                }

            # If matched existing ID, update it cleanly
            if matched_id:
                db.update_memory_item_full(chat_id, matched_id, title=clean_title, due_date=d_val, due_time=t_val, duration_minutes=dur_mins)
                item_id = matched_id
                rescheduled = True
            else:
                item_id = db.save_memory(chat_id, args.category or 'appointments', clean_title, amount=None, due_date=d_val, due_time=t_val, duration_minutes=dur_mins)
                rescheduled = False

            # Add reminders
            try:
                from timing import build_plan
                from db import add_reminders
                if rescheduled:
                    with db.engine.begin() as conn:
                        conn.execute(db.text("DELETE FROM reminders WHERE item_id = :id"), {"id": item_id})
                if d_val and t_val:
                    plan = build_plan(d_val, t_val)
                    if plan.get("reminders") and item_id:
                        add_reminders(item_id, plan["reminders"])
            except Exception:
                pass

            # Sync to Google Calendar if connected
            sync_note = " (Telegram reminder set ✅)"
            try:
                from main import get_valid_google_connection_by_chat_id
                is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
                if is_conn and d_val and t_val:
                    import google_services
                    ev_start_dt = datetime.combine(d_val, t_val, tzinfo=u_tz)
                    ev_end_dt = ev_start_dt + timedelta(minutes=dur_mins)
                    await google_services.create_calendar_event(
                        chat_id,
                        summary=f"🔔 {clean_title}",
                        start_time=ev_start_dt.isoformat(),
                        end_time=ev_end_dt.isoformat(),
                        description=f"Auto-synced reminder from PeculiarAI ({dur_mins} mins)",
                    )
                    sync_note = " (Synced with Google Calendar & Telegram ✅)"
            except Exception:
                pass

            # Invalidate prompt schedule cache immediately!
            try:
                from agent.prompt import invalidate_prompt_schedule_cache
                invalidate_prompt_schedule_cache(chat_id)
            except Exception:
                pass

            time_str = t_val.strftime("%I:%M %p") if t_val else "specified time"
            date_str = d_val.strftime("%d %b %Y") if d_val else "specified date"
            action_word = "shift" if rescheduled else "set"
            return {
                "action": "create_reminder",
                "tool_name": "create_reminder",
                "status": "success",
                "title": clean_title,
                "due_date": args.due_date,
                "due_time": args.due_time,
                "duration_minutes": dur_mins,
                "rescheduled": rescheduled,
                "conflicts": conflicts,
                "has_conflict": len(conflicts) > 0,
                "message": f"Done bhai! '{clean_title}' ka reminder {date_str} ko {time_str} ke liye {action_word} kar diya hai.{sync_note}"
            }

        elif tool_name == "delete_reminder":
            kw = str(arguments.get("title", "")).strip().lower()
            rem_id = arguments.get("reminder_id")
            from db import canonical_chat_id, engine, text, items_for_date
            can_cid = canonical_chat_id(chat_id)

            clean_kw = kw.replace("*", "").replace("'", "").replace('"', "").strip()

            deleted_titles = []
            with engine.begin() as conn:
                if rem_id:
                    matched_rows = conn.execute(
                        text("SELECT id, title FROM memory_items WHERE id = :id AND user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c)"),
                        {"id": rem_id, "c": can_cid}
                    ).fetchall()
                elif clean_kw:
                    matched_rows = conn.execute(
                        text("SELECT id, title FROM memory_items WHERE LOWER(title) LIKE :pat AND user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c)"),
                        {"pat": f"%{clean_kw}%", "c": can_cid}
                    ).fetchall()
                    # Fallback token match if exact substring not found
                    if not matched_rows:
                        tokens = [t for t in clean_kw.split() if len(t) >= 3]
                        for tk in tokens:
                            rows = conn.execute(
                                text("SELECT id, title FROM memory_items WHERE LOWER(title) LIKE :pat AND user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c)"),
                                {"pat": f"%{tk}%", "c": can_cid}
                            ).fetchall()
                            if rows:
                                matched_rows = rows
                                break
                else:
                    matched_rows = []

                for r_id, r_title in matched_rows:
                    conn.execute(text("DELETE FROM reminders WHERE item_id = :id"), {"id": r_id})
                    conn.execute(text("DELETE FROM memory_items WHERE id = :id"), {"id": r_id})
                    deleted_titles.append(r_title)

            try:
                rules = db.get_user_rules(can_cid)
                for r in rules:
                    r_text = (r.get("rule") or "").lower()
                    if clean_kw in r_text or any(tk in r_text for tk in clean_kw.split() if len(tk) >= 3):
                        db.delete_user_rule(can_cid, r.get("id"))
            except Exception:
                pass

            try:
                from api_v1 import _invalidate_all_app_caches
                _invalidate_all_app_caches()
                from agent.prompt import invalidate_prompt_schedule_cache
                invalidate_prompt_schedule_cache(chat_id)
            except Exception:
                pass

            if deleted_titles:
                return {
                    "status": "success",
                    "action": "delete_reminder",
                    "tool_name": "delete_reminder",
                    "title": ", ".join(deleted_titles),
                    "deleted_count": len(deleted_titles),
                    "deleted_titles": deleted_titles,
                    "message": f"Done! Reminder '{', '.join(deleted_titles)}' successfully delete kar diya hai."
                }
            else:
                # If not found or empty title, fetch active reminders to guide user
                today_items = items_for_date(can_cid, datetime.now(IST).date())
                if today_items:
                    rem_lines = [f"• {it[1]}" + (f" ({it[3].strftime('%I:%M %p')})" if it[3] else "") for it in today_items]
                    active_hint = "\n\nAapke active reminders:\n" + "\n".join(rem_lines)
                else:
                    active_hint = "\n\nFilhaal aapka koi pending reminder nahi hai."

                if clean_kw:
                    msg = f"'{kw}' naam ka reminder nahi mila.{active_hint}"
                else:
                    msg = f"Aapko kaunsa reminder delete karna hai?{active_hint}"

                return {
                    "status": "not_found",
                    "action": "delete_reminder",
                    "title": kw or "None",
                    "message": msg
                }

        elif tool_name == "list_reminders":
            args = ListRemindersArgs(**arguments)
            if args.date_str:
                d_val = date.fromisoformat(args.date_str)
                items = db.items_for_date(chat_id, d_val)
                # items_for_date returns: (category, title, amount, due_time, id)
                formatted = [
                    {
                        "id": it[4],
                        "category": it[0],
                        "title": it[1],
                        "due_date": args.date_str,
                        "due_time": str(it[3]) if it[3] else None
                    }
                    for it in items
                ]
            else:
                items = db.list_memories(chat_id)
                # list_memories returns: (category, title, amount, due_date, due_time)
                formatted = [
                    {
                        "category": it[0],
                        "title": it[1],
                        "due_date": str(it[3]) if len(it) > 3 and it[3] else None,
                        "due_time": str(it[4]) if len(it) > 4 and it[4] else None
                    }
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

            st_clean = args.start_time.strip().lower()
            if ":" in st_clean:
                parts = st_clean.split(":")
                h = int("".join(c for c in parts[0] if c.isdigit()) or 17)
                m = int("".join(c for c in parts[1][:2] if c.isdigit()) or 0)
            else:
                digits = "".join(c for c in st_clean if c.isdigit())
                h = int(digits) if digits else 17
                m = 0
            if "pm" in st_clean and h < 12:
                h += 12
            elif "am" in st_clean and h == 12:
                h = 0
            activity_start_mins = h * 60 + m
            dur = args.duration_minutes or (180 if "movie" in (args.activity_type or "").lower() else 60)
            activity_end_mins = activity_start_mins + dur

            u_tz, _ = await _resolve_user_timezone(chat_id)
            conflicts = []
            for ev in sched.get("calendar_events", []):
                raw_dt = ev.get("start", {}).get("dateTime")
                if raw_dt:
                    try:
                        ev_dt = datetime.fromisoformat(raw_dt.replace("Z", "+00:00")).astimezone(u_tz)
                        ev_start_mins = ev_dt.hour * 60 + ev_dt.minute
                        ev_end_mins = ev_start_mins + 30
                        if (activity_start_mins < ev_end_mins) and (activity_end_mins > ev_start_mins):
                            conflicts.append(f"Meeting '{ev.get('summary', 'Meeting')}' at {ev_dt.strftime('%I:%M %p').lstrip('0')}")
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

            final_title = args.title or "Meeting"
            if final_title in ("Meeting", "meeting", "call"):
                try:
                    from state.active_context import get_active_event
                    act_ev = get_active_event(chat_id)
                    if act_ev and act_ev.get("title") and act_ev["title"] not in ("Meeting", "meeting"):
                        final_title = act_ev["title"]
                except Exception:
                    pass

            time_conflicts, day_commitments = await detect_meeting_conflicts(chat_id, args.start_iso, args.duration_minutes or 30)

            try:
                from state.active_context import set_active_event
                set_active_event(chat_id, {
                    "title": final_title,
                    "start_iso": args.start_iso,
                    "duration_minutes": args.duration_minutes or 30,
                    "type": "meeting"
                })
            except Exception:
                pass

            return {
                "status": "needs_confirmation",
                "action": "create_meeting",
                "title": final_title,
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
            u_tz, _ = await _resolve_user_timezone(chat_id)
            events = await google_services.list_calendar_events(chat_id, time_min_iso=datetime.now(u_tz).isoformat(), max_results=5)
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

        elif tool_name == "check_integrations_status":
            args = CheckIntegrationsStatusArgs(**arguments)
            s_key = (args.service or "").strip().lower()

            if s_key in ("notion",):
                notion_row = db.get_user_integration(chat_id, "notion")
                notion_ws = None
                if notion_row:
                    try:
                        cfg = json.loads(notion_row.get("config_data") or "{}")
                        notion_ws = cfg.get("workspace_name") or "Connected"
                    except Exception:
                        notion_ws = "Connected"
                if notion_row:
                    msg = f"✅ Aapka Notion account connected hai! (Workspace: {notion_ws})"
                else:
                    from notion_auth import is_notion_oauth_configured, build_notion_auth_url
                    frontend_connect = os.getenv("FRONTEND_CONNECT_URL", "https://bucolic-dieffenbachia-c20bd0.netlify.app/connect.html")
                    if is_notion_oauth_configured():
                        try:
                            auth_url = build_notion_auth_url(int(chat_id), source="tg")
                            msg = (
                                f"❌ Aapka Notion account abhi connect nahi hai.\n\n"
                                f"👉 1-Click Login: Is secure official link par click karke workspace authorize karein:\n"
                                f"{auth_url}\n\n"
                                f"Authorize karte hi aapka Notion workspace automatically link ho jayega!"
                            )
                        except Exception:
                            msg = f"❌ Aapka Notion account abhi connect nahi hai.\n\n👉 1-Click Connect Link:\n{frontend_connect}"
                    else:
                        msg = f"❌ Aapka Notion account abhi connect nahi hai.\n\n👉 1-Click Connect Link:\n{frontend_connect}"
                return {
                    "status": "success",
                    "service": "notion",
                    "connected": bool(notion_row),
                    "workspace": notion_ws,
                    "message": msg
                }

            if s_key in ("github", "git"):
                summary = db.get_user_integrations_summary(chat_id)
                gh_row = db.get_user_integration(chat_id, "github")
                gh_username = None
                if gh_row:
                    try:
                        cfg = json.loads(gh_row.get("config_data") or "{}")
                        gh_username = cfg.get("username")
                    except Exception:
                        pass
                if gh_username or summary.get("github", {}).get("connected"):
                    u_display = f"@{gh_username}" if gh_username else "Active"
                    msg = f"✅ Aapka GitHub account connected hai ({u_display})!"
                else:
                    msg = "❌ Aapka GitHub account abhi connect nahi hai. 'connect github' likhein ya auth link par approve karein."
                return {
                    "status": "success",
                    "service": "github",
                    "connected": bool(gh_username or summary.get("github", {}).get("connected")),
                    "username": gh_username,
                    "message": msg
                }

            if s_key in ("google", "gmail", "calendar", "drive", "email", "mail", "gmeet"):
                google_conn = False
                google_email = None
                try:
                    from main import get_valid_google_connection_by_chat_id
                    is_g_conn, g_email = await get_valid_google_connection_by_chat_id(chat_id)
                    google_conn = bool(is_g_conn)
                    if google_conn and g_email:
                        google_email = g_email if isinstance(g_email, str) else g_email.get("google_email")
                except Exception:
                    pass

                # Also check verified email_links if Google OAuth is not directly active
                if not google_conn or not google_email:
                    try:
                        with db.engine.connect() as conn:
                            el_row = conn.execute(
                                db.text("SELECT email FROM email_links WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) AND verified = true"),
                                {"cid": db.canonical_chat_id(chat_id)}
                            ).first()
                            if el_row and el_row[0]:
                                google_email = el_row[0]
                                google_conn = True
                    except Exception:
                        pass

                if google_conn and google_email:
                    msg = f"Aapka connected/linked email address: **{google_email}**"
                elif google_conn:
                    msg = "✅ Aapka Google account connected hai! (Calendar, Meet, Gmail, Drive active)"
                else:
                    msg = "Aapka koi bhi email address ya Google account abhi linked ya connect nahi hai. Email link karne ke liye bas apna email yahan bhej dein ya 'connect google' bol dein."
                return {
                    "status": "success",
                    "service": "google",
                    "connected": google_conn,
                    "email": google_email,
                    "message": msg
                }

            # If general summary requested, query all
            summary = db.get_user_integrations_summary(chat_id)
            google_conn = False
            google_email = None
            try:
                from main import get_valid_google_connection_by_chat_id
                is_g_conn, g_email = await get_valid_google_connection_by_chat_id(chat_id)
                google_conn = bool(is_g_conn)
                if google_conn and g_email:
                    google_email = g_email if isinstance(g_email, str) else g_email.get("google_email")
            except Exception:
                pass

            gh_row = db.get_user_integration(chat_id, "github")
            gh_username = None
            if gh_row:
                try:
                    cfg = json.loads(gh_row.get("config_data") or "{}")
                    gh_username = cfg.get("username")
                except Exception:
                    pass

            notion_row = db.get_user_integration(chat_id, "notion")
            notion_ws = None
            if notion_row:
                try:
                    cfg = json.loads(notion_row.get("config_data") or "{}")
                    notion_ws = cfg.get("workspace_name") or "Connected"
                except Exception:
                    notion_ws = "Connected"

            # All services summary
            lines = [
                "🔌 **Aapke Accounts / Integrations ka Status:**\n",
                f"• **GitHub:** {'✅ Connected (@' + gh_username + ')' if gh_username else ('✅ Connected' if summary.get('github', {}).get('connected') else '❌ Not connected')}",
                f"• **Notion:** {'✅ Connected (' + notion_ws + ')' if notion_row else '❌ Not connected'}",
                f"• **Google:** {'✅ Connected' + (f' ({google_email})' if google_email else '') if google_conn else '❌ Not connected'}"
            ]
            full_msg = "\n".join(lines)

            status = {
                "status": "success",
                "message": full_msg,
                "github": {
                    "connected": bool(summary.get("github", {}).get("connected") or gh_username),
                    "username": gh_username,
                    "details": summary.get("github", {}).get("details", "")
                },
                "notion": {
                    "connected": bool(notion_row),
                    "workspace": notion_ws,
                    "details": f"Connected ({notion_ws})" if notion_row else "Not connected"
                },
                "google": {
                    "connected": google_conn,
                    "email": google_email,
                    "services": ["Google Calendar", "Google Meet", "Gmail", "Google Drive"] if google_conn else []
                }
            }
            return status

        elif tool_name == "connect_integration":
            args = ConnectIntegrationArgs(**arguments)
            svc = args.service.strip().lower()
            val = (args.value or "connect").strip()

            if svc in ("google", "gmail", "calendar", "google_calendar", "drive", "google_drive"):
                from main import get_valid_google_connection_by_chat_id
                is_g_conn, g_email = await get_valid_google_connection_by_chat_id(chat_id)
                if is_g_conn:
                    return {
                        "status": "success",
                        "service": "google",
                        "connected": True,
                        "email": g_email,
                        "message": (
                            f"✅ Aapka Google account **pehle se connected hai** ({g_email})!\n\n"
                            f"Google Calendar, Meet, Gmail aur Drive active hain. Aapko dobara connect karne ki zaroorat nahi hai."
                        )
                    }
                from google_auth import build_auth_url
                try:
                    auth_url = build_auth_url(int(chat_id), source="tg")
                    return {
                        "status": "success",
                        "service": "google",
                        "auth_url": auth_url,
                        "message": (
                            f"Google account connect karne ke liye is secure link par click karke login & approve karein:\n\n"
                            f"{auth_url}\n\n"
                            f"Approve hote hi Google Calendar, Meet aur Drive automatically ready ho jayenge!"
                        )
                    }
                except Exception as exc:
                    return {"status": "error", "message": f"Could not generate Google auth URL: {exc}"}

            elif svc == "github":
                from github_auth import is_github_oauth_configured, build_github_auth_url
                clean_user = str(val or "").strip()

                if clean_user.startswith("ghp_") or clean_user.startswith("github_pat_"):
                    is_valid, login = await integrations.validate_github_token(clean_user)
                    if is_valid:
                        db.save_user_integration(chat_id, "github", clean_user, json.dumps({"username": login}))
                        return {
                            "status": "success",
                            "service": "github",
                            "username": login,
                            "message": f"GitHub personal token validated! Account @{login} connected successfully."
                        }
                    else:
                        return {"status": "error", "message": f"Invalid GitHub token: {login}"}

                if is_github_oauth_configured():
                    try:
                        auth_url = build_github_auth_url(int(chat_id), source="tg")
                        return {
                            "status": "success",
                            "service": "github",
                            "auth_url": auth_url,
                            "message": (
                                f"GitHub account connect karne ke liye is secure official link par click karke authorize karein:\n\n"
                                f"👉 {auth_url}\n\n"
                                f"Authorize karte hi aapke repositories ka access aur write/commit permission automatically ready ho jayegi!"
                            )
                        }
                    except Exception as exc:
                        pass

                if "github.com/" in clean_user:
                    parts = clean_user.split("github.com/")[-1].strip("/").split("/")
                    if parts:
                        clean_user = parts[0]
                clean_user = clean_user.lstrip("@").strip()

                if clean_user.startswith("ghp_") or clean_user.startswith("github_pat_"):
                    is_valid, login = await integrations.validate_github_token(clean_user)
                    if is_valid:
                        db.save_user_integration(chat_id, "github", clean_user, json.dumps({"username": login}))
                        return {
                            "status": "success",
                            "service": "github",
                            "username": login,
                            "message": f"GitHub personal token validated! Account @{login} connected successfully."
                        }
                    else:
                        return {"status": "error", "message": f"Invalid GitHub token: {login}"}

                # Public profile username check
                import httpx
                async with httpx.AsyncClient(timeout=10.0) as client:
                    gh_res = await client.get(
                        f"https://api.github.com/users/{clean_user}",
                        headers={"User-Agent": "PeculiarAI", "Accept": "application/vnd.github.v3+json"}
                    )
                    if gh_res.status_code == 200:
                        u_info = gh_res.json()
                        login = u_info.get("login") or clean_user
                        public_repos = u_info.get("public_repos", 0)
                        db.save_user_integration(
                            chat_id,
                            "github",
                            os.getenv("GITHUB_TOKEN", "public_access"),
                            json.dumps({"username": login, "profile_url": f"https://github.com/{login}", "public_repos": public_repos})
                        )
                        return {
                            "status": "success",
                            "service": "github",
                            "username": login,
                            "public_repos": public_repos,
                            "message": f"GitHub account @{login} connected successfully! Found {public_repos} public repositories."
                        }
                    else:
                        return {
                            "status": "error",
                            "message": f"GitHub user '{clean_user}' not found (Status {gh_res.status_code})."
                        }

            elif svc == "notion":
                from notion_auth import is_notion_oauth_configured, build_notion_auth_url
                clean_val = val.strip()
                if clean_val.startswith("secret_") or clean_val.startswith("ntn_"):
                    is_valid, name = await integrations.validate_notion_token(clean_val)
                    if is_valid:
                        db.save_user_integration(chat_id, "notion", clean_val, json.dumps({"workspace_name": name}))
                        return {
                            "status": "success",
                            "service": "notion",
                            "workspace": name,
                            "message": f"✅ Notion workspace '{name}' connected successfully! Ab aap summaries ya notes Notion me add kar sakte hain."
                        }
                existing_row = db.get_user_integration(chat_id, "notion")
                if existing_row:
                    existing_ws = "Connected"
                    try:
                        cfg = json.loads(existing_row.get("config_data") or "{}")
                        existing_ws = cfg.get("workspace_name") or "Connected"
                    except Exception:
                        pass
                    return {
                        "status": "success",
                        "service": "notion",
                        "workspace": existing_ws,
                        "message": f"✅ Aapka Notion workspace '{existing_ws}' already connected hai! Ab aap notes ya meeting summaries Notion me add kar sakte hain."
                    }

                if is_notion_oauth_configured():
                    try:
                        auth_url = build_notion_auth_url(int(chat_id), source="tg")
                        return {
                            "status": "success",
                            "service": "notion",
                            "auth_url": auth_url,
                            "message": (
                                f"Notion workspace connect karne ke liye is secure official link par click karke login & authorize karein:\n\n"
                                f"👉 {auth_url}\n\n"
                                f"Authorize karte hi aapka Notion workspace automatically link ho jayega!"
                            )
                        }
                    except Exception:
                        pass

                frontend_connect = os.getenv("FRONTEND_CONNECT_URL", "https://bucolic-dieffenbachia-c20bd0.netlify.app/connect.html")
                return {
                    "status": "success",
                    "service": "notion",
                    "message": (
                        f"Notion workspace connect karne ke liye niche diye gaye 1-click link par jayein:\n\n"
                        f"👉 {frontend_connect}\n\n"
                        f"Authorize karte hi aapka Notion workspace link ho jayega!"
                    )
                }

        elif tool_name == "disconnect_integration":
            args = DisconnectIntegrationArgs(**arguments)
            svc = args.service.strip().lower()
            if svc in ("google", "gmail", "calendar", "drive"):
                db.delete_google_token(chat_id)
                return {"status": "success", "service": "google", "message": "✅ Aapka Google account successfully disconnect kar diya gaya hai."}
            elif svc == "github":
                db.delete_user_integration(chat_id, "github")
                return {"status": "success", "service": "github", "message": "✅ Aapka GitHub account successfully disconnect kar diya gaya hai."}
            elif svc == "notion":
                db.delete_user_integration(chat_id, "notion")
                return {"status": "success", "service": "notion", "message": "✅ Aapka Notion workspace successfully disconnect kar diya gaya hai."}
            elif svc == "all":
                db.delete_google_token(chat_id)
                db.delete_user_integration(chat_id, "github")
                db.delete_user_integration(chat_id, "notion")
                return {"status": "success", "service": "all", "message": "✅ Sabhi accounts (Google, GitHub, Notion) successfully disconnect kar diye gaye hain."}
            else:
                return {"status": "error", "message": f"Unknown service '{svc}'. Supported services: google, github, notion, all."}

        elif tool_name == "create_notion_page":
            title = arguments.get("title", "")
            content = arguments.get("content", "")
            target_section = arguments.get("target_section")
            formatting = arguments.get("formatting")

            if not title or not content:
                return {"status": "error", "message": "Missing 'title' or 'content' for Notion page."}

            token_row = db.get_user_integration(chat_id, "notion")
            api_key = token_row.get("api_key") if isinstance(token_row, dict) else (token_row if isinstance(token_row, str) else None)
            if not api_key:
                api_key = os.getenv("NOTION_API_KEY")

            if not api_key:
                return {
                    "status": "error",
                    "message": "Notion is not connected. Type 'connect notion' ya apna Notion token ('ntn_...' ya 'secret_...') bhej kar connect karein."
                }

            ok, msg, pg_url = await integrations.create_or_append_notion_note(
                token=api_key,
                title=title,
                content=content,
                target_section=target_section,
                formatting=formatting
            )
            if ok:
                link_str = f"\n\n🔗 Link: {pg_url}" if pg_url else ""
                return {
                    "status": "success",
                    "title": title,
                    "url": pg_url or "",
                    "message": f"✅ {msg}{link_str}"
                }
            else:
                return {"status": "error", "message": msg}

        elif tool_name in ("update_notion_page", "append_notion_page"):
            content = arguments.get("content", "")
            page_title_or_id = arguments.get("page_title_or_id") or arguments.get("target_section")
            section_title = arguments.get("section_title")
            formatting = arguments.get("formatting")

            if not content:
                return {"status": "error", "message": "Missing 'content' to append to Notion page."}

            token_row = db.get_user_integration(chat_id, "notion")
            api_key = token_row.get("api_key") if isinstance(token_row, dict) else (token_row if isinstance(token_row, str) else None)
            if not api_key:
                api_key = os.getenv("NOTION_API_KEY")

            if not api_key:
                return {
                    "status": "error",
                    "message": "Notion is not connected. Type 'connect notion' ya apna Notion token ('ntn_...' ya 'secret_...') bhej kar connect karein."
                }

            if not page_title_or_id:
                try:
                    from state.active_context import get_active_context
                    act_ctx = get_active_context(chat_id)
                    if act_ctx and act_ctx.get("connector", "").lower() == "notion":
                        page_title_or_id = act_ctx.get("url") or act_ctx.get("topic")
                except Exception:
                    pass

            ok, msg, pg_url = await integrations.append_to_notion_page(
                token=api_key,
                content=content,
                page_id_or_title=page_title_or_id,
                section_title=section_title,
                formatting=formatting
            )
            if ok:
                link_str = f"\n\n🔗 Link: {pg_url}" if pg_url else ""
                return {
                    "status": "success",
                    "url": pg_url or "",
                    "message": f"✅ {msg}{link_str}"
                }
            else:
                return {"status": "error", "message": msg}

        elif tool_name == "list_notion_pages":
            query = arguments.get("query")
            token_row = db.get_user_integration(chat_id, "notion")
            api_key = token_row.get("api_key") if isinstance(token_row, dict) else (token_row if isinstance(token_row, str) else None)
            if not api_key:
                api_key = os.getenv("NOTION_API_KEY")

            if not api_key:
                return {
                    "status": "error",
                    "message": "Notion account is not connected. Type 'connect notion' to connect your workspace."
                }

            pages = await integrations.list_notion_pages(token=api_key, query=query)
            if not pages:
                return {
                    "status": "success",
                    "count": 0,
                    "pages": [],
                    "message": "Aapke Notion workspace me koi pages ya notes nahi mile."
                }

            lines = [f"📚 **Aapke Notion Pages & Notes ({len(pages)}):**\n"]
            for p in pages:
                lines.append(f"• **{p['title']}**\n  🔗 Link: {p['url']}")

            return {
                "status": "success",
                "count": len(pages),
                "pages": pages,
                "message": "\n\n".join(lines)
            }

        elif tool_name == "get_notion_page_content":
            page_id_or_title = arguments.get("page_id_or_title") or arguments.get("title") or arguments.get("page")
            token_row = db.get_user_integration(chat_id, "notion")
            api_key = token_row.get("api_key") if isinstance(token_row, dict) else (token_row if isinstance(token_row, str) else None)
            if not api_key:
                api_key = os.getenv("NOTION_API_KEY")

            if not api_key:
                return {
                    "status": "error",
                    "message": "Notion account is not connected. Type 'connect notion' to connect your workspace."
                }

            ok, content, pg_url = await integrations.get_notion_page_content(api_key, page_id_or_title)
            if ok:
                link_str = f"\n\n🔗 Link: {pg_url}" if pg_url else ""
                return {
                    "status": "success",
                    "content": content,
                    "url": pg_url or "",
                    "message": f"{content}{link_str}"
                }
            else:
                return {"status": "error", "message": content}

        elif tool_name == "list_github_repos":
            args = ListGitHubReposArgs(**arguments)
            req_username = args.username

            # Check if user has saved GitHub username in user_integrations
            token_row = db.get_user_integration(chat_id, "github")
            token = token_row if isinstance(token_row, str) else (token_row.get("api_key") if isinstance(token_row, dict) else None)
            saved_username = None
            if isinstance(token_row, dict):
                try:
                    cfg = json.loads(token_row.get("config_data") or "{}")
                    saved_username = cfg.get("username")
                except Exception:
                    pass

            if not req_username and saved_username:
                req_username = saved_username

            # Check recent history for any github.com/username
            if not req_username:
                recent_history = db.get_recent_chat_history(chat_id, limit=6)
                for h in reversed(recent_history):
                    txt = h.get("text", "")
                    if "github.com/" in txt:
                        parts = txt.split("github.com/")[1].split()
                        if parts:
                            req_username = parts[0].strip("/ ,.!?()[]{}")
                            break

            # Fallback to system token
            if not token:
                token = os.getenv("GITHUB_TOKEN")

            success, repos, resolved_user = await integrations.list_github_repos(token=token, username=req_username)
            if not success and req_username:
                success, repos, resolved_user = await integrations.list_github_repos(token=None, username=req_username)

            if not success:
                return {"status": "error", "message": "Could not fetch repositories from GitHub."}

            repo_names = [r.get("name") for r in repos if isinstance(r, dict)]
            return {
                "status": "success",
                "username": resolved_user or req_username,
                "count": len(repo_names),
                "repositories": repo_names
            }

        elif tool_name == "get_github_file":
            args = GetGitHubFileArgs(**arguments)
            req_repo = args.repo
            req_path = args.path or "README.md"
            req_username = args.username

            token_row = db.get_user_integration(chat_id, "github")
            token = token_row if isinstance(token_row, str) else (token_row.get("api_key") if isinstance(token_row, dict) else None)
            saved_username = None
            if isinstance(token_row, dict):
                try:
                    cfg = json.loads(token_row.get("config_data") or "{}")
                    saved_username = cfg.get("username")
                except Exception:
                    pass

            if not req_username and saved_username:
                req_username = saved_username

            if not req_username:
                try:
                    from state.active_context import get_active_context
                    act = get_active_context(chat_id)
                    if act and act.get("github_username"):
                        req_username = act.get("github_username")
                except Exception:
                    pass

            if not token:
                token = os.getenv("GITHUB_TOKEN")

            success, file_data = await integrations.get_github_file(
                token=token,
                username=req_username,
                repo=req_repo,
                file_path=req_path
            )
            if not success and req_username:
                success, file_data = await integrations.get_github_file(
                    token=None,
                    username=req_username,
                    repo=req_repo,
                    file_path=req_path
                )

            if not success:
                return {
                    "status": "error",
                    "repo": req_repo,
                    "message": file_data.get("message") or file_data.get("error") or f"Could not inspect file '{req_path}' in repository '{req_repo}'."
                }

            file_data["status"] = "success"
            return file_data

        elif tool_name == "update_github_file":
            args = UpdateGitHubFileArgs(**arguments)
            req_repo = args.repo
            req_path = args.path or "README.md"
            req_content = args.content
            commit_msg = args.commit_message or args.message or "Update file via PeculiarAI"
            req_username = args.username

            if not req_repo:
                try:
                    from state.active_context import get_active_context
                    act = get_active_context(chat_id)
                    if act and act.get("focus_topic") and "repo:" in act.get("focus_topic"):
                        req_repo = act.get("focus_topic").split("repo:")[-1].strip()
                except Exception:
                    pass
            if not req_repo:
                recent = db.get_recent_chat_history(chat_id, limit=6)
                for t in reversed(recent):
                    txt = t.get("text", "")
                    for tok in txt.split():
                        tok_clean = tok.strip("`'\"()[],;").lower()
                        if "/" in tok_clean and len(tok_clean.split("/")) == 2 and not tok_clean.startswith("http"):
                            req_repo = tok_clean
                            break
                        if "-project" in tok_clean or "-repo" in tok_clean:
                            req_repo = tok_clean
                            break
                    if req_repo:
                        break

            if not req_content:
                recent = db.get_recent_chat_history(chat_id, limit=6)
                for t in reversed(recent):
                    txt = t.get("text", "")
                    if "<parameter=content>" in txt and "</parameter>" in txt:
                        req_content = txt.split("<parameter=content>")[1].split("</parameter>")[0].strip()
                        break
                    if "```" in txt:
                        parts = txt.split("```")
                        if len(parts) >= 3:
                            req_content = parts[1].lstrip("markdown\n").strip()
                            break

            if not req_repo:
                req_repo = "php-project"

            if not req_content:
                req_content = "# Project\nUpdated README via PeculiarAI"

            token_row = db.get_user_integration(chat_id, "github")
            token = token_row if isinstance(token_row, str) else (token_row.get("api_key") if isinstance(token_row, dict) else None)
            saved_username = None
            if isinstance(token_row, dict):
                try:
                    cfg = json.loads(token_row.get("config_data") or "{}")
                    saved_username = cfg.get("username")
                except Exception:
                    pass

            if not req_username and saved_username:
                req_username = saved_username

            if not req_username:
                try:
                    from state.active_context import get_active_context
                    act = get_active_context(chat_id)
                    if act and act.get("github_username"):
                        req_username = act.get("github_username")
                except Exception:
                    pass

            if req_username and "/" not in req_repo:
                req_repo = f"{req_username}/{req_repo}"

            if not token:
                token = os.getenv("GITHUB_TOKEN")

            success, msg, commit_url = await integrations.update_github_file(
                token=token,
                repo=req_repo,
                file_path=req_path,
                content=req_content,
                commit_message=commit_msg,
                username=req_username
            )

            if not success:
                return {"status": "error", "repo": req_repo, "message": msg}

            try:
                from state.active_context import update_active_context
                update_active_context(chat_id, connector="GitHub", topic=f"repo: {req_repo}", objects=[req_path])
            except Exception:
                pass

            return {
                "status": "success",
                "repo": req_repo,
                "path": req_path,
                "message": msg,
                "commit_url": commit_url
            }

        elif tool_name == "list_github_issues":
            args = ListGitHubIssuesArgs(**arguments)
            req_repo = args.repo
            token_row = db.get_user_integration(chat_id, "github")
            token = token_row if isinstance(token_row, str) else (token_row.get("api_key") if isinstance(token_row, dict) else None)
            saved_username = None
            if isinstance(token_row, dict):
                try:
                    cfg = json.loads(token_row.get("config_data") or "{}")
                    saved_username = cfg.get("username")
                except Exception:
                    pass

            if req_repo and "/" not in req_repo and saved_username:
                req_repo = f"{saved_username}/{req_repo}"

            if not token:
                token = os.getenv("GITHUB_TOKEN")

            success, issues = await integrations.list_github_issues(token=token, repo=req_repo)
            if not success:
                return {"status": "error", "message": f"Could not fetch issues for '{req_repo or 'account'}'."}

            issue_items = [
                {"number": iss.get("number"), "title": iss.get("title"), "url": iss.get("html_url")}
                for iss in issues if isinstance(iss, dict)
            ]
            return {
                "status": "success",
                "repo": req_repo or "user",
                "count": len(issue_items),
                "issues": issue_items
            }

        elif tool_name == "generate_pdf":
            args = GeneratePdfArgs(**arguments)
            pdf_bytes = build_styled_pdf(args.title, [{"heading": "Summary", "text": args.content}])
            return {"status": "ready_for_dispatch", "type": "pdf", "filename": f"{args.title.replace(' ', '_')}.pdf", "bytes": len(pdf_bytes)}

        elif tool_name == "generate_excel":
            args = GenerateExcelArgs(**arguments)
            contacts = db.get_contact_memories(chat_id)
            excel_bytes = build_contacts_excel(contacts)
            return {"status": "ready_for_dispatch", "type": "excel", "filename": "contacts.xlsx", "bytes": len(excel_bytes)}

        elif tool_name == "search_user_emails":
            args = SearchUserEmailsArgs(**arguments)
            from main import get_valid_google_connection_by_chat_id
            is_conn, g_email = await get_valid_google_connection_by_chat_id(chat_id)
            from google_auth import build_auth_url
            auth_url = build_auth_url(int(chat_id), source="tg")

            if not is_conn:
                return {
                    "status": "not_connected",
                    "connected": False,
                    "auth_url": auth_url,
                    "message": f"Aapka Google account abhi connected nahi hai. Connect karne ke liye is link par tap karein:\n\n👉 {auth_url}"
                }

            # Check if this chat is known to lack Gmail read scope
            if int(chat_id) in getattr(google_services, "_CHAT_GMAIL_INSUFFICIENT_SCOPES", set()):
                return {
                    "status": "needs_permission",
                    "connected": True,
                    "email": g_email,
                    "auth_url": auth_url,
                    "message": (
                        f"Aapka Google account pehle se connected hai ({g_email}), lekin **Gmail Read permission** missing hai.\n\n"
                        f"Naye emails, tickets aur booking confirmations auto-check karne ke liye is link par tap karke allow karein:\n\n"
                        f"👉 {auth_url}\n\n"
                        f"Allow karte hi main aapke tickets aur incoming emails automatically read karne lagunga!"
                    )
                }

            user_q = (args.query or "").strip()
            gmail_query = user_q if user_q else "in:inbox"
            messages = await google_services.fetch_user_inbox_messages(chat_id, max_results=args.max_results or 5, query=gmail_query)
            if not messages and gmail_query != "in:inbox":
                messages = await google_services.fetch_user_inbox_messages(chat_id, max_results=args.max_results or 5, query="in:inbox")

            if int(chat_id) in getattr(google_services, "_CHAT_GMAIL_INSUFFICIENT_SCOPES", set()):
                return {
                    "status": "needs_permission",
                    "connected": True,
                    "email": g_email,
                    "auth_url": auth_url,
                    "message": (
                        f"Aapka Google account connected hai ({g_email}), lekin **Gmail Read permission** missing hai.\n\n"
                        f"Naye emails aur tickets automatically check karne ke liye is link par tap karke allow karein:\n\n"
                        f"👉 {auth_url}\n\n"
                        f"Allow karte hi main aapke tickets aur incoming emails automatically read karne lagunga!"
                    )
                }

            results = []
            from email_intelligence import analyze_incoming_email
            for m in messages:
                analysis = await analyze_incoming_email(m.get("sender", ""), m.get("subject", ""), m.get("body", ""))
                results.append({
                    "id": m.get("id"),
                    "sender": m.get("sender"),
                    "subject": m.get("subject"),
                    "date": m.get("date"),
                    "is_important": analysis.get("is_important", False),
                    "category": analysis.get("category", "general"),
                    "alert_text": analysis.get("alert_text"),
                    "details": analysis.get("raw_data") or {},
                    "body_snippet": m.get("body", "")[:1000]
                })

                if analysis.get("is_important") and analysis.get("category") == "ticket_booking":
                    raw_d = analysis.get("raw_data") or {}
                    pnr = raw_d.get("pnr") or ""
                    route = raw_d.get("route") or ""
                    dt = raw_d.get("datetime_str") or ""
                    carrier = raw_d.get("carrier") or "Travel Provider"
                    seats = raw_d.get("seats") or ""
                    passengers = raw_d.get("passengers") or ""
                    t_fact = f"Booking/Ticket: {carrier} | PNR: {pnr} | Route: {route} | Date: {dt} | Seats: {seats} | Passengers: {passengers}"
                    try:
                        db.save_user_fact(chat_id, t_fact, category="booking")
                        from state.active_context import set_active_ticket
                        set_active_ticket(chat_id, raw_d)
                    except Exception as c_err:
                        print("Error caching ticket:", c_err)

            saved_facts = db.get_user_facts(chat_id) or []
            booking_facts = [f for f in saved_facts if any(k in f.get("fact", "").lower() for k in ("booking", "ticket", "pnr", "train", "flight"))]

            return {
                "status": "success",
                "count": len(results),
                "emails": results,
                "saved_booking_facts": [f.get("fact") for f in booking_facts],
                "message": f"Found {len(results)} email(s) in connected mailbox."
            }

        elif tool_name == "ask_user":
            args = AskUserArgs(**arguments)
            return {"status": "clarification_needed", "question": args.question, "options": args.options}

        elif tool_name == "get_weather":
            args = GetWeatherArgs(**arguments)
            from utilities import get_weather
            res = await get_weather(args.city, chat_id=chat_id)
            return res

        elif tool_name == "convert_currency":
            args = ConvertCurrencyArgs(**arguments)
            from utilities import convert_currency
            res = await convert_currency(args.amount, args.from_currency, args.to_currency or "INR")
            return res

        elif tool_name == "get_market_price":
            args = GetMarketPriceArgs(**arguments)
            item_lower = (args.item or "").lower().strip()
            valid_commodities = ("gold", "silver", "bitcoin", "ethereum", "solana", "sona", "chandi", "btc", "eth", "sol", "crypto", "24k", "22k")
            if not any(k in item_lower for k in valid_commodities):
                return {
                    "status": "not_applicable",
                    "success": False,
                    "message": f"'{args.item}' is not a supported financial commodity or cryptocurrency. For travel costs, tolls, fares, or everyday pricing, answer the user directly with general knowledge without calling this tool."
                }
            from utilities import get_market_price
            res = await get_market_price(args.item, args.currency or "inr")
            return res

        elif tool_name == "calculate":
            args = CalculateArgs(**arguments)
            from utilities import calculate
            res = calculate(args.expression)
            return res

        elif tool_name == "deep_research":
            args = DeepResearchArgs(**arguments)
            from agent.deep_research import run_deep_research
            res = await run_deep_research(args.topic, args.goal)
            return res

        elif tool_name == "learn_user_correction":
            args = LearnCorrectionArgs(**arguments)
            ok = db.save_user_correction(chat_id, args.wrong_concept, args.correct_rule, args.category or "user_feedback")
            return {
                "status": "success" if ok else "error",
                "learned_rule": args.correct_rule,
                "message": f"Understood and permanently recorded: '{args.correct_rule}'"
            }

        elif tool_name == "find_nearby_places":
            args = FindNearbyPlacesArgs(**arguments)
            from utilities import find_nearby_places
            res = await find_nearby_places(args.category, args.area, args.city or "Jodhpur")
            return res

        elif tool_name == "get_movie_showtimes":
            args = GetMovieShowtimesArgs(**arguments)
            from utilities import get_movie_showtimes
            res = await get_movie_showtimes(args.city or "Jodhpur", args.area or "Sardarpura", args.movie)
            return res

        elif tool_name in ("search_web", "web_search"):
            q = arguments.get("query") or arguments.get("q") or ""
            n = arguments.get("num_results") or arguments.get("max_results") or 5
            from utilities import search_web
            res = await search_web(q, max_results=int(n))
            return res

        elif tool_name in ("read_webpage", "fetch_url"):
            u = arguments.get("url") or ""
            m_chars = arguments.get("max_chars") or 4000
            from utilities import read_webpage
            res = await read_webpage(u, max_chars=int(m_chars))
            return res

        elif tool_name == "manage_commitment":
            args = ManageCommitmentArgs(**arguments)
            from memory.store import (
                resolve_user_id, create_commitment, get_commitments, update_commitment_status
            )
            uid = resolve_user_id(chat_id)
            if args.action == "create":
                if not args.title:
                    return {"status": "error", "message": "Title is required to create a commitment."}
                cid = create_commitment(
                    user_id=uid,
                    title=args.title,
                    description=args.description,
                    target_date=args.target_date,
                    condition_type=args.condition_type or "date",
                    condition_details=args.condition_details
                )
                return {
                    "status": "success",
                    "commitment_id": cid,
                    "title": args.title,
                    "message": f"Commitment '{args.title}' successfully recorded with ID #{cid}."
                }
            elif args.action == "list":
                items = get_commitments(user_id=uid, status=args.status)
                return {"status": "success", "commitments": items, "count": len(items)}
            elif args.action in ("complete", "cancel", "update"):
                if not args.commitment_id:
                    return {"status": "error", "message": "commitment_id is required to update status."}
                st = "completed" if args.action == "complete" else ("cancelled" if args.action == "cancel" else (args.status or "completed"))
                ok = update_commitment_status(args.commitment_id, uid, status=st)
                return {
                    "status": "success" if ok else "error",
                    "commitment_id": args.commitment_id,
                    "new_status": st,
                    "message": f"Commitment #{args.commitment_id} marked as {st}."
                }
            else:
                return {"status": "error", "message": f"Unknown action: '{args.action}'"}

        elif tool_name == "manage_relationship":
            action = arguments.get("action", "list")
            from services.relationship_service import add_or_update_relationship, get_user_relationships, resolve_relationship_in_text
            if action == "add":
                name = arguments.get("name") or "Person"
                rel = arguments.get("relationship") or "Contact"
                res = add_or_update_relationship(
                    user_id_or_chat_id=chat_id,
                    name=name,
                    relationship=rel,
                    phone=arguments.get("phone"),
                    birthday=arguments.get("birthday"),
                    notes=arguments.get("notes")
                )
                return res
            elif action == "get":
                name = arguments.get("name") or ""
                matches = resolve_relationship_in_text(chat_id, name)
                return {"status": "success", "matches": matches, "count": len(matches)}
            else:
                items = get_user_relationships(chat_id)
                return {"status": "success", "relationships": items, "count": len(items)}

        elif tool_name == "manage_subscription":
            action = arguments.get("action", "list")
            from services.subscription_service import upsert_subscription, get_user_subscriptions, check_expiring_subscriptions
            if action == "add":
                res = upsert_subscription(
                    user_id_or_chat_id=chat_id,
                    service_name=arguments.get("service_name") or "Subscription",
                    amount=arguments.get("amount"),
                    billing_cycle=arguments.get("billing_cycle") or "monthly",
                    next_renewal_date=arguments.get("renewal_date"),
                    is_trial=arguments.get("is_trial") or False,
                    trial_expiry_date=arguments.get("renewal_date") if arguments.get("is_trial") else None,
                    cancel_url=arguments.get("cancel_url")
                )
                return res
            elif action == "check_expiring":
                exp = check_expiring_subscriptions(chat_id, days_ahead=7)
                return {"status": "success", "expiring": exp, "count": len(exp)}
            else:
                items = get_user_subscriptions(chat_id)
                return {"status": "success", "subscriptions": items, "count": len(items)}

        elif tool_name == "get_memory_health_radar":
            from services.health_radar import compute_memory_health_score
            score_data = compute_memory_health_score(chat_id)
            return {"status": "success", "radar": score_data}

        else:
            return {"status": "error", "message": f"Unknown tool: '{tool_name}'"}

    except ValidationError as val_err:
        return {"status": "error", "type": "ValidationError", "details": val_err.errors()}
    except Exception as exc:
        return {"status": "error", "type": type(exc).__name__, "message": str(exc)}


CORE_TOOL_NAMES = {
    "execute_python_code", "create_reminder", "update_reminder", "delete_reminder", "list_reminders", "get_schedule",
    "save_note_or_fact", "search_memory", "save_contact", "get_contact", "list_contacts", "get_commitments",
    "connect_integration", "disconnect_integration", "check_integrations_status", "create_meeting",
    "search_web", "find_nearby_places"
}

_DOMAIN_TOOL_EMBED_CACHE: Dict[str, List[float]] = {}

def _cosine_similarity(a: Optional[List[float]], b: Optional[List[float]]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5
    norm_b = sum(y * y for y in b) ** 0.5
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)

def is_action_or_tool_intent(user_text: str) -> bool:
    """
    Returns True ONLY if the user is asking the assistant to take an action,
    execute an external integration, check live schedule/calendar, or query tools.
    For general chat, learning, explanations, questions, coding, or chit-chat, returns False.
    """
    low = user_text.lower().strip()
    service_keywords = (
        'weather', 'github', 'repo', 'notion', 'email', 'gmail', 'inbox', 'pdf', 'excel',
        'remind', 'reminder', 'meeting', 'calendar', 'schedule', 'contact', 'packing list',
        'delete', 'cancel', 'hatao', 'hata do', 'save', 'store', 'likh lo', 'glucose', 'sugar',
        'blood', 'reading', 'smart list', 'checklist', 'wipe', 'memory', 'dlt'
    )
    if any(sk in low for sk in service_keywords):
        return True

    edu_indicators = (
        'difference', 'diffrnc', 'difrnc', 'farq', 'kya farq', 'kya antar', 'explain', 'samjhao', 'smjhao',
        'detail m', 'detail mein', 'kya hota hai', 'kya h', 'what is', 'what are',
        'how does', 'why is', 'code', 'python', 'script', 'meaning', 'define', 'concept', 'batao'
    )
    if any(e in low for e in edu_indicators):
        return False

    return False


def route_tools(user_text: str) -> List[Dict[str, Any]]:
    """
    Intelligent Dynamic Tool Routing:
    - Core baseline tools are ALWAYS provided (Reminders, Schedule, Contacts, Memory, Integrations, Places, Web).
    - Relevant domain tools (GitHub, Notion, Email, Weather, Lists, Finance) are dynamically attached based on user intent.
    - Zero RAM overhead: eliminates cold-start blocking loops.
    """
    core_tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in CORE_TOOL_NAMES]
    low = (user_text or "").lower().strip()

    # Fast-path: Greetings and short casual phrases need only core tools (0ms)
    if not low or low in ("hi", "hello", "hey", "hlo", "hii", "namaste", "ping", "test", "ok", "okay", "bye") or (len(low.split()) == 1 and low in ("hi", "hello", "hey", "hlo", "hii")):
        return core_tools

    domain_tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] not in CORE_TOOL_NAMES]
    extra_tools = []

    for dt in domain_tools:
        fname = dt["function"]["name"]
        if fname in ("list_github_repos", "get_github_file", "update_github_file", "list_github_issues") and any(w in low for w in ("git", "github", "repo", "readme", "commit", "issue", "pr")):
            extra_tools.append(dt)
        elif fname in ("create_notion_page", "update_notion_page", "list_notion_pages", "get_notion_page_content") and any(w in low for w in ("notion", "workspace", "notes section", "note", "notes", "code block", "heading")):
            extra_tools.append(dt)
        elif fname in ("send_email", "search_user_emails") and any(w in low for w in ("mail", "email", "gmail", "inbox", "sent")):
            extra_tools.append(dt)
        elif fname in ("get_weather",) and any(w in low for w in ("weather", "mausam", "temperature", "rain", "barish", "cold", "garmi")):
            extra_tools.append(dt)
        elif fname in ("convert_currency", "get_market_price", "calculate") and any(w in low for w in ("currency", "dollar", "usd", "inr", "convert", "gold", "silver", "bitcoin", "crypto", "calc", "%", "hisaab")):
            extra_tools.append(dt)
        elif fname in ("request_master_privacy_wipe", "clear_chat_history") and any(w in low for w in ("wipe", "clear chat", "delete my data", "saari memory", "clear memory")):
            extra_tools.append(dt)
        elif fname in ("manage_smart_list",) and any(w in low for w in ("list", "smart list", "grocery", "saman", "items")):
            extra_tools.append(dt)
        elif fname in ("get_movie_showtimes",) and any(w in low for w in ("movie", "cinema", "showtime", "theatre")):
            extra_tools.append(dt)
        elif fname in ("create_packing_list",) and any(w in low for w in ("packing", "luggage", "travel list", "pack")):
            extra_tools.append(dt)
        elif fname in ("get_measurements", "get_memory_health_radar") and any(w in low for w in ("sugar", "glucose", "bp", "blood pressure", "health", "reading")):
            extra_tools.append(dt)
        elif fname in ("check_availability", "get_meet_link", "update_meeting", "delete_meeting") and any(w in low for w in ("meeting", "meet", "slot", "free slot", "availability", "clash")):
            extra_tools.append(dt)

    return core_tools + extra_tools
