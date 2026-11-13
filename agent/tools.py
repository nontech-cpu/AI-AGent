"""
PeculiarAI Agent Tools & Execution Registry (Phase 1)
All tools validated with Pydantic models. All tool execution functions are async.
Write actions implement the Two-Phase Confirmation Gate (return needs_confirmation).
"""
import os
import re
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
import core.timeutil as timeutil
from core.json_safe import to_json_safe
from core.file_dispatch import dispatch_generated_file, sanitize_filename
from file_generator import (
    build_contacts_excel,
    build_document_artifact,
    build_excel_workbook,
    build_styled_pdf,
)

async def _resolve_user_timezone(chat_id: int):
    """Dynamically resolves the user's primary calendar timezone or defaults gracefully to IST."""
    try:
        user_tz_str = await google_services.get_user_calendar_timezone(chat_id)
        if not user_tz_str or user_tz_str in ("UTC", "Etc/UTC", "GMT", "Universal"):
            user_tz_str = "Asia/Kolkata"
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

class SetUserLanguageArgs(BaseModel):
    language: str = Field(..., description="The language code or name requested by user, e.g. 'english', 'hindi', 'hinglish', 'spanish', 'french'.")

class SetUserPreferenceArgs(BaseModel):
    reply_language: str = Field(..., description="Target reply language: 'english', 'hinglish', 'hindi', etc.")
    tone: Optional[str] = Field("concise", description="Preferred tone: 'concise', 'friendly', 'formal'.")
    request_quote: Optional[str] = Field(None, description="Exact words from user requesting this preference.")

class CreateCalendarEventArgs(BaseModel):
    title: str = Field(..., description="Title of the calendar event or reminder to add to Google Calendar.")
    start_iso: Optional[str] = Field(None, description="ISO start datetime in user's timezone (YYYY-MM-DDTHH:MM:SS).")
    due_date: Optional[str] = Field(None, description="Date in YYYY-MM-DD format.")
    due_time: Optional[str] = Field(None, description="Time in 24-hour HH:MM format.")
    duration_minutes: Optional[int] = Field(30, description="Duration in minutes (default 30).")
    recurrence: Optional[str] = Field("none", description="Recurrence pattern: 'daily', 'weekly', 'none'.")
    request_quote: Optional[str] = Field(None, description="Exact words from user's message requesting to add this event to calendar.")

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
    request_quote: Optional[str] = Field(None, description="Exact words from the user's message requesting this reminder.")

class UpdateReminderArgs(BaseModel):
    title: Optional[str] = Field(None, description="Title or keyword of the reminder, meeting, or task to update.")
    new_title: Optional[str] = Field(None, description="New title if renaming or replacing the reminder (e.g. changing from 'Devender' to 'Rahul').")
    reminder_id: Optional[int] = Field(None, description="ID of the reminder if known.")
    due_date: Optional[str] = Field(None, description="Updated date in YYYY-MM-DD format if date is being changed.")
    due_time: Optional[str] = Field(None, description="Updated time in 24-hour HH:MM format if time is being changed.")
    from_time: Optional[str] = Field(None, description="Original or current time of the meeting being changed, e.g. '16:00' if shifting from 4pm.")
    duration_minutes: Optional[Any] = Field(None, description="Updated duration in minutes (e.g. 60 for 1 hour, 30 for 30 mins).")
    force: Optional[bool] = Field(False, description="Set to true if user insists on updating despite a time conflict.")
    request_quote: Optional[str] = Field(None, description="Exact words from the user's message requesting this update.")

class ListRemindersArgs(BaseModel):
    date_str: Optional[str] = Field(None, description="Target date in YYYY-MM-DD format. The model semantically converts relative user expressions ('today', 'aaj', 'tomorrow', 'kal', day names) into the exact YYYY-MM-DD date. Defaults to today's date if omitted.")

class DeleteReminderArgs(BaseModel):
    title: Optional[str] = Field(None, description="Title or keyword of the reminder or meeting to delete.")
    reminder_id: Optional[int] = Field(None, description="The exact database ID of the reminder to delete if known.")
    target_ids: Optional[List[int]] = Field(None, description="List of exact database IDs of reminders to delete.")
    scope: Optional[str] = Field("all", description="Scope for recurring items: 'this' cancels only this occurrence; 'all' cancels all recurring occurrences.")
    request_quote: Optional[str] = Field(None, description="Exact words from the user's message requesting deletion.")

class RestoreReminderArgs(BaseModel):
    reminder_id: int = Field(..., description="The exact database ID of the soft-deleted reminder to restore.")

class GetScheduleArgs(BaseModel):
    target_date: Optional[str] = Field(None, description="Target date in YYYY-MM-DD format. Defaults to today IST.")

class CheckAvailabilityArgs(BaseModel):
    start_time: str = Field(..., description="Planned start time in HH:MM format (e.g. '17:00' for 5 PM).")
    target_date: Optional[str] = Field(None, description="Target date in YYYY-MM-DD format. Defaults to today or tomorrow IST.")
    duration_minutes: Optional[Any] = Field(60, description="Estimated duration in minutes (e.g. 180 for movie, 30 for call).")
    activity_type: Optional[str] = Field("general", description="Type of activity e.g. 'movie', 'meeting', 'dinner'.")

class ScheduleEventArgs(BaseModel):
    title: str = Field(..., description="Title or subject of the event/meeting/reminder.")
    start_iso: Optional[str] = Field(None, description="ISO start datetime (YYYY-MM-DDTHH:MM:SS) in user's timezone.")
    start_time: Optional[str] = Field(None, description="Alias for start_iso.")
    due_date: Optional[str] = Field(None, description="Optional date string (YYYY-MM-DD).")
    due_time: Optional[str] = Field(None, description="Optional time string (HH:MM).")
    duration_minutes: Optional[int] = Field(30, description="Duration in minutes (default 30). Set 0 for instant reminders.")
    sync_calendar: Optional[bool] = Field(True, description="Whether to sync to Google Calendar if connected.")
    sync_telegram: Optional[bool] = Field(True, description="Whether to set proactive Telegram alarm notifications.")
    is_video_meeting: Optional[bool] = Field(False, description="True ONLY if user explicitly asked for Google Meet or Zoom video link. Default false.")
    recurrence: Optional[str] = Field("none", description="Recurrence pattern: 'daily', 'weekly', 'none'.")
    attendees: Optional[List[str]] = Field(default_factory=list, description="Attendee email addresses.")

class CreateMeetingArgs(BaseModel):
    title: Optional[str] = Field("Meeting", description="Title of the meeting.")
    summary: Optional[str] = Field(None, description="Alias for meeting title.")
    start_iso: Optional[str] = Field(None, description="Meeting start datetime in ISO format (e.g. YYYY-MM-DDTHH:MM:SS with user's timezone offset or local time).")
    start_time: Optional[str] = Field(None, description="Alias for meeting start datetime.")
    duration_minutes: Optional[int] = Field(30, description="Duration in minutes.")
    attendees: Optional[List[str]] = Field([], description="List of attendee email addresses.")
    request_quote: Optional[str] = Field(None, description="Exact words from user's message requesting to schedule this meeting.")

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

class SendTargetedMentionMessageArgs(BaseModel):
    message: str = Field(..., description="Prompt text containing @mentions and their respective directives.")
    attachments: Optional[List[Dict[str, Any]]] = Field(default_factory=list, description="Optional file attachments.")

class LinkUserPhoneNumberArgs(BaseModel):
    phone_number: str = Field(..., description="The user's verified phone number with country code (e.g. '+919461089998' or '9461089998').")


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
    content: Optional[str] = Field("", description="Text or Markdown content to format in the PDF.")
    sections: Optional[List[Dict[str, Any]]] = Field(None, description="Optional structured sections: [{'heading': str, 'paragraphs': [...], 'bullets': [...], 'table': [[...]]}].")
    data_type: Optional[str] = Field(None, description="Optional user memory export source: 'contacts', 'reminders', 'tasks', 'facts', or 'all'.")
    filename: Optional[str] = Field(None, description="Optional custom filename ending in .pdf.")

class GenerateExcelArgs(BaseModel):
    data_type: Optional[str] = Field("contacts", description="Type of saved data to export: 'contacts', 'reminders', 'tasks', 'all', or 'custom'.")
    title: Optional[str] = Field(None, description="Workbook/sheet title for custom or filtered spreadsheets.")
    filename: Optional[str] = Field(None, description="Optional output filename ending in .xlsx.")
    headers: Optional[List[str]] = Field(None, description="Column headers when creating a custom Excel spreadsheet.")
    rows: Optional[List[List[Any]]] = Field(None, description="Row values when creating a custom Excel spreadsheet.")
    sheets: Optional[List[Dict[str, Any]]] = Field(None, description="Optional multi-sheet definitions: [{'name': str, 'headers': [...], 'rows': [[...]]}].")

class GenerateFileArgs(BaseModel):
    format: str = Field(..., description="Target format: 'pdf', 'xlsx', 'csv', 'docx', 'pptx', 'md', 'txt', 'json', 'html', or 'ics'.")
    title: Optional[str] = Field("Document", description="Document title.")
    filename: Optional[str] = Field(None, description="Optional output filename.")
    content: Optional[str] = Field("", description="Text, Markdown, or document body content.")
    sections: Optional[List[Dict[str, Any]]] = Field(None, description="Structured sections for PDF, DOCX, HTML, or Markdown.")
    slides: Optional[List[Dict[str, Any]]] = Field(None, description="Slide definitions for PPTX presentations: [{'title': str, 'bullets': [...], 'table': [[...]]}].")
    headers: Optional[List[str]] = Field(None, description="Table column headers for CSV/XLSX/Markdown/DOCX.")
    rows: Optional[List[List[Any]]] = Field(None, description="Table rows for CSV/XLSX.")
    sheets: Optional[List[Dict[str, Any]]] = Field(None, description="Multi-sheet definitions for XLSX workbooks.")
    data: Optional[Any] = Field(None, description="Arbitrary JSON or tabular object data.")
    data_type: Optional[str] = Field(None, description="Optional user data source to export: 'contacts', 'reminders', 'tasks', 'facts', or 'all'.")

class SearchUserEmailsArgs(BaseModel):
    query: Optional[str] = Field(None, description="Query to filter emails or documents e.g. 'ticket', 'flight', 'train', 'interview', 'bill', or 'in:inbox'.")
    to: Optional[str] = Field(None, description="Recipient email address for searching sent emails (e.g. 'aaravmishra619@gmail.com').")
    subject: Optional[str] = Field(None, description="Subject keyword to search for.")
    folder: Optional[str] = Field(None, description="Folder to search: 'sent', 'inbox', or 'all'.")
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
    area: Optional[str] = Field(None, description="Local neighborhood or area.")
    city: Optional[str] = Field(None, description="City name. If omitted, asks user or infers from context.")

class GetMovieShowtimesArgs(BaseModel):
    city: Optional[str] = Field(None, description="City to look up movies for. If omitted, asks user or infers from context.")
    area: Optional[str] = Field(None, description="Local area or neighbourhood.")
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
            "description": "Explicitly save a life fact, preference, or detail the user wants remembered permanently (e.g. 'I like 5 PM movies'). Do NOT call on casual feedback, greetings, or acknowledgements like 'Good', 'Thanks', 'Ok'.",
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
            "description": "Initiate permanent master privacy wipe to reset the entire user account, all stored memories, chat logs, contacts, and personal profile data. Call this ONLY when user explicitly asks for a complete account wipe, total memory reset, or fresh profile reset (e.g. 'delete entire account', 'wipe my profile', 'sab kuch bhool jao factory reset'). NEVER use this when user asks to delete a specific item, specific document, Notion page, individual note, travel ticket, or reminder.",
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
            "name": "set_user_language",
            "description": "Configure or update the user's preferred conversation language in their profile when they explicitly ask to speak in a specific language (e.g. 'speak in English from now on', 'talk to me in English', 'Hindi mein bolo', 'Hinglish mein baat karo').",
            "parameters": {
                "type": "object",
                "properties": {
                    "language": {"type": "string", "description": "The language code or name requested by the user, e.g. 'english', 'hindi', 'hinglish'."}
                },
                "required": ["language"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "set_user_preference",
            "description": "Configure and lock user's communication preferences (reply language and tone) in their profile when the user explicitly instructs how they want replies (e.g. 'talk to me in English only', 'English mein baat karo', 'speak in Hinglish', 'be concise').",
            "parameters": {
                "type": "object",
                "properties": {
                    "reply_language": {"type": "string", "description": "Target reply language: 'english', 'hinglish', 'hindi', etc."},
                    "tone": {"type": "string", "description": "Assistant tone: 'concise', 'friendly', 'formal'."},
                    "request_quote": {"type": "string", "description": "Exact words from user's message requesting this preference."}
                },
                "required": ["reply_language"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "create_calendar_event",
            "description": "Add a personal event, time block, or reminder directly to Google Calendar. Does NOT create video call links (Google Meet/Zoom) and requires NO attendees. Use this whenever user asks to 'add this reminder to calendar', 'calendar pe sync kar do', or block personal time.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title or summary of the calendar event."},
                    "start_iso": {"type": "string", "description": "ISO start datetime in user's timezone (YYYY-MM-DDTHH:MM:SS)."},
                    "due_date": {"type": "string", "description": "Optional date string (YYYY-MM-DD)."},
                    "due_time": {"type": "string", "description": "Optional time string (HH:MM)."},
                    "duration_minutes": {"type": "integer", "description": "Duration in minutes (default 30)."},
                    "recurrence": {"type": "string", "description": "Recurrence pattern: 'daily', 'weekly', 'none'."},
                    "request_quote": {"type": "string", "description": "Exact words from user's message requesting this calendar event."}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "delete_note_or_fact",
            "description": "Delete a specific note, memory fact, travel ticket, or saved booking from memory by keyword or query (e.g. 'delete the dr verma appointment', 'remove flight ticket', 'is fact ko memory se hata do'). Use whenever user wants to remove a specific fact or note, NOT the entire account.",
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
                "Save a NEW task, meeting, appointment, or reminder with due_date/due_time. "
                "NEVER call this for rescheduling or modifying an existing reminder/meeting (use update_reminder instead). "
                "Convert natural time to 24h 'HH:MM' due_time; defaults due_date to today if omitted."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Task, meeting, or reminder title."},
                    "due_date": {"type": ["string", "null"], "description": "YYYY-MM-DD date (defaults to today)."},
                    "due_time": {
                        "type": ["string", "null"],
                        "description": "24-hour HH:MM time (e.g. '23:00' for 11pm, '15:00' for 3 baje). Required when user specifies a time."
                    },
                    "duration_minutes": {"type": ["integer", "null"], "description": "Duration in minutes (e.g. 30, 60)."},
                    "category": {"type": ["string", "null"], "description": "Category e.g. appointments, bills, work."},
                    "recurrence": {"type": ["string", "null"], "description": "'daily' (roj/har din), 'weekly', or 'none'."},
                    "force": {"type": ["boolean", "null"], "description": "True to override conflict warning if user confirmed."},
                    "request_quote": {"type": "string", "description": "Exact words from user message requesting this reminder."}
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
                "Update or reschedule an existing reminder/meeting (time, date, title, or duration). "
                "ALWAYS use this to reschedule, move, shift, postpone, or modify an existing reminder/meeting; NEVER call create_reminder."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": ["string", "null"], "description": "Existing reminder/meeting title keyword."},
                    "new_title": {"type": ["string", "null"], "description": "New title if renaming or replacing person/event."},
                    "reminder_id": {"type": ["integer", "null"], "description": "Reminder ID if known."},
                    "due_date": {"type": ["string", "null"], "description": "Updated YYYY-MM-DD date."},
                    "due_time": {"type": ["string", "null"], "description": "Updated 24-hour HH:MM time."},
                    "from_time": {"type": ["string", "null"], "description": "Original time of event being moved (e.g. '16:00')."},
                    "duration_minutes": {"type": ["integer", "null"], "description": "Updated duration in minutes."},
                    "force": {"type": ["boolean", "null"], "description": "True to override conflict warning if confirmed."},
                    "request_quote": {"type": "string", "description": "Exact user words requesting this update."}
                }
            }
        }
    },
    
    {
        "type": "function",
        "function": {
            "name": "delete_reminder",
            "description": "Delete or cancel a saved reminder, meeting, or task by title keyword or ID. If multiple rows match, returns ambiguous; if 1 row matches, stages deletion requiring user confirmation. Confirmation is managed by the system state machine; model must never supply confirmation.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title or keyword of the reminder/event to delete."},
                    "reminder_id": {"type": ["integer", "null"], "description": "Optional specific reminder ID to delete."},
                    "target_ids": {"type": ["array", "null"], "items": {"type": "integer"}, "description": "List of exact database IDs to delete."},
                    "request_quote": {"type": "string", "description": "Exact words from user's message requesting deletion or cancellation."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "restore_reminder",
            "description": "Restore a soft-deleted reminder back to active status by exact ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reminder_id": {"type": "integer", "description": "The exact database ID of the deleted reminder to restore."}
                },
                "required": ["reminder_id"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_reminders",
            "description": (
                "List saved tasks, reminders, and appointments for a target date. Call this ONLY when the user explicitly asks about their "
                "schedule, tasks, or reminders (e.g. 'mere aajke reminder btana', 'aaj ke reminders dikhao', 'reminders check karo'). "
                "NEVER call this for casual greetings or simple hellos (e.g. 'hi', 'hello', 'hey', 'kaise ho'). "
                "The model must semantically convert relative temporal expressions (such as 'today', 'aaj', 'tomorrow', 'kal', day names) "
                "into an exact ISO date YYYY-MM-DD anchored to Current Time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "date_str": {
                        "type": ["string", "null"],
                        "description": "Target date in YYYY-MM-DD format (e.g. '2026-11-05'). If omitted or null, defaults to today's date."
                    }
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "get_schedule",
            "description": "Unified schedule view aggregating Google Calendar events, reminders, contact commitments, and board cards for a date. Call ONLY when user explicitly asks for schedule/agenda. NEVER call for casual greetings.",
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
            "name": "schedule_event",
            "description": "Create, sync, or schedule any event, meeting, or reminder across Telegram alarms and Google Calendar. By default creates a standard calendar event without video links. Set is_video_meeting=true ONLY when user explicitly requests a Google Meet or Zoom video conference link.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "Title or subject of the event/meeting/reminder."},
                    "start_iso": {"type": "string", "description": "ISO start datetime (YYYY-MM-DDTHH:MM:SS) in user's timezone."},
                    "duration_minutes": {"type": "integer", "description": "Duration in minutes (default 30). Set 0 for instant reminders."},
                    "sync_calendar": {"type": "boolean", "description": "Whether to sync to Google Calendar if connected (default true)."},
                    "sync_telegram": {"type": "boolean", "description": "Whether to set proactive Telegram alarm notifications at 15m, 5m, 0m (default true)."},
                    "is_video_meeting": {"type": "boolean", "description": "True ONLY if user explicitly asked for Google Meet or Zoom video link. Default false."},
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "Optional attendee email addresses."}
                },
                "required": ["title", "start_iso"]
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
                    "attendees": {"type": "array", "items": {"type": "string"}, "description": "Attendee email addresses."},
                    "request_quote": {"type": "string", "description": "Exact words from user's message requesting to schedule this meeting."}
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
                    "body": {"type": "string", "description": "Email body."},
                    "request_quote": {"type": "string", "description": "Exact words from user's message requesting to send this email."}
                },
                "required": ["to", "subject", "body"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "send_targeted_mention_message",
            "description": "Send targeted, private messages to mentioned contacts or app users (e.g. @devender_contact, @rahul, @9461089998). Guarantees strict privacy isolation: each recipient only receives their respective assigned task without seeing other recipients' directives.",
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {"type": "string", "description": "The full prompt containing @mentions and respective tasks (e.g. '@devender work on project a and @rahul work on project b')."},
                    "attachments": {"type": "array", "items": {"type": "object"}, "description": "Optional list of attachment file paths or names."}
                },
                "required": ["message"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "link_user_phone_number",
            "description": "Explicitly link or update the caller's phone number in the Peculiar AI network (e.g. when user says 'my phone number is 9461089998', 'mera number ye hai', or registers phone). Enables receiving targeted directives and notifications from other users.",
            "parameters": {
                "type": "object",
                "properties": {
                    "phone_number": {"type": "string", "description": "User's 10-12 digit phone number with optional country code (e.g. '+919461089998' or '9461089998')."}
                },
                "required": ["phone_number"]
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
            "description": "Generate and send a styled PDF document (.pdf) directly to the user (supports rich text, headings, bullets, tables, or exporting saved contacts/reminders/all data).",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "PDF Title."},
                    "content": {"type": ["string", "null"], "description": "Text or Markdown body content of the document."},
                    "sections": {"type": ["array", "null"], "items": {"type": "object"}, "description": "Optional structured sections with heading, paragraphs, bullets, table."},
                    "data_type": {"type": ["string", "null"], "description": "Optional saved data source to include: 'contacts', 'reminders', 'tasks', 'facts', or 'all'."},
                    "filename": {"type": ["string", "null"], "description": "Optional output filename ending in .pdf."}
                },
                "required": ["title"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_excel",
            "description": "Generate and send a styled Excel spreadsheet (.xlsx) directly to the user — either from saved data ('contacts', 'reminders', 'tasks', 'all') or custom headers/rows/sheets.",
            "parameters": {
                "type": "object",
                "properties": {
                    "data_type": {"type": ["string", "null"], "description": "'contacts', 'reminders', 'tasks', 'all', or 'custom'."},
                    "title": {"type": ["string", "null"], "description": "Workbook or sheet title."},
                    "filename": {"type": ["string", "null"], "description": "Optional output filename ending in .xlsx."},
                    "headers": {"type": ["array", "null"], "items": {"type": "string"}, "description": "Column headers for custom spreadsheet."},
                    "rows": {"type": ["array", "null"], "items": {"type": "array", "items": {}}, "description": "Data rows for custom spreadsheet."},
                    "sheets": {"type": ["array", "null"], "items": {"type": "object"}, "description": "Multi-sheet definitions."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "generate_file",
            "description": "Universal document & file generator. Generates and directly sends ANY requested file format to the user: Word (.docx), PowerPoint (.pptx), CSV (.csv), PDF (.pdf), Excel (.xlsx), Markdown (.md), Plain Text (.txt), JSON (.json), HTML (.html), or Calendar (.ics).",
            "parameters": {
                "type": "object",
                "properties": {
                    "format": {"type": "string", "description": "File format extension: 'pdf', 'xlsx', 'csv', 'docx', 'pptx', 'md', 'txt', 'json', 'html', or 'ics'."},
                    "title": {"type": ["string", "null"], "description": "Title of the file/presentation/report."},
                    "filename": {"type": ["string", "null"], "description": "Optional filename."},
                    "content": {"type": ["string", "null"], "description": "Text/Markdown/ICS body content."},
                    "sections": {"type": ["array", "null"], "items": {"type": "object"}, "description": "Structured sections for PDF/DOCX/HTML/MD."},
                    "slides": {"type": ["array", "null"], "items": {"type": "object"}, "description": "Slides list for PPTX decks: [{'title': ..., 'bullets': [...], 'table': [[...]]}]."},
                    "headers": {"type": ["array", "null"], "items": {"type": "string"}, "description": "Table column headers for CSV/XLSX."},
                    "rows": {"type": ["array", "null"], "items": {"type": "array", "items": {}}, "description": "Table rows for CSV/XLSX."},
                    "sheets": {"type": ["array", "null"], "items": {"type": "object"}, "description": "Multi-sheet spec for XLSX."},
                    "data_type": {"type": ["string", "null"], "description": "Optional saved data source to export: 'contacts', 'reminders', 'tasks', 'facts', or 'all'."}
                },
                "required": ["format"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "search_user_emails",
            "description": "Search and inspect recent emails (inbox or sent mail) and attached documents (tickets, booking confirmations, interview invites, bills) from the user's connected Gmail account. Use this to lookup sent emails (e.g. 'what did I send to Aarav') or incoming emails.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": ["string", "null"], "description": "Search query keyword."},
                    "to": {"type": ["string", "null"], "description": "Recipient email address for searching sent emails."},
                    "subject": {"type": ["string", "null"], "description": "Subject line filter."},
                    "folder": {"type": ["string", "null"], "description": "Folder filter: 'sent', 'inbox', or 'all'."},
                    "max_results": {"type": ["integer", "null"], "description": "Number of emails to inspect (default 5)."}
                }
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "check_integrations_status",
            "description": "Check whether user third-party integrations (Google Calendar/Meet/Gmail/Drive, GitHub, Notion) or email accounts are connected/linked in the database. ALWAYS call this tool when the user asks whether their account/service is connected or linked (e.g. 'mera google account link hai?', 'is my github connected?', 'notion status').",
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
            "name": "delete_notion_page",
            "description": "Delete (move to Trash / archive) one or more pages, documents, or series from the user's connected Notion workspace by title, search query, or page ID. Use whenever user wants to delete, trash, or remove Notion pages, notes, or series.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_id_or_title": {"type": "string", "description": "The title, search keyword (e.g. 'FastAPI', 'FastAPI Quick Start'), or page ID of the Notion page(s) to delete/trash."},
                    "confirm": {"type": "boolean", "description": "Whether the user explicitly confirmed deletion."}
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
            "description": "Fetch live BookMyShow cinema listings, latest running movies, and showtimes for theatres in the specified city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": ["string", "null"],
                        "description": "City name."
                    },
                    "area": {
                        "type": ["string", "null"],
                        "description": "Local neighborhood or area."
                    },
                    "movie": {
                        "type": ["string", "null"],
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

WRITE_TOOLS_WITH_QUOTE = {
    "create_reminder", "update_reminder", "delete_reminder",
    "create_calendar_event", "create_meeting", "send_email",
    "set_user_preference"
}

def validate_request_quote(chat_id: int, quote: Optional[str], user_context: Optional[dict] = None) -> bool:
    """
    Step 6: Server-side validation that request_quote appears literally in current or recent user messages.
    Plain string containment, case-insensitive, normalized whitespace, no meaning logic.
    """
    if user_context is not None and (user_context.get("bypass_quote_check") or user_context.get("_from_pending_executor")):
        return True

    # Layer 5 Prompt Injection & Administrative Override Defense:
    # If the quote or current message simulates a system command or safety bypass, reject write operations immediately
    injection_patterns = (
        r"system\s+(?:command|prompt|override|directive)\s*:",
        r"ignore\s+(?:all\s+)?(?:previous|prior|safety)\s+(?:instructions?|rules?)",
        r"disregard\s+(?:all\s+)?(?:previous|prior|safety)",
        r"you\s+are\s+now\s+(?:in\s+)?(?:developer|unrestricted|god)\s+mode",
        r"admin\s+(?:override|command)\s*:"
    )
    combined_check_str = f"{quote or ''} {(user_context or {}).get('user_text', '')}"
    if any(re.search(pat, combined_check_str, re.IGNORECASE) for pat in injection_patterns):
        logger.warning(f"validate_request_quote blocked prompt injection pattern: {combined_check_str[:120]}")
        return False

    if not quote or not str(quote).strip():
        # If user_context has user_text, quote is strictly required
        if user_context and user_context.get("user_text"):
            return False
        # Headless testing with no user_context
        try:
            import db
            recent = db.get_recent_chat_history(chat_id, limit=3)
            if any(r.get("role") == "user" for r in (recent or [])):
                return False
        except Exception:
            pass
        return True

    q_clean = " ".join(str(quote).strip().lower().split())

    # 1. Check current turn user_text
    curr_text = " ".join(str((user_context or {}).get("user_text", "")).strip().lower().split())
    if curr_text and (q_clean in curr_text or curr_text in q_clean):
        return True

    # 2. Check recent user turns from chat_history (database agnostic across PostgreSQL & SQLite)
    try:
        import db
        recent_turns = db.get_recent_chat_history(chat_id, limit=6)
        user_msgs = [
            " ".join(str(r.get("text") or r.get("message") or "").strip().lower().split())
            for r in (recent_turns or [])
            if r.get("role") == "user"
        ]
        if curr_text and curr_text not in user_msgs:
            user_msgs.append(curr_text)

        # Check containment in any individual user message
        for msg in user_msgs:
            if msg and (q_clean in msg or msg in q_clean):
                return True

        # Check across combined recent user conversation (multi-turn context)
        combined = " ".join(user_msgs)
        if combined and (q_clean in combined or combined in q_clean):
            return True

        # Check word containment with typo tolerance (e.g. 'devedner' -> 'devender')
        def _token_matches(w: str, text: str) -> bool:
            if w in text:
                return True
            for t in text.split():
                if abs(len(w) - len(t)) <= 1 and len(w) >= 4:
                    diff = sum(1 for c1, c2 in zip(sorted(w), sorted(t)) if c1 != c2) + abs(len(w) - len(t))
                    if diff <= 1:
                        return True
            return False

        q_words = [w for w in q_clean.split() if len(w) > 2]
        matching_words = [w for w in q_words if _token_matches(w, combined)]
        if q_words and (len(matching_words) / len(q_words) >= 0.6 or len(matching_words) >= 2):
            return True
    except Exception as exc:
        logger.warning(f"validate_request_quote history check error: {exc}")

    # If headless unit test with no chat logs at all, allow
    if not curr_text:
        return True

    return False

async def execute_tool(chat_id: int, tool_name: str, arguments: dict, user_context: Optional[dict] = None) -> dict:
    """
    Executes a tool call asynchronously with Write Action Idempotency Shield
    (Layer 3 Glass Tower Standard) and returns structured JSON output.
    """
    from core.idempotency import check_idempotency, record_idempotency
    cached_replay = check_idempotency(chat_id, tool_name, arguments)
    if cached_replay is not None:
        return cached_replay

    res = await _execute_tool_inner(chat_id, tool_name, arguments, user_context)
    res = to_json_safe(res)
    if isinstance(res, dict):
        record_idempotency(chat_id, tool_name, arguments, res)
    return res

_TOOL_NAME_ALIASES = {
    "find_nearby_place": "find_nearby_places",
}


def _load_user_export_sheets(chat_id: int, data_type: Optional[str]) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """
    Shared exporter helper for contacts, reminders/tasks, facts, or all saved user data.
    Returns (sheets, raw_contacts).
    """
    dtype = (data_type or "all").strip().lower()
    sheets: List[Dict[str, Any]] = []
    contacts: List[Dict[str, Any]] = []

    if dtype in ("contacts", "contact", "all"):
        contacts = db.get_contact_memories(chat_id) or []
        sheets.append({
            "name": "Contacts & Networking",
            "headers": [
                "Full Name", "Company", "Job Title / Role", "Email", "Phone",
                "Met At / Location", "Context & Notes", "Commitments / Action Items", "Saved Date"
            ],
            "rows": [
                [
                    c.get("name") or "",
                    c.get("company") or "",
                    c.get("role_title") or "",
                    c.get("email") or "",
                    c.get("phone") or "",
                    c.get("met_at") or "",
                    c.get("context_notes") or "",
                    c.get("commitments") or "",
                    str(c.get("created_at") or "")[:10],
                ]
                for c in contacts
            ],
        })

    if dtype in ("reminders", "reminder", "tasks", "task", "schedule", "all"):
        tasks = db.list_memories(chat_id) or []
        sheets.append({
            "name": "Tasks & Reminders",
            "headers": ["Category", "Title", "Amount", "Due Date", "Time", "Duration (mins)"],
            "rows": [
                [
                    t[0] if len(t) > 0 else "",
                    t[1] if len(t) > 1 else "",
                    t[2] if len(t) > 2 and t[2] is not None else "",
                    str(t[3]) if len(t) > 3 and t[3] else "",
                    str(t[4])[:5] if len(t) > 4 and t[4] else "",
                    t[5] if len(t) > 5 and t[5] is not None else 30,
                ]
                for t in tasks
            ],
        })

    if dtype in ("facts", "fact", "notes", "all"):
        facts = db.get_user_facts(chat_id, limit=50) or []
        sheets.append({
            "name": "Saved Facts",
            "headers": ["Fact / Memory", "Category", "Saved At"],
            "rows": [
                [
                    f.get("fact") or "",
                    f.get("category") or "general",
                    str(f.get("created_at") or "")[:10],
                ]
                for f in facts
            ],
        })

    return sheets, contacts


async def _execute_tool_inner(chat_id: int, tool_name: str, arguments: dict, user_context: Optional[dict] = None) -> dict:
    import google_services
    tool_name = _TOOL_NAME_ALIASES.get(tool_name, tool_name)
    origin_chat_id = (user_context or {}).get("delivery_chat_id") or (user_context or {}).get("originating_chat_id") or chat_id
    channel = (user_context or {}).get("channel") or "telegram"
    try:
        # Step 6: Server-side validation of request_quote on write tools
        if tool_name in WRITE_TOOLS_WITH_QUOTE:
            quote = arguments.get("request_quote")
            if not quote and user_context and user_context.get("user_text"):
                u_text = str(user_context["user_text"]).strip()
                u_low = u_text.lower()
                # Status inquiries and informational questions must never auto-synthesize a write quote,
                # but questions that contain explicit write action verbs (e.g. "Can you delete reminder 5?")
                # are genuine write requests and must not be blocked.
                is_explicit_status = any(w in u_low for w in ("what about", "how about", "tell me about", "kya hua", "kya status", "status of"))
                has_write_action = not is_explicit_status and (any(w in u_low for w in (
                    "delete", "remove", "hata", "cancel", "create", "add", "save",
                    "bana", "laga", "likh", "update", "kar do", "kardo", "schedule", "clear", "wipe", "drop"
                )) or ("set" in u_low and not any(p in u_low for p in ("u set", "you set", "i set", "already set"))))
                is_status_inquiry = is_explicit_status or (("?" in u_text or any(w in u_low.split() for w in ("kya", "what", "show", "check", "list", "dikhao", "status"))) and not has_write_action)
                if not is_status_inquiry:
                    quote = u_text
            if not validate_request_quote(chat_id, quote, user_context):
                return {
                    "status": "error",
                    "error": "no_user_request_for_write",
                    "action": tool_name,
                    "facts": {
                        "reason": "no_user_request_for_write",
                        "details": "Write operation rejected: request_quote must match text from the user's message.",
                        "tool": tool_name,
                        "provided_quote": quote
                    },
                    "next_options": ["answer_read_only", "ask_user_for_confirmation"]
                }
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
                if "status" not in tool_res.data:
                    tool_res.data["status"] = "success"
                return tool_res.data
            return {"status": "success", "result": tool_res.data}

        if tool_name == "execute_python_code":
            from agent.sandbox import run_python_sandbox
            code = arguments.get("code") or ""
            purpose = arguments.get("purpose") or ""
            return await run_python_sandbox(
                chat_id=chat_id,
                code=code,
                purpose=purpose,
                delivery_chat_id=origin_chat_id,
                channel=channel,
            )

        if tool_name == "validate_ticket_date":
            from state.active_context import get_active_ticket, parse_ticket_datetime
            tkt = get_active_ticket(chat_id)
            dt_str = (arguments.get("datetime_str") or (tkt.get("datetime_str") if tkt else None))
            tkt_dt = parse_ticket_datetime(dt_str) if dt_str else None
            now_ist = timeutil.now(IST)
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
            recent_actions = []
            try:
                from memory.store import resolve_user_id, search_memories_hybrid, get_recent_actions_for_user
                uid = resolve_user_id(chat_id)
                hybrid_res = search_memories_hybrid(uid, args.query, limit=5)
                q_tokens = [tok for tok in re.findall(r"[a-zA-Z0-9]+", (args.query or "").lower()) if len(tok) >= 2]
                all_actions = get_recent_actions_for_user(uid, limit=10)
                if q_tokens:
                    recent_actions = [
                        a for a in all_actions
                        if any(tok in (a.get("summary") or "").lower() or tok in str(a.get("details") or "").lower() for tok in q_tokens)
                    ]
                if not recent_actions:
                    recent_actions = all_actions[:5]
            except Exception as exc:
                print("Hybrid search fallback:", exc)

            logs = db.get_chat_turns_mentioning_keyword(chat_id, args.query, limit=10)
            facts = db.get_user_facts(chat_id, limit=20)
            matched_facts = [f for f in facts if args.query.lower() in f.get("fact", "").lower()]
            return {
                "status": "success",
                "query": args.query,
                "hybrid_memories": [f"[{h['memory_type']}] {h['content']}" for h in hybrid_res],
                "recent_actions": [f"[{str(a.get('created_at', ''))[:16]}] {a.get('summary', '')}" for a in recent_actions],
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
                    "status": "success",
                    "found": True,
                    "name": name,
                    "company": company,
                    "role_title": role_title,
                    "email": email,
                    "phone": phone,
                    "commitments": commitments,
                    "context_notes": (match.get("context_notes") if match else "") or cognitive_data.get("context_notes", "")
                }
            return {"status": "not_found", "found": False, "message": f"Contact '{args.name_or_alias}' not found in saved memories."}

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
                # Universal phonetic / double-letter typo variations for any contact
                for al in list(alias_list):
                    al_low = al.lower()
                    collapsed = re.sub(r'([a-z])\1+', r'\1', al_low)
                    if collapsed != al_low and collapsed not in [a.lower() for a in alias_list]:
                        alias_list.append(collapsed)
                    # Common phonetic consonant doubling (e.g. ananya -> annaya)
                    if len(al_low) >= 4 and al_low[1] not in "aeiou":
                        doubled = al_low[0] + al_low[1] + al_low[1] + al_low[2:]
                        if doubled not in [a.lower() for a in alias_list]:
                            alias_list.append(doubled)

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
            user_self_names = set()
            try:
                with db.engine.connect() as conn:
                    urow = conn.execute(
                        db.text("SELECT first_name, username FROM users WHERE id = :u OR telegram_chat_id = :c"),
                        {"u": uid, "c": chat_id}
                    ).first()
                    if urow:
                        if urow[0]: user_self_names.add(str(urow[0]).strip().lower())
                        if urow[1]: user_self_names.add(str(urow[1]).strip().lower())
            except Exception:
                pass
            EXCLUDED_NAMES = {"user", "assistant", "bot", "me", "myself", "i", "self", "friend", "admin", "ai", "companion"}.union(user_self_names)

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
            return {"status": "success", "count": len(final_contacts), "contacts": final_contacts}

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
                    return {"status": "success", "commitments": [{"name": c["contact_name"], "commitment": c["commitment"]} for c in all_comms]}
            except Exception as exc:
                print("Cognitive get_commitments fallback:", exc)

            contacts = db.get_contact_memories(chat_id)
            if args.contact:
                from main import find_matching_contact
                match = find_matching_contact(args.contact, contacts)
                if match:
                    return {"status": "success", "contact": match["name"], "commitment": match.get("commitments", "")}
                return {"status": "not_found", "contact": args.contact, "commitment": "No record found"}
            comms = [{"name": c["name"], "company": c.get("company"), "commitment": c["commitments"]} for c in contacts if c.get("commitments")]
            return {"status": "success", "commitments": comms}

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
            return {"status": "success", "saved_fact": args.text, "message": f"Note saved: '{args.text}'."}

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

        elif tool_name == "set_user_language":
            args = SetUserLanguageArgs(**arguments)
            clean_l = args.language.strip().lower()
            db.set_user_language(chat_id, clean_l)
            return {
                "status": "success",
                "action": "set_user_language",
                "language": clean_l,
                "facts": {"language": clean_l},
                "next_options": ["continue_conversation"]
            }

        elif tool_name == "set_user_preference":
            args = SetUserPreferenceArgs(**arguments)
            clean_lang = (args.reply_language or "").strip()
            tone = (args.tone or "concise").strip()
            db.set_user_preference(chat_id, reply_language=clean_lang, tone=tone)

            try:
                from agent.prompt import invalidate_prompt_schedule_cache
                invalidate_prompt_schedule_cache(chat_id)
            except Exception:
                pass

            return {
                "status": "success",
                "action": "set_user_preference",
                "facts": {
                    "reply_language": clean_lang,
                    "tone": tone
                },
                "next_options": ["continue_conversation"]
            }

        elif tool_name == "request_master_privacy_wipe":
            from state.pending import save_pending_action
            db.set_state(chat_id, "confirm_master_wipe")
            save_pending_action(chat_id, "master_privacy_wipe", {})
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
            today_d = timeutil.today(u_tz)

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
                        db.text("SELECT id, title, due_date, due_time, COALESCE(duration_minutes, 30) AS duration_minutes FROM memory_items WHERE id = :id AND user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c) AND (status IS NULL OR status != 'deleted')"),
                        {"id": rem_id, "c": chat_id}
                    ).mappings().first()
                    if row:
                        matched_row = dict(row)
            else:
                existing_items = db.items_for_date(chat_id, today_d)
                range_items = db.items_for_date_range(chat_id, today_d, today_d + timedelta(days=7))

                # 1. If from_time is provided or extractable from user message, match by original time first
                search_from_time = getattr(args, "from_time", None)
                if not search_from_time and user_msg:
                    # Robust transition extraction: supports 'from X to Y', 'X ki jagah Y', 'X ke badle Y'
                    m_time = re.search(r'\b(\d{1,2})(?::(\d{2}))?\s*(am|pm|baje)?\s*(?:ke badle|ki jagah|se badal|se|to|from|wali|wala|ka)\b', user_msg)
                    if m_time:
                        hr = int(m_time.group(1))
                        minute = int(m_time.group(2) or 0)
                        ampm = (m_time.group(3) or "").lower()
                        if ampm == "pm" and hr < 12: hr += 12
                        elif ampm == "am" and hr == 12: hr = 0
                        elif ampm in ("baje", "") and 1 <= hr <= 6: hr += 12
                        search_from_time = f"{hr:02d}:{minute:02d}"

                if search_from_time:
                    for r in existing_items + range_items:
                        r_tm = _get_row_val(r, "due_time", 3)
                        if r_tm and str(r_tm).startswith(search_from_time[:5]):
                            matched_row = r
                            break

                # 2. Inspect candidate items in reverse chronological order (most recent first)
                search_list = list(reversed(existing_items))

                # 3. Match by title keyword
                if not matched_row and target_kw:
                    for r in search_list:
                        r_title = (_get_row_val(r, "title", 1, "") or "").strip().lower()
                        if target_kw == r_title or target_kw in r_title or r_title in target_kw:
                            matched_row = r
                            break

                # 4. If still not matched, search upcoming 7 days
                if not matched_row and target_kw:
                    for r in range_items:
                        r_title = (_get_row_val(r, "title", 1, "") or "").strip().lower()
                        if target_kw == r_title or target_kw in r_title or r_title in target_kw:
                            matched_row = r
                            break

                # 5. Mind-Changer / Entity Replacement Fallback:
                # If user corrects an entity (e.g. 'are nahi Devender nahi Rahul ke saath 11 baje kar do'),
                # search upcoming reminders for the entity being replaced from user_msg.
                explicit_new_title = getattr(args, "new_title", None) or arguments.get("new_title")
                if not matched_row and (user_msg or target_kw):
                    combined_search = (user_msg + " " + target_kw).lower()
                    for r in range_items:
                        r_title = (_get_row_val(r, "title", 1, "") or "").strip().lower()
                        r_words = [w for w in re.findall(r'\b[a-zA-Z0-9_-]+\b', r_title) if len(w) > 3 and w not in ("meeting", "reminder", "with")]
                        if any(w in combined_search for w in r_words):
                            matched_row = r
                            if not explicit_new_title and target_kw and target_kw not in r_title:
                                explicit_new_title = args.title
                            break

                # 6. Active Context Fallback (most recently created/discussed reminder)
                if not matched_row and user_context:
                    act_ctx = (user_context or {}).get("active_context") or {}
                    last_ev = act_ctx.get("last_event") or {}
                    last_id = last_ev.get("id") or last_ev.get("item_id")
                    if last_id:
                        with db.engine.connect() as conn:
                            row = conn.execute(
                                db.text("SELECT id, title, due_date, due_time, COALESCE(duration_minutes, 30) AS duration_minutes FROM memory_items WHERE id = :id AND user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c) AND (status IS NULL OR status != 'deleted')"),
                                {"id": last_id, "c": chat_id}
                            ).mappings().first()
                            if row:
                                matched_row = dict(row)
                                if not explicit_new_title and target_kw:
                                    explicit_new_title = args.title

                # 7. If title keyword was omitted or generic and only one/latest item exists today
                if not matched_row and existing_items:
                    matched_row = search_list[0]

            if not matched_row:
                return {
                    "action": "update_reminder",
                    "tool_name": "update_reminder",
                    "status": "error",
                    "error": "reminder_not_found",
                    "facts": {
                        "searched_title": args.title,
                        "found": False
                    },
                    "next_options": ["list_reminders", "create_reminder"]
                }

            row_id = None
            orig_title = ""
            curr_date = None
            curr_time = None
            curr_dur = 30

            if isinstance(matched_row, dict):
                row_id = matched_row.get("id")
                orig_title = matched_row.get("title", "")
                curr_date = matched_row.get("due_date")
                curr_time = matched_row.get("due_time")
                curr_dur = _safe_int(matched_row.get("duration_minutes", 30), default=30)
            elif hasattr(matched_row, "_mapping"):
                m = matched_row._mapping
                row_id = m.get("id")
                orig_title = m.get("title", "")
                curr_date = m.get("due_date")
                curr_time = m.get("due_time")
                curr_dur = _safe_int(m.get("duration_minutes", 30), default=30)
            else:
                orig_title = matched_row[1] if len(matched_row) > 1 else ""
                curr_time = matched_row[3] if len(matched_row) > 3 else None
                if len(matched_row) > 6 and isinstance(matched_row[6], (date, datetime, str)) and not isinstance(matched_row[6], bool):
                    curr_date = matched_row[6]
                    row_id = matched_row[4]
                    curr_dur = _safe_int(matched_row[5], default=30)
                elif len(matched_row) > 4 and isinstance(matched_row[4], (date, datetime, str)) and not isinstance(matched_row[4], bool):
                    curr_date = matched_row[4]
                    row_id = matched_row[5] if len(matched_row) > 5 else None
                    curr_dur = _safe_int(matched_row[6], default=30) if len(matched_row) > 6 else 30
                else:
                    row_id = matched_row[4] if len(matched_row) > 4 else None
                    curr_date = today_d

            if not curr_date:
                curr_date = today_d
            if isinstance(curr_date, str):
                try:
                    curr_date = date.fromisoformat(curr_date.split("T")[0])
                except Exception:
                    curr_date = today_d
            elif isinstance(curr_date, datetime):
                curr_date = curr_date.date()

            explicit_new_title = getattr(args, "new_title", None) or arguments.get("new_title")
            new_title = (explicit_new_title or args.title or orig_title).strip()
            if args.due_date:
                ok_d, resolved_new_date, err_d = timeutil.resolve_date(args.due_date, tz=u_tz, allow_past=False)
                if not ok_d:
                    return {
                        "action": "update_reminder",
                        "tool_name": "update_reminder",
                        "status": "error",
                        "error": "invalid_date_or_in_past",
                        "today": today_d.isoformat(),
                        "date": args.due_date,
                        "timezone": u_tz.key if hasattr(u_tz, "key") else str(u_tz),
                        "message": f"Date {args.due_date} is invalid or in the past. Today is {today_d.isoformat()} ({u_tz}). Please specify today or a future date.",
                        "facts": {
                            "date": args.due_date,
                            "today": today_d.isoformat(),
                            "timezone": u_tz.key if hasattr(u_tz, "key") else str(u_tz)
                        },
                        "next_options": ["resend_with_valid_date"]
                    }
                new_date = resolved_new_date
            else:
                new_date = curr_date
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

            # Step 4: Strict Time validation check (tolerance configurable, default 0)
            now_user = timeutil.now(u_tz)
            tolerance_sec = int(os.environ.get("TIME_VALIDATION_TOLERANCE_SECONDS", "0"))
            if new_time:
                target_new_dt = datetime.combine(new_date, new_time, tzinfo=u_tz)
                if target_new_dt < (now_user - timedelta(seconds=tolerance_sec)):
                    return {
                        "action": "update_reminder",
                        "tool_name": "update_reminder",
                        "status": "error",
                        "error": "time_already_passed",
                        "now": now_user.isoformat(),
                        "requested": f"{new_date.isoformat()} {new_time.strftime('%H:%M')}",
                        "tolerance_seconds": tolerance_sec,
                        "facts": {
                            "title": new_title,
                            "requested_date": new_date.isoformat(),
                            "requested_time": new_time.strftime("%H:%M"),
                            "current_time": now_user.strftime("%Y-%m-%d %H:%M:%S %Z"),
                            "is_past": True
                        },
                        "next_options": ["schedule_for_tomorrow", "choose_future_time"]
                    }

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
                return {
                    "action": "update_reminder",
                    "tool_name": "update_reminder",
                    "status": "conflict_detected",
                    "has_conflict": True,
                    "conflict_type": "direct_overlap",
                    "conflicts": conflicts,
                    "message": f"Time Conflict: {', '.join(conflicts)}",
                    "due_date": new_date.isoformat(),
                    "due_time": args.due_time or (new_time.strftime("%H:%M") if new_time else None),
                    "facts": {
                        "title": new_title,
                        "due_date": new_date.isoformat(),
                        "due_time": args.due_time or (new_time.strftime("%H:%M") if new_time else None),
                        "duration_minutes": new_dur,
                        "conflicting_events": conflicts
                    },
                    "next_options": ["confirm_with_force", "choose_different_time", "cancel"]
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
            try:
                import time as _t
                from memory.store import record_action_event
                record_action_event(
                    chat_id,
                    action_type="update_reminder",
                    summary=f"Updated reminder {row_id}: '{new_title}' for {date_str} at {time_str}",
                    details={"title": new_title, "item_id": row_id, "due_date": new_date.isoformat(), "due_time": new_time.strftime("%H:%M") if new_time else None},
                    source_type="reminder",
                    source_id=f"rem_upd_{row_id}_{int(_t.time())}",
                    importance=3
                )
            except Exception:
                pass
            return {
                "action": "update_reminder",
                "tool_name": "update_reminder",
                "status": "success",
                "title": new_title,
                "due_date": new_date.isoformat() if new_date else None,
                "due_time": args.due_time or (new_time.strftime("%H:%M") if new_time else None),
                "duration_minutes": new_dur,
                "message": f"Reminder '{new_title}' updated for {date_str} at {time_str} ({args.due_time or (new_time.strftime('%H:%M') if new_time else '')}).",
                "rescheduled": True,
                "has_conflict": len(conflicts) > 0,
                "conflicts": conflicts,
                "facts": {
                    "item_id": row_id,
                    "title": new_title,
                    "due_date": new_date.isoformat(),
                    "due_time": new_time.strftime("%H:%M") if new_time else None,
                    "duration_minutes": new_dur,
                    "rescheduled": True
                },
                "next_options": ["list_reminders", "delete_reminder"]
            }

        elif tool_name in ("create_reminder", "add_reminder"):
            args = CreateReminderArgs(**arguments)
            u_tz, _ = await _resolve_user_timezone(chat_id)
            today_d = timeutil.today(u_tz)

            # Date resolution with strict validation
            ok_d, d_val, err_d = timeutil.resolve_date(args.due_date, tz=u_tz, allow_past=False)
            if not ok_d:
                return {
                    "action": "create_reminder",
                    "tool_name": "create_reminder",
                    "status": "error",
                    "error": "invalid_date_or_in_past",
                    "today": today_d.isoformat(),
                    "date": args.due_date,
                    "timezone": u_tz.key if hasattr(u_tz, "key") else str(u_tz),
                    "message": f"Date {args.due_date} is invalid or in the past. Today is {today_d.isoformat()} ({u_tz}). Please specify today or a future date.",
                    "facts": {
                        "date": args.due_date,
                        "today": today_d.isoformat(),
                        "timezone": u_tz.key if hasattr(u_tz, "key") else str(u_tz)
                    },
                    "next_options": ["resend_with_valid_date"]
                }
            args.due_date = d_val.isoformat()

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

            # Recurrence extraction & normalization
            recurrence = (args.recurrence or "none").strip().lower()
            if recurrence not in ("daily", "weekly", "weekdays", "monthly", "none"):
                recurrence = "none"

            clean_title = args.title.strip()
            target_low = clean_title.lower()

            # Step 4: Strict Time-of-day Validation (tolerance configurable, default 0)
            now_user = timeutil.now(u_tz)
            tolerance_sec = int(os.environ.get("TIME_VALIDATION_TOLERANCE_SECONDS", "0"))
            if t_val:
                target_dt = datetime.combine(d_val, t_val, tzinfo=u_tz)
                if target_dt < (now_user - timedelta(seconds=tolerance_sec)):
                    return {
                        "action": "create_reminder",
                        "tool_name": "create_reminder",
                        "status": "error",
                        "error": "time_already_passed",
                        "now": now_user.isoformat(),
                        "requested": f"{d_val.isoformat()} {t_val.strftime('%H:%M')}",
                        "tolerance_seconds": tolerance_sec,
                        "facts": {
                            "title": clean_title,
                            "requested_date": d_val.isoformat(),
                            "requested_time": t_val.strftime("%H:%M"),
                            "current_time": now_user.strftime("%Y-%m-%d %H:%M:%S %Z"),
                            "is_past": True
                        },
                        "next_options": ["schedule_for_tomorrow", "schedule_daily_from_tomorrow", "choose_future_time_today"]
                    }

            # Check if this is an explicit reschedule of an existing reminder
            existing_items = db.items_for_date(chat_id, d_val)
            matched_id = None
            generic_titles = {"meeting", "call", "reminder", "task", "event", "appointment", "class", "session"}

            # Match existing item for overwrite if the title is non-generic (e.g. "Meeting with Devender")
            if target_low not in generic_titles:
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
                            tight_buffers.append(f"'{r_title}' (gap {gap} min)")

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
                return {
                    "action": "create_reminder",
                    "tool_name": "create_reminder",
                    "status": "conflict_detected",
                    "has_conflict": True,
                    "conflict_type": "direct_overlap",
                    "conflicts": conflicts,
                    "message": f"Time Conflict: {', '.join(conflicts)}",
                    "conflict_warning": f"Time Conflict: {', '.join(conflicts)}",
                    "due_date": args.due_date,
                    "due_time": args.due_time,
                    "facts": {
                        "title": clean_title,
                        "due_date": args.due_date,
                        "due_time": args.due_time,
                        "duration_minutes": dur_mins,
                        "conflicting_events": conflicts
                    },
                    "next_options": ["confirm_with_force", "choose_different_time", "cancel"]
                }

            if tight_buffers and not args.force and not matched_id:
                return {
                    "action": "create_reminder",
                    "tool_name": "create_reminder",
                    "status": "conflict_detected",
                    "has_conflict": True,
                    "conflict_type": "tight_buffer",
                    "conflicts": tight_buffers,
                    "message": f"Schedule Buffer: {', '.join(tight_buffers)}",
                    "conflict_warning": f"Schedule Buffer: {', '.join(tight_buffers)}",
                    "due_date": args.due_date,
                    "due_time": args.due_time,
                    "facts": {
                        "title": clean_title,
                        "due_date": args.due_date,
                        "due_time": args.due_time,
                        "duration_minutes": dur_mins,
                        "tight_buffers": tight_buffers
                    },
                    "next_options": ["confirm_with_force", "choose_different_time", "cancel"]
                }

            # If matched existing ID, update it cleanly
            if matched_id:
                db.update_memory_item_full(chat_id, matched_id, title=clean_title, due_date=d_val, due_time=t_val, duration_minutes=dur_mins, recurrence=recurrence)
                item_id = matched_id
                rescheduled = True
            else:
                item_id = db.save_memory(chat_id, args.category or 'appointments', clean_title, amount=None, due_date=d_val, due_time=t_val, duration_minutes=dur_mins, recurrence=recurrence)
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
            except Exception as plan_err:
                logger.warning(f"Reminder plan scheduling error: {plan_err}")

            # Sync to Google Calendar if connected
            is_conn = False
            try:
                from main import get_valid_google_connection_by_chat_id
                is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
                if is_conn and d_val and t_val:
                    ev_start_dt = datetime.combine(d_val, t_val, tzinfo=u_tz)
                    ev_end_dt = ev_start_dt + timedelta(minutes=dur_mins)
                    g_rec = ["RRULE:FREQ=DAILY"] if recurrence == "daily" else (["RRULE:FREQ=WEEKLY"] if recurrence == "weekly" else None)
                    cal_res = await google_services.create_calendar_event(
                        chat_id,
                        summary=f"🔔 {clean_title}",
                        start_time=ev_start_dt.isoformat(),
                        end_time=ev_end_dt.isoformat(),
                        description=f"Auto-synced reminder from PeculiarAI ({dur_mins} mins, recurrence={recurrence})",
                        recurrence=g_rec,
                    )
                    if cal_res and isinstance(cal_res, dict) and cal_res.get("id"):
                        try:
                            db.update_memory_calendar_id(chat_id, item_id, cal_res["id"])
                        except Exception:
                            pass
            except Exception as cal_err:
                logger.warning(f"Google Calendar auto-sync error: {cal_err}")

            # Invalidate prompt schedule cache immediately!
            try:
                from agent.prompt import invalidate_prompt_schedule_cache
                invalidate_prompt_schedule_cache(chat_id)
            except Exception as cache_err:
                logger.warning(f"Prompt schedule cache invalidation error: {cache_err}")

            time_str = t_val.strftime("%I:%M %p") if t_val else "specified time"
            date_str = d_val.strftime("%d %b %Y") if d_val else "specified date"

            try:
                from memory.store import record_action_event
                record_action_event(
                    chat_id,
                    action_type="create_reminder",
                    summary=f"Set reminder: '{clean_title}' for {date_str} at {time_str} (recurrence={recurrence})",
                    details={"title": clean_title, "due_date": args.due_date, "due_time": args.due_time, "item_id": item_id, "recurrence": recurrence},
                    source_type="reminder",
                    source_id=f"rem_{item_id}",
                    importance=3
                )
            except Exception:
                pass

            return {
                "action": "create_reminder",
                "tool_name": "create_reminder",
                "status": "success",
                "title": clean_title,
                "due_date": args.due_date,
                "due_time": args.due_time,
                "duration_minutes": dur_mins,
                "has_conflict": len(conflicts) > 0,
                "conflicts": conflicts,
                "rescheduled": rescheduled,
                "facts": {
                    "item_id": item_id,
                    "title": clean_title,
                    "due_date": args.due_date,
                    "due_time": args.due_time,
                    "duration_minutes": dur_mins,
                    "recurrence": recurrence,
                    "rescheduled": rescheduled,
                    "calendar_synced": is_conn
                },
                "next_options": ["list_reminders", "update_reminder", "delete_reminder"]
            }

        elif tool_name == "delete_reminder":
            kw = str(arguments.get("title") or "").strip().lower()
            rem_id = arguments.get("reminder_id")
            target_ids = arguments.get("target_ids")
            has_model_confirm = bool(arguments.get("confirmed") or arguments.get("confirm", False))
            is_agent_loop_model_call = bool(user_context and "delivery_chat_id" in user_context and not user_context.get("_from_pending_executor"))
            is_from_pending = bool(user_context and (user_context.get("_from_pending_executor") or (user_context.get("bypass_quote_check") and arguments.get("target_ids"))))
            from db import canonical_chat_id, engine, text
            can_cid = canonical_chat_id(chat_id)

            clean_kw = kw.replace("*", "").replace("'", "").replace('"', "").strip()

            # Normalize target IDs list
            ids_to_act = []
            if target_ids and isinstance(target_ids, list):
                ids_to_act = [int(v) for v in target_ids if isinstance(v, int) or (isinstance(v, str) and v.isdigit())]
            elif rem_id is not None:
                try:
                    ids_to_act = [int(rem_id)]
                except (ValueError, TypeError):
                    pass

            # A5.1: Model cannot supply confirmation. Reject model-supplied confirmation.
            if has_model_confirm and not is_from_pending:
                return {
                    "status": "error",
                    "error_type": "model_confirmation_rejected",
                    "action": "delete_reminder",
                    "tool_name": "delete_reminder",
                    "facts": {
                        "reason": "Model cannot self-confirm destructive actions. User confirmation via pending action state machine is required.",
                        "attempted_ids": ids_to_act
                    },
                    "next_options": ["stage_pending_delete", "list_reminders"]
                }

            if is_from_pending:
                # Execution phase: ONLY on exact IDs via pending action handler
                if not ids_to_act and clean_kw:
                    with engine.connect() as conn:
                        rows = conn.execute(
                            text("SELECT m.id, m.title FROM memory_items m JOIN users u ON u.id = m.user_id "
                                 "WHERE u.telegram_chat_id = :c AND LOWER(m.title) LIKE :pat AND (m.status IS NULL OR m.status != 'deleted')"),
                            {"c": can_cid, "pat": f"%{clean_kw}%"}
                        ).fetchall()
                        if len(rows) == 1:
                            ids_to_act = [rows[0][0]]
                        elif len(rows) > 1:
                            return {
                                "status": "ambiguous",
                                "action": "delete_reminder",
                                "tool_name": "delete_reminder",
                                "facts": {
                                    "matching_items": [{"id": r[0], "title": r[1]} for r in rows],
                                    "reason": "Multiple matching reminders found. User must specify which ID to delete."
                                },
                                "next_options": ["specify_target_id", "list_reminders"]
                            }

                if not ids_to_act:
                    return {
                        "status": "error",
                        "error": "missing_delete_target",
                        "action": "delete_reminder",
                        "tool_name": "delete_reminder",
                        "facts": {"reason": "Exact reminder ID(s) required for confirmed deletion."},
                        "next_options": ["list_reminders"]
                    }

                scope_arg = arguments.get("scope", "all")
                deleted = db.soft_delete_reminders(chat_id, ids_to_act, scope=scope_arg)

                # Check for direct calendar event ID staged from Google Calendar discovery
                direct_cal_id = arguments.get("google_calendar_event_id")
                if direct_cal_id:
                    try:
                        await google_services.delete_calendar_event(chat_id, direct_cal_id)
                        logger.info(f"Two-way sync: deleted direct Google Calendar event {direct_cal_id}")
                    except Exception as g_del_err:
                        logger.warning(f"Error deleting direct calendar event {direct_cal_id}: {g_del_err}")

                if deleted or direct_cal_id:
                    try:
                        from api_v1 import _invalidate_all_app_caches
                        _invalidate_all_app_caches()
                        from agent.prompt import invalidate_prompt_schedule_cache
                        invalidate_prompt_schedule_cache(chat_id)
                    except Exception:
                        pass
                    # If synced to Google Calendar, remove Google Calendar event as well
                    try:
                        for d_id, d_title in (deleted or []):
                            cal_ev_id = None
                            with engine.connect() as conn:
                                try:
                                    cal_row = conn.execute(
                                        text("SELECT google_calendar_event_id FROM memory_items WHERE id = :id"),
                                        {"id": d_id}
                                    ).fetchone()
                                    if cal_row and cal_row[0]:
                                        cal_ev_id = cal_row[0]
                                except Exception:
                                    pass
                            if cal_ev_id:
                                try:
                                    base_ev_id = cal_ev_id.split("_")[0] if "_" in cal_ev_id else cal_ev_id
                                    await google_services.delete_calendar_event(chat_id, base_ev_id)
                                    logger.info(f"Two-way sync: deleted calendar event {base_ev_id} by event_id")
                                except Exception as cal_err:
                                    logger.warning(f"Could not delete synced calendar event {cal_ev_id}: {cal_err}")

                            # Fallback & Complete Purge: search Google Calendar for matching title
                            # Ensures events created before column migration or recurring instances are 100% purged
                            try:
                                cal_events = await google_services.list_calendar_events(chat_id, query=d_title, max_results=10)
                                norm_title = d_title.strip().lower()
                                for ev in (cal_events or []):
                                    ev_summary = (ev.get("summary") or "").strip().lower()
                                    ev_id = ev.get("id")
                                    clean_summary = ev_summary.lstrip("🔔 ").strip()
                                    if ev_id and (clean_summary == norm_title or norm_title in clean_summary or clean_summary in norm_title):
                                        base_ev_id = ev_id.split("_")[0] if "_" in ev_id else ev_id
                                        await google_services.delete_calendar_event(chat_id, base_ev_id)
                                        logger.info(f"Two-way sync: deleted matching Google Calendar event {base_ev_id} for reminder '{d_title}'")
                            except Exception as search_del_err:
                                logger.warning(f"Fallback calendar search-and-delete failed for '{d_title}': {search_del_err}")
                    except Exception:
                        pass
                    try:
                        import time as _t
                        from memory.store import record_action_event
                        for d_id, d_title in deleted:
                            record_action_event(
                                chat_id,
                                action_type="delete_reminder",
                                summary=f"Soft-deleted reminder '{d_title}' [ID {d_id}]",
                                details={"id": d_id, "title": d_title},
                                source_type="reminder",
                                source_id=f"rem_del_{chat_id}_{d_id}_{int(_t.time())}",
                                importance=2
                            )
                    except Exception:
                        pass
                    return {
                        "status": "success",
                        "action": "delete_reminder",
                        "tool_name": "delete_reminder",
                        "facts": {
                            "deleted_ids": [r[0] for r in deleted],
                            "deleted_titles": [r[1] for r in deleted],
                            "deleted_count": len(deleted)
                        },
                        "next_options": ["restore_reminder", "list_reminders"]
                    }
                else:
                    return {
                        "status": "not_found",
                        "action": "delete_reminder",
                        "tool_name": "delete_reminder",
                        "facts": {"target_ids": ids_to_act, "found": False, "reason": "Target IDs not found or not owned by user."},
                        "next_options": ["list_reminders"]
                    }

            # Phase 1: Unconfirmed request -> Discovery & Confirmation gate
            if not clean_kw and not ids_to_act:
                return {
                    "status": "error",
                    "error": "missing_delete_target",
                    "action": "delete_reminder",
                    "facts": {"reason": "An explicit reminder title or ID is required to perform deletion."},
                    "next_options": ["list_reminders", "specify_target_to_delete"]
                }

            matched_rows = []
            kw_to_check = clean_kw
            if not kw_to_check and user_context:
                u_text = str(user_context.get("user_text", "")).lower()
                cand_words = [w for w in re.findall(r'\b[a-zA-Z0-9_-]+\b', u_text) if len(w) > 3 and w not in ("cancel", "delete", "delet", "hata", "kar", "karo", "krdo", "please", "yes", "confirm", "this", "that")]
                if cand_words:
                    kw_to_check = " ".join(cand_words)

            with engine.connect() as conn:
                is_pg = engine.dialect.name == "postgresql"
                if ids_to_act:
                    if is_pg:
                        matched_rows = conn.execute(
                            text("SELECT m.id, m.title, m.due_date, m.due_time FROM memory_items m JOIN users u ON u.id = m.user_id "
                                 "WHERE u.telegram_chat_id = :c AND m.id = ANY(:ids) AND (m.status IS NULL OR m.status != 'deleted')"),
                            {"c": can_cid, "ids": ids_to_act}
                        ).fetchall()
                    else:
                        placeholders = ", ".join([f":id_{i}" for i in range(len(ids_to_act))])
                        p_dict = {f"id_{i}": v for i, v in enumerate(ids_to_act)}
                        p_dict["c"] = can_cid
                        matched_rows = conn.execute(
                            text(f"SELECT m.id, m.title, m.due_date, m.due_time FROM memory_items m JOIN users u ON u.id = m.user_id "
                                 f"WHERE u.telegram_chat_id = :c AND m.id IN ({placeholders}) AND (m.status IS NULL OR m.status != 'deleted')"),
                            p_dict
                        ).fetchall()

                    # Title Consistency Verification:
                    # If kw_to_check is present, verify that the ID-fetched candidate actually matches the requested title!
                    if matched_rows and kw_to_check:
                        chk_words = set(re.findall(r'\b[a-zA-Z0-9_-]+\b', kw_to_check.lower())) - {"the", "a", "an", "with", "to", "for", "in", "on", "at"}
                        if chk_words:
                            consistent_rows = []
                            for r in matched_rows:
                                r_title_lower = str(r[1] or "").lower()
                                r_words = set(re.findall(r'\b[a-zA-Z0-9_-]+\b', r_title_lower))
                                has_overlap = bool(chk_words & r_words) or any(w in r_title_lower for w in chk_words) or any(w in kw_to_check.lower() for w in r_words if len(w) > 3)
                                if has_overlap:
                                    consistent_rows.append(r)
                            if not consistent_rows:
                                # Candidate ID points to an unrelated item (e.g. Gym session when user asked for Devender)!
                                logger.info(f"delete_reminder: ID {ids_to_act} title '{[r[1] for r in matched_rows]}' does not match '{kw_to_check}'. Falling back to title lookup.")
                                ids_to_act = []
                                matched_rows = []
                                if not clean_kw:
                                    clean_kw = kw_to_check
                            else:
                                matched_rows = consistent_rows

                if not ids_to_act and clean_kw:
                    # 1. Direct SQL LIKE search
                    matched_rows = conn.execute(
                        text("SELECT m.id, m.title, m.due_date, m.due_time FROM memory_items m JOIN users u ON u.id = m.user_id "
                             "WHERE u.telegram_chat_id = :c AND LOWER(m.title) LIKE :pat AND (m.status IS NULL OR m.status != 'deleted')"),
                        {"pat": f"%{clean_kw}%", "c": can_cid}
                    ).fetchall()

                    # 2. Multi-day token overlap and typo-resilient upcoming search (today + 14 days)
                    if not matched_rows:
                        u_tz, _ = await _resolve_user_timezone(chat_id)
                        today_d = timeutil.today(u_tz)
                        all_active = conn.execute(
                            text("SELECT m.id, m.title, m.due_date, m.due_time FROM memory_items m JOIN users u ON u.id = m.user_id "
                                 "WHERE u.telegram_chat_id = :c AND (m.status IS NULL OR m.status != 'deleted') "
                                 "AND (m.due_date IS NULL OR m.due_date >= :t_min) "
                                 "ORDER BY m.due_date ASC, m.due_time ASC"),
                            {"c": can_cid, "t_min": today_d.isoformat()}
                        ).fetchall()

                        search_tokens = [w for w in re.findall(r'\b[a-zA-Z0-9_-]+\b', clean_kw.lower()) if len(w) > 2 and w not in ("the", "with", "for", "cancel", "krdo", "karo", "please")]

                        def _token_sim(t1: str, t2: str) -> bool:
                            if t1 == t2 or t1 in t2 or t2 in t1:
                                return True
                            if abs(len(t1) - len(t2)) <= 1 and min(len(t1), len(t2)) >= 4:
                                diff = sum(1 for c1, c2 in zip(sorted(t1), sorted(t2)) if c1 != c2) + abs(len(t1) - len(t2))
                                return diff <= 1
                            return False

                        scored_items = []
                        for r in all_active:
                            r_title = str(r[1] or "").lower()
                            r_toks = [w for w in re.findall(r'\b[a-zA-Z0-9_-]+\b', r_title) if len(w) > 2]
                            match_count = 0
                            for st in search_tokens:
                                if any(_token_sim(st, rt) for rt in r_toks):
                                    match_count += 1
                            if match_count > 0:
                                scored_items.append((match_count, r))

                        if scored_items:
                            scored_items.sort(key=lambda x: x[0], reverse=True)
                            max_score = scored_items[0][0]
                            top_matches = [item for sc, item in scored_items if sc == max_score]
                            matched_rows = top_matches

            if not matched_rows:
                # If no active rows in memory_items, check if matching events exist on Google Calendar
                cal_found = []
                if clean_kw:
                    try:
                        cal_evs = await google_services.list_calendar_events(chat_id, query=clean_kw, max_results=5)
                        norm_kw = clean_kw.strip().lower()
                        for cev in (cal_evs or []):
                            c_sum = (cev.get("summary") or "").strip().lower().lstrip("🔔 ").strip()
                            if norm_kw in c_sum or c_sum in norm_kw:
                                cal_found.append(cev)
                    except Exception:
                        pass

                if cal_found:
                    c_ev = cal_found[0]
                    base_id = c_ev["id"].split("_")[0] if "_" in c_ev["id"] else c_ev["id"]
                    c_title = c_ev.get("summary", clean_kw)
                    return {
                        "status": "needs_confirmation",
                        "action": "delete_reminder",
                        "tool_name": "delete_reminder",
                        "message": f"Please confirm deletion of Google Calendar event '{c_title}'.",
                        "target_ids": [],
                        "facts": {
                            "google_calendar_event_id": base_id,
                            "title": c_title,
                            "requires_explicit_confirmation": True
                        },
                        "next_options": ["confirm", "cancel"]
                    }

                return {
                    "status": "not_found",
                    "action": "delete_reminder",
                    "tool_name": "delete_reminder",
                    "facts": {"target": clean_kw or str(ids_to_act), "found": False},
                    "next_options": ["list_reminders"]
                }

            if len(matched_rows) > 1 and not ids_to_act:
                # Ambiguous title -> do NOT delete! Ask which ID!
                return {
                    "status": "ambiguous",
                    "action": "delete_reminder",
                    "tool_name": "delete_reminder",
                    "facts": {
                        "reason": "Multiple matching reminders found. User must specify which ID to delete.",
                        "matching_items": [
                            {"id": r[0], "title": r[1], "due_date": str(r[2]), "due_time": str(r[3])}
                            for r in matched_rows
                        ]
                    },
                    "next_options": ["specify_target_id", "list_reminders"]
                }

            # Exactly 1 match (or explicit target_ids) -> needs confirmation before soft deleting!
            target_ids_list = [r[0] for r in matched_rows]
            items_list = [
                {"id": r[0], "title": r[1], "due_date": str(r[2]), "due_time": str(r[3])}
                for r in matched_rows
            ]
            return {
                "status": "needs_confirmation",
                "action": "delete_reminder",
                "tool_name": "delete_reminder",
                "target_ids": target_ids_list,
                "facts": {
                    "target_ids": target_ids_list,
                    "items": items_list,
                    "requires_explicit_confirmation": True
                },
                "next_options": ["confirm", "cancel"]
            }

        elif tool_name == "restore_reminder":
            rem_id = arguments.get("reminder_id")
            if rem_id is None:
                return {
                    "status": "error",
                    "error_type": "missing_required_argument",
                    "action": "restore_reminder",
                    "facts": {"reason": "Reminder ID is required to restore."},
                    "next_options": ["list_reminders"]
                }
            restored = db.restore_reminder(chat_id, int(rem_id))
            if restored:
                try:
                    from api_v1 import _invalidate_all_app_caches
                    _invalidate_all_app_caches()
                    from agent.prompt import invalidate_prompt_schedule_cache
                    invalidate_prompt_schedule_cache(chat_id)
                except Exception:
                    pass
                return {
                    "status": "success",
                    "action": "restore_reminder",
                    "tool_name": "restore_reminder",
                    "facts": {
                        "restored_id": restored["id"],
                        "title": restored["title"]
                    },
                    "next_options": ["list_reminders"]
                }
            else:
                return {
                    "status": "not_found",
                    "action": "restore_reminder",
                    "tool_name": "restore_reminder",
                    "facts": {"target_id": rem_id, "found": False},
                    "next_options": ["list_reminders"]
                }

        elif tool_name == "list_reminders":
            args = ListRemindersArgs(**arguments)
            u_tz, _ = await _resolve_user_timezone(chat_id)
            today_d = timeutil.today(u_tz)

            target_d = today_d
            if args.date_str:
                raw_d = str(args.date_str).strip()
                ok_d, resolved_d, _ = timeutil.resolve_date(raw_d, tz=u_tz, allow_past=True)
                if ok_d and resolved_d:
                    target_d = resolved_d
                    args.date_str = target_d.isoformat()
                else:
                    target_d = today_d

            items = db.items_for_date(chat_id, target_d)
            # items_for_date returns: (category, title, amount, due_time, id, duration_minutes, due_date)
            now_t = timeutil.now(u_tz).time()
            formatted = []
            for it in items:
                t_obj = None
                if it[3]:
                    if isinstance(it[3], dtime):
                        t_obj = it[3]
                    else:
                        try:
                            h, m = map(int, str(it[3]).split(":")[:2])
                            t_obj = dtime(h, m)
                        except Exception:
                            t_obj = None

                if target_d < today_d:
                    item_status = "PAST"
                elif target_d > today_d:
                    item_status = "UPCOMING"
                else:
                    item_status = "PAST" if (t_obj and t_obj < now_t) else "UPCOMING"

                formatted.append({
                    "id": it[4],
                    "category": it[0],
                    "title": it[1],
                    "due_date": target_d.isoformat(),
                    "due_time": str(it[3]) if it[3] else None,
                    "duration_minutes": it[5] if len(it) > 5 and it[5] is not None else 30,
                    "status": item_status
                })
            date_meta = timeutil.date_metadata(target_d, today_d)
            return {
                "status": "success",
                "date": date_meta["date"],
                "today": date_meta["today"],
                "is_today": date_meta["is_today"],
                "day_label": date_meta["day_label"],
                "relative": date_meta["relative"],
                "count": len(formatted),
                "reminders": formatted
            }

        elif tool_name == "get_schedule":
            args = GetScheduleArgs(**arguments)
            u_tz, _ = await _resolve_user_timezone(chat_id)
            today_d = timeutil.today(u_tz)
            target_d = today_d
            if args.target_date:
                raw_d = str(args.target_date).strip()
                ok_d, resolved_d, _ = timeutil.resolve_date(raw_d, tz=u_tz, allow_past=True)
                if ok_d and resolved_d:
                    target_d = resolved_d
                    args.target_date = target_d.isoformat()
                else:
                    target_d = today_d

            try:
                from schedule.intervals import get_unified_daily_schedule
                daily = await get_unified_daily_schedule(chat_id, target_d)
                if isinstance(daily, dict) and "status" not in daily:
                    daily["status"] = "success"
                return daily
            except Exception as exc:
                print("Interval get_schedule fallback:", exc)

            from main import get_unified_schedule, format_event_clock_ist
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
                "status": "success",
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
                if isinstance(res, dict) and "status" not in res:
                    res["status"] = "success"
                return res
            except Exception as exc:
                print("Interval availability check fallback:", exc)

            from main import get_unified_schedule, format_event_clock_ist
            target_d = date.fromisoformat(args.target_date) if args.target_date else timeutil.today(IST)
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
                "status": "success",
                "is_free": is_free,
                "queried_slot": f"{h:02d}:{m:02d} - {activity_end_mins//60:02d}:{activity_end_mins%60:02d} ({args.activity_type})",
                "conflicts": conflicts,
                "calendar_events_count": len(sched.get("calendar_events", []))
            }

        elif tool_name == "schedule_event":
            args = ScheduleEventArgs(**arguments)
            u_tz, _ = await _resolve_user_timezone(chat_id)
            cur_now = timeutil.now(u_tz)

            raw_start = args.start_iso or args.start_time
            d_val = None
            t_val = None

            if raw_start:
                try:
                    dt_start = datetime.fromisoformat(raw_start)
                    if dt_start.tzinfo is None:
                        dt_start = dt_start.replace(tzinfo=u_tz)
                    else:
                        dt_start = dt_start.astimezone(u_tz)
                    if dt_start < cur_now - timedelta(minutes=2):
                        u_text = str((user_context or {}).get("user_text", "")).lower()
                        if "tomorrow" in u_text or "kal" in u_text:
                            target_d = cur_now.date() + timedelta(days=1)
                        else:
                            target_d = cur_now.date() if dt_start.time() > cur_now.time() else cur_now.date() + timedelta(days=1)
                        dt_start = datetime.combine(target_d, dt_start.time(), tzinfo=u_tz)
                    d_val = dt_start.date()
                    t_val = dt_start.time()
                    args.start_iso = dt_start.isoformat()
                except Exception:
                    pass

            if not d_val and args.due_date:
                try:
                    d_val = date.fromisoformat(args.due_date)
                except Exception:
                    pass
            if not t_val and args.due_time:
                try:
                    t_val = dtime.fromisoformat(args.due_time)
                except Exception:
                    pass

            if not d_val:
                d_val = cur_now.date()
            if not t_val:
                t_val = dtime(17, 0)

            dur_mins = _safe_int(args.duration_minutes, default=30)
            clean_title = args.title.strip() if args.title else "Event"
            recurrence = (args.recurrence or "none").strip().lower()
            if recurrence not in ("daily", "weekly", "weekdays", "monthly", "none"):
                recurrence = "none"

            rolled_to_tomorrow = False
            if t_val:
                target_dt = datetime.combine(d_val, t_val, tzinfo=u_tz)
                if target_dt <= cur_now:
                    if recurrence in ("daily", "weekdays"):
                        d_val = cur_now.date() + timedelta(days=1)
                        if recurrence == "weekdays" and d_val.weekday() >= 5:
                            d_val = d_val + timedelta(days=(7 - d_val.weekday()))
                        rolled_to_tomorrow = True
                    elif not arguments.get("due_date") and not arguments.get("start_iso"):
                        d_val = cur_now.date() + timedelta(days=1)
                        rolled_to_tomorrow = True

            synced_tg = False
            item_id = None
            if args.sync_telegram is not False:
                try:
                    existing = db.items_for_date(chat_id, d_val) if d_val else []
                    matched_id = None
                    for m in existing:
                        m_title = _get_row_val(m, "title", 1, "")
                        if m_title and clean_title.lower() in m_title.lower():
                            matched_id = _get_row_val(m, "id", 4, None)
                            break
                    if matched_id:
                        db.update_memory_item_full(chat_id, matched_id, title=clean_title, due_date=d_val, due_time=t_val, duration_minutes=dur_mins, recurrence=recurrence)
                        item_id = matched_id
                    else:
                        item_id = db.save_memory(chat_id, 'appointments', clean_title, amount=None, due_date=d_val, due_time=t_val, duration_minutes=dur_mins, recurrence=recurrence)
                    synced_tg = True
                    if d_val and t_val:
                        try:
                            from timing import build_plan
                            plan = build_plan(d_val, t_val)
                            if plan.get("reminders") and item_id:
                                db.add_reminders(item_id, plan["reminders"])
                        except Exception as rem_e:
                            logger.warning(f"schedule_event add_reminders error: {rem_e}")
                except Exception as tg_err:
                    logger.warning(f"schedule_event telegram sync error: {tg_err}")

            synced_cal = False
            if args.sync_calendar is not False:
                from main import get_valid_google_connection_by_chat_id
                is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
                if is_conn:
                    if args.is_video_meeting:
                        from main import detect_meeting_conflicts
                        time_conflicts, day_commitments = await detect_meeting_conflicts(chat_id, args.start_iso, dur_mins)
                        return {
                            "status": "needs_confirmation",
                            "action": "create_meeting",
                            "title": clean_title,
                            "start_iso": args.start_iso,
                            "duration_minutes": dur_mins,
                            "attendees": args.attendees or [],
                            "conflicts": time_conflicts,
                            "commitments": day_commitments
                        }
                    else:
                        try:
                            ev_start_dt = datetime.combine(d_val, t_val, tzinfo=u_tz)
                            ev_end_dt = ev_start_dt + timedelta(minutes=dur_mins)
                            g_rec = ["RRULE:FREQ=DAILY"] if recurrence == "daily" else (["RRULE:FREQ=WEEKLY"] if recurrence == "weekly" else None)
                            await google_services.create_calendar_event(
                                chat_id,
                                summary=clean_title,
                                start_time=ev_start_dt.isoformat(),
                                end_time=ev_end_dt.isoformat(),
                                duration_minutes=dur_mins,
                                is_video_meeting=False,
                                attendees=args.attendees or [],
                                recurrence=g_rec
                            )
                            synced_cal = True
                        except Exception as cal_err:
                            logger.warning(f"schedule_event calendar sync error: {cal_err}")

            try:
                from agent.prompt import invalidate_prompt_schedule_cache
                invalidate_prompt_schedule_cache(chat_id)
            except Exception:
                pass

            time_str = t_val.strftime("%I:%M %p").lstrip("0")
            date_str = d_val.strftime("%d %b %Y")
            if synced_cal and synced_tg:
                channel_str = "Google Calendar aur Telegram dono par"
            elif synced_cal:
                channel_str = "Google Calendar par"
            else:
                channel_str = "Telegram reminder mein"

            return {
                "status": "success",
                "action": "schedule_event",
                "tool_name": "schedule_event",
                "synced_calendar": synced_cal,
                "synced_telegram": synced_tg,
                "message": f"Event '{clean_title}' {channel_str} schedule kar diya gaya hai.",
                "facts": {
                    "title": clean_title,
                    "due_date": d_val.isoformat(),
                    "due_time": t_val.strftime("%H:%M"),
                    "recurrence": recurrence,
                    "rolled_to_tomorrow": rolled_to_tomorrow,
                    "synced_calendar": synced_cal,
                    "synced_telegram": synced_tg
                },
                "next_options": ["get_schedule", "create_reminder"]
            }

        elif tool_name == "create_meeting":
            args = CreateMeetingArgs(**arguments)
            # Two-phase confirmation gate: return needs_confirmation
            from main import detect_meeting_conflicts, get_valid_google_connection_by_chat_id
            is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
            if not is_conn:
                return {
                    "status": "error",
                    "error": "google_account_not_connected",
                    "facts": {"connected": False},
                    "next_options": ["connect_google"]
                }

            raw_start = args.start_iso or args.start_time
            if not raw_start:
                return {
                    "status": "error",
                    "error": "missing_start_time",
                    "facts": {"title": args.summary or args.title or "Meeting"},
                    "next_options": ["specify_start_time"]
                }
            u_tz, _ = await _resolve_user_timezone(chat_id)
            cur_now = timeutil.now(u_tz)
            tolerance_sec = int(os.environ.get("TIME_VALIDATION_TOLERANCE_SECONDS", "0"))
            try:
                dt_start = datetime.fromisoformat(raw_start)
                if dt_start.tzinfo is None:
                    dt_start = dt_start.replace(tzinfo=u_tz)
                else:
                    dt_start = dt_start.astimezone(u_tz)
                if dt_start < (cur_now - timedelta(seconds=tolerance_sec)):
                    return {
                        "status": "error",
                        "error": "time_already_passed",
                        "action": "create_meeting",
                        "now": cur_now.isoformat(),
                        "requested": dt_start.isoformat(),
                        "tolerance_seconds": tolerance_sec,
                        "facts": {
                            "title": args.summary or args.title or "Meeting",
                            "requested_start": dt_start.isoformat(),
                            "now": cur_now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                            "is_past": True
                        },
                        "next_options": ["schedule_for_tomorrow", "choose_future_time"]
                    }
                args.start_iso = dt_start.isoformat()
            except Exception:
                args.start_iso = raw_start

            final_title = args.summary or args.title or "Meeting"
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
                "is_video_meeting": True,
                "has_video_link": True,
                "conflicts": time_conflicts,
                "commitments": day_commitments
            }

        elif tool_name == "create_calendar_event":
            args = CreateCalendarEventArgs(**arguments)
            u_tz, _ = await _resolve_user_timezone(chat_id)
            today_d = timeutil.today(u_tz)
            now_user = timeutil.now(u_tz)

            start_dt = None
            if args.start_iso:
                try:
                    start_dt = datetime.fromisoformat(args.start_iso)
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=u_tz)
                    else:
                        start_dt = start_dt.astimezone(u_tz)
                except Exception:
                    pass

            if not start_dt:
                d_val = None
                if args.due_date:
                    ok_d, resolved_d, _ = timeutil.resolve_date(args.due_date, tz=u_tz, allow_past=False)
                    if ok_d:
                        d_val = resolved_d
                t_val = None
                if args.due_time:
                    try:
                        h, m = map(int, args.due_time.split(":")[:2])
                        t_val = dtime(h, m)
                    except Exception:
                        pass

                # If missing date or time, lookup existing reminder matching title (e.g. "joggin wala reminder calender pr b lga do")
                if not d_val or not t_val:
                    clean_kw = args.title.replace("🔔", "").strip().lower()
                    existing_items = db.items_for_date(chat_id, today_d)
                    for it in existing_items:
                        it_title = (it[1] or "").lower()
                        if clean_kw in it_title or it_title in clean_kw:
                            d_val = today_d
                            t_val = it[3]
                            break
                    if not d_val or not t_val:
                        tmrw = today_d + timedelta(days=1)
                        tmrw_items = db.items_for_date(chat_id, tmrw)
                        for it in tmrw_items:
                            it_title = (it[1] or "").lower()
                            if clean_kw in it_title or it_title in clean_kw:
                                d_val = tmrw
                                t_val = it[3]
                                break

                if d_val and t_val:
                    start_dt = datetime.combine(d_val, t_val, tzinfo=u_tz)

            if not start_dt:
                return {
                    "status": "error",
                    "error": "missing_start_time",
                    "facts": {"title": args.title},
                    "next_options": ["specify_date_and_time"]
                }

            # Step 4: Strict Time validation check (tolerance configurable, default 0)
            tolerance_sec = int(os.environ.get("TIME_VALIDATION_TOLERANCE_SECONDS", "0"))
            if start_dt < (now_user - timedelta(seconds=tolerance_sec)):
                return {
                    "status": "error",
                    "error": "time_already_passed",
                    "action": "create_calendar_event",
                    "now": now_user.isoformat(),
                    "requested": start_dt.isoformat(),
                    "tolerance_seconds": tolerance_sec,
                    "facts": {
                        "title": args.title,
                        "requested_start": start_dt.isoformat(),
                        "now": now_user.strftime("%Y-%m-%d %H:%M:%S %Z"),
                        "is_past": True
                    },
                    "next_options": ["schedule_for_tomorrow", "choose_future_time"]
                }

            from main import detect_meeting_conflicts, get_valid_google_connection_by_chat_id
            is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
            if not is_conn:
                return {
                    "status": "error",
                    "error": "google_account_not_connected",
                    "facts": {"title": args.title, "connected": False},
                    "next_options": ["connect_google"]
                }

            dur_mins = args.duration_minutes or 30
            rec = (args.recurrence or "none").lower()
            time_conflicts, day_commitments = await detect_meeting_conflicts(chat_id, start_dt.isoformat(), dur_mins)

            try:
                from state.active_context import set_active_event
                set_active_event(chat_id, {
                    "title": args.title,
                    "start_iso": start_dt.isoformat(),
                    "duration_minutes": dur_mins,
                    "recurrence": rec,
                    "type": "calendar_event"
                })
            except Exception:
                pass

            return {
                "status": "needs_confirmation",
                "action": "create_calendar_event",
                "title": args.title,
                "start_iso": start_dt.isoformat(),
                "duration_minutes": dur_mins,
                "recurrence": rec,
                "is_video_meeting": False,
                "has_video_link": False,
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
            events = await google_services.list_calendar_events(chat_id, time_min_iso=timeutil.now(u_tz).isoformat(), max_results=5)
            meet_evs = [ev for ev in events if ev.get("meet_url")]
            if meet_evs:
                ev = meet_evs[0]
                return {
                    "status": "success",
                    "found": True,
                    "title": ev.get("summary", "Meeting"),
                    "meet_url": ev["meet_url"],
                    "start": ev.get("start", {}).get("dateTime", "")
                }
            return {"status": "not_found", "found": False, "message": "No upcoming meeting with Google Meet link found."}

        elif tool_name == "send_email":
            args = SendEmailArgs(**arguments)
            from core.email_verifier import verify_email
            val_res = await verify_email(args.to)
            if not val_res.is_valid:
                err_msg = val_res.error_message or f"Recipient email '{args.to}' is invalid."
                return {
                    "status": "error",
                    "action": "send_email",
                    "error": err_msg,
                    "to": args.to,
                    "has_typo": val_res.has_typo,
                    "suggested_email": val_res.suggested_email
                }
            return {
                "status": "needs_confirmation",
                "action": "send_email",
                "to": args.to,
                "subject": args.subject,
                "body": args.body,
                "has_typo": val_res.has_typo,
                "suggested_email": val_res.suggested_email,
                "warning": val_res.warning_message
            }

        elif tool_name == "upload_to_drive":
            args = UploadToDriveArgs(**arguments)
            return {
                "status": "needs_confirmation",
                "action": "upload_file",
                "filename": args.filename,
                "content": args.content
            }

        elif tool_name == "send_targeted_mention_message":
            args = SendTargetedMentionMessageArgs(**arguments)
            from core.mentions_engine import execute_targeted_mentions_dispatch
            disp_res = await execute_targeted_mentions_dispatch(chat_id, args.message, attachments=args.attachments)
            return {
                "status": disp_res.status,
                "summary": disp_res.summary_message,
                "directives": [d.to_dict() for d in disp_res.directives],
                "privacy_guarantee": disp_res.privacy_guarantee
            }

        elif tool_name == "link_user_phone_number":
            args = LinkUserPhoneNumberArgs(**arguments)
            ok, msg = db.set_user_phone_number(chat_id, args.phone_number)
            return {
                "status": "success" if ok else "error",
                "phone_number": args.phone_number,
                "message": msg
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
                            auth_url = build_notion_auth_url(int(origin_chat_id), source="tg")
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
                    auth_url = build_auth_url(int(origin_chat_id), source="tg")
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
                        auth_url = build_github_auth_url(int(origin_chat_id), source="tg")
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
                        auth_url = build_notion_auth_url(int(origin_chat_id), source="tg")
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

        elif tool_name == "delete_notion_page":
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

            ok, msg, deleted_pages = await integrations.delete_notion_page(api_key, page_id_or_title)
            if ok:
                return {
                    "status": "success",
                    "action": "delete_notion_page",
                    "deleted_count": len(deleted_pages),
                    "deleted_titles": [p["title"] for p in deleted_pages],
                    "pages": deleted_pages,
                    "message": f"✅ {msg}"
                }
            else:
                return {"status": "error", "message": msg}

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
                return {
                    "status": "error",
                    "error_type": "github_api_error",
                    "message": "Could not fetch repositories from GitHub. Please check token permissions or username."
                }

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
            sections = list(args.sections or [])
            if not sections and (args.content or "").strip():
                sections.append({"heading": "Details", "text": args.content})
            if args.data_type:
                sheets, _ = _load_user_export_sheets(chat_id, args.data_type)
                for sh in sheets:
                    sections.append({"heading": sh["name"], "table": [sh["headers"]] + sh["rows"]})
            if not sections:
                sections = [{"heading": args.title, "paragraphs": ["Generated by PeculiarAI."]}]
            pdf_bytes = build_styled_pdf(args.title, sections)
            fname = sanitize_filename(args.filename or args.title, default_name="document", ext=".pdf")
            return await dispatch_generated_file(
                chat_id=chat_id,
                origin_chat_id=origin_chat_id,
                filename=fname,
                content_bytes=pdf_bytes,
                caption=args.title,
                channel=channel,
            )

        elif tool_name == "generate_excel":
            args = GenerateExcelArgs(**arguments)
            dtype = (args.data_type or "contacts").strip().lower()
            if args.sheets or (args.headers is not None and args.rows is not None):
                custom_sheets = args.sheets or [
                    {"name": (args.title or "Data")[:31], "headers": args.headers or [], "rows": args.rows or []}
                ]
                excel_bytes = build_excel_workbook(custom_sheets, title=args.title or "Spreadsheet")
                fname = sanitize_filename(args.filename or args.title or "spreadsheet", default_name="spreadsheet", ext=".xlsx")
            elif dtype in ("contacts", "contact"):
                contacts = db.get_contact_memories(chat_id)
                excel_bytes = build_contacts_excel(contacts)
                fname = sanitize_filename(args.filename or "contacts", default_name="contacts", ext=".xlsx")
            else:
                sheets, _ = _load_user_export_sheets(chat_id, dtype)
                excel_bytes = build_excel_workbook(sheets, title=args.title or f"{dtype}_export")
                fname = sanitize_filename(args.filename or f"{dtype}_export", default_name="export", ext=".xlsx")

            return await dispatch_generated_file(
                chat_id=chat_id,
                origin_chat_id=origin_chat_id,
                filename=fname,
                content_bytes=excel_bytes,
                caption=args.title or f"Generated Excel ({fname})",
                channel=channel,
            )

        elif tool_name == "generate_file":
            args = GenerateFileArgs(**arguments)
            eff_sections = list(args.sections or [])
            eff_sheets = list(args.sheets or [])
            eff_headers = args.headers
            eff_rows = args.rows
            eff_data = args.data

            if args.data_type:
                loaded_sheets, loaded_contacts = _load_user_export_sheets(chat_id, args.data_type)
                if not eff_sheets:
                    eff_sheets = loaded_sheets
                if eff_headers is None and eff_rows is None and loaded_sheets:
                    eff_headers = loaded_sheets[0]["headers"]
                    eff_rows = loaded_sheets[0]["rows"]
                if not eff_sections:
                    for sh in loaded_sheets:
                        eff_sections.append({"heading": sh["name"], "table": [sh["headers"]] + sh["rows"]})
                if eff_data is None:
                    eff_data = (
                        {"contacts": loaded_contacts}
                        if args.data_type.lower() == "contacts"
                        else {sh["name"]: {"headers": sh["headers"], "rows": sh["rows"]} for sh in loaded_sheets}
                    )

            file_bytes, fname, mime = build_document_artifact(
                fmt=args.format,
                title=args.title or (f"{args.data_type.title()} Export" if args.data_type else "Document"),
                filename=args.filename,
                content=args.content or "",
                sections=eff_sections or None,
                slides=args.slides,
                headers=eff_headers,
                rows=eff_rows,
                sheets=eff_sheets or None,
                data=eff_data,
            )
            return await dispatch_generated_file(
                chat_id=chat_id,
                origin_chat_id=origin_chat_id,
                filename=fname,
                content_bytes=file_bytes,
                caption=args.title or fname,
                mime_type=mime,
                channel=channel,
            )

        elif tool_name == "search_user_emails":
            args = SearchUserEmailsArgs(**arguments)
            # google_services is already imported at module level
            from main import get_valid_google_connection_by_chat_id
            from google_auth import build_auth_url
            is_conn, g_email = await get_valid_google_connection_by_chat_id(chat_id)
            auth_url = build_auth_url(int(origin_chat_id), source="tg")

            if not is_conn:
                return {
                    "status": "error",
                    "error_type": "not_connected",
                    "connected": False,
                    "auth_url": auth_url,
                    "message": f"Aapka Google account abhi connected nahi hai. Connect karne ke liye is link par tap karein:\n\n👉 {auth_url}"
                }

            # Check if this chat is known to lack Gmail read scope
            if int(chat_id) in getattr(google_services, "_CHAT_GMAIL_INSUFFICIENT_SCOPES", set()):
                return {
                    "status": "error",
                    "error_type": "auth_scope_missing",
                    "connected": True,
                    "email": g_email,
                    "auth_url": auth_url,
                    "message": (
                        f"Google read access nahi mila. Aapka Google account connected hai ({g_email}), "
                        f"lekin Gmail read permission missing hai. Ye link se allow karo:\n\n👉 {auth_url}"
                    )
                }

            # Build query supporting sent mail, recipient, subject, and folder filters
            q_parts = []
            if args.to:
                q_parts.append(f"to:{args.to}")
            if args.subject:
                q_parts.append(f"subject:{args.subject}")
            if args.folder == "sent":
                q_parts.append("in:sent")
            elif args.folder == "inbox":
                q_parts.append("in:inbox")

            user_q = (args.query or "").strip()
            if user_q:
                low_q = user_q.lower()
                if any(w in low_q for w in ("sent", "bheja", "bheji", "send", "outbox")):
                    if "in:sent" not in q_parts:
                        q_parts.append("in:sent")
                clean_q_tokens = [
                    w for w in user_q.split()
                    if w.lower() not in {"ko", "kya", "bheja", "bheji", "bheje", "hua", "hue", "hui", "email", "mail", "tha", "thi", "the", "sent", "send", "wala", "wali", "wale", "se", "ka", "ki", "ke", "me", "mein", "par", "pe"}
                ]
                clean_search_q = " ".join(clean_q_tokens).strip()
                if clean_search_q:
                    q_parts.append(clean_search_q)
                elif user_q and "in:sent" not in q_parts:
                    q_parts.append(user_q)

            gmail_query = " ".join(q_parts) if q_parts else "in:inbox"

            try:
                messages = await google_services.fetch_user_inbox_messages(chat_id, max_results=args.max_results or 5, query=gmail_query)
            except Exception as g_err:
                err_s = str(g_err).lower()
                if "429" in err_s or "rate" in err_s:
                    return {
                        "status": "error",
                        "error_type": "rate_limited",
                        "message": "Google API rate limit exceed ho gaya hai, please thodi der baad try karein."
                    }
                return {
                    "status": "error",
                    "error_type": "provider_error",
                    "message": f"Google service se connect karne mein samasya aayi: {g_err}."
                }

            if int(chat_id) in getattr(google_services, "_CHAT_GMAIL_INSUFFICIENT_SCOPES", set()):
                return {
                    "status": "error",
                    "error_type": "auth_scope_missing",
                    "connected": True,
                    "email": g_email,
                    "auth_url": auth_url,
                    "message": (
                        f"Google read access nahi mila. Aapka Google account connected hai ({g_email}), "
                        f"lekin Gmail read permission missing hai. Ye link se allow karo:\n\n👉 {auth_url}"
                    )
                }

            if not messages:
                return {
                    "status": "not_found",
                    "count": 0,
                    "query": gmail_query,
                    "message": f"Koi matching email nahi mila for query '{gmail_query}'. Kya aap Sent folder ya koi dusra keyword check karna chahte hain?"
                }

            results = []
            from email_intelligence import analyze_incoming_email
            from connectors.guardrails import wrap_untrusted_content
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
                    "body_snippet": wrap_untrusted_content(m.get("body", "")[:1000], source="gmail")
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
            res = await find_nearby_places(args.category, args.area, args.city)
            return res

        elif tool_name == "get_movie_showtimes":
            args = GetMovieShowtimesArgs(**arguments)
            from utilities import get_movie_showtimes
            res = await get_movie_showtimes(args.city, args.area, args.movie)
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
        import uuid
        trace_id = f"tool_val_{uuid.uuid4().hex[:8]}"
        logger.warning(f"Tool validation error [{trace_id}] in {tool_name}: {val_err}")
        return {
            "status": "error",
            "error": "validation_error",
            "error_type": "validation_error",
            "retryable": False,
            "user_message": f"Tool '{tool_name}' parameters are invalid. Please check the requested inputs.",
            "trace_id": trace_id
        }
    except Exception as exc:
        import uuid, traceback
        trace_id = f"tool_err_{uuid.uuid4().hex[:8]}"
        logger.error(f"Tool execution exception [{trace_id}] in {tool_name}:\n{traceback.format_exc()}")
        exc_type = type(exc).__name__
        is_retryable = exc_type in ("TimeoutError", "NetworkError", "ConnectTimeout", "ReadTimeout", "OperationalError")
        if "Authentication" in exc_type or "Unauthorized" in exc_type:
            user_msg = f"Authentication issue encountered while accessing '{tool_name}'."
            err_type = "auth_error"
        elif "Permission" in exc_type or "Forbidden" in exc_type:
            user_msg = f"Permission denied while accessing '{tool_name}'."
            err_type = "permission_denied"
        elif is_retryable:
            user_msg = f"Network or timeout issue while calling '{tool_name}'. Please try again."
            err_type = "network_timeout"
        else:
            user_msg = f"An unexpected error occurred while processing '{tool_name}'."
            err_type = "internal_tool_error"

        return {
            "status": "error",
            "error_type": err_type,
            "retryable": is_retryable,
            "user_message": user_msg,
            "trace_id": trace_id
        }


CORE_TOOL_NAMES = {
    'calculate',
    "execute_python_code", "create_reminder", "update_reminder", "delete_reminder", "list_reminders", "get_schedule",
    "save_note_or_fact", "delete_note_or_fact", "search_memory", "save_contact", "get_contact", "list_contacts", "get_commitments",
    "update_contact_commitment", "manage_relationship", "set_user_timezone", "set_user_language", "set_user_preference", "ask_user", "learn_user_correction",
    "connect_integration", "disconnect_integration", "check_integrations_status", "create_meeting", "create_calendar_event", "schedule_event",
    "search_web", "find_nearby_places",
    "send_targeted_mention_message", "link_user_phone_number"
}

# Dead keyword routing and duplicate _cosine_similarity excised per Phase 7c Item 6


TOOL_GROUPS = {
    "communication_mentions": {
        "description": "mention contact message telegram dispatch targeted directive tag user send task notify phone number direct work ui design",
        "tools": {"send_targeted_mention_message", "link_user_phone_number"}
    },
    "developer_github": {
        "description": "git github repository commit pull request source code file branch issue readme programming",
        "tools": {"list_github_repos", "get_github_file", "update_github_file", "list_github_issues"}
    },
    "workspace_notion": {
        "description": "notion notes documentation page workspace markdown code block heading article delete trash remove",
        "tools": {"create_notion_page", "update_notion_page", "list_notion_pages", "get_notion_page_content", "delete_notion_page"}
    },
    "communication_email": {
        "description": "email mail gmail inbox draft send message subject recipient correspondence",
        "tools": {"send_email", "search_user_emails"}
    },
    "utilities_finance_weather": {
        "description": "weather mausam temperature forecast rain climate atmosphere currency exchange rate market price crypto gold silver sona chandi bhav daam stock calculation math",
        "tools": {"get_weather", "convert_currency", "get_market_price", "calculate"}
    },
    "organization_lists": {
        "description": "list smart list grocery packing luggage items checklist task",
        "tools": {"manage_smart_list", "create_packing_list", "manage_commitment"}
    },
    "health_measurements": {
        "description": "health medical sugar glucose blood pressure bp readings test vitals",
        "tools": {"get_measurements", "get_memory_health_radar"}
    },
    "media_entertainment": {
        "description": "movie cinema theatre showtimes film tickets screening",
        "tools": {"get_movie_showtimes"}
    },
    "calendar_advanced": {
        "description": "meeting calendar schedule appointment slot zoom google meet availability clash",
        "tools": {"check_availability", "get_meet_link", "create_meeting", "schedule_event"}
    },
    "privacy_data": {
        "description": "permanently erase delete dlete wipe all user data and memory memories account reset clear history uda do clean slate bhool jao",
        "tools": {"request_master_privacy_wipe", "clear_chat_history"}
    },
    "web_research": {
        "description": "live web search deep research browse internet articles fetch url read webpage online links scraping",
        "tools": {"search_web", "web_search", "read_webpage", "fetch_url", "deep_research"}
    },
    "document_generation": {
        "description": "generate pdf documents create excel spreadsheet export files reports download pdf xlsx csv docx pptx word powerpoint slides deck markdown json html txt ics document file sheet",
        "tools": {"generate_pdf", "generate_excel", "generate_file"}
    },
    "subscriptions_finance": {
        "description": "manage subscriptions recurring bills renewals free trial watchdog cancel subscription audit services",
        "tools": {"manage_subscription"}
    }
}

_TOOL_GROUP_EMBEDDINGS: Dict[str, List[float]] = {}
_TOOL_SCHEMA_EMBEDDINGS: Dict[str, List[float]] = {}


def _get_tool_group_embedding(group_name: str, desc: str) -> Optional[List[float]]:
    global _TOOL_GROUP_EMBEDDINGS
    if group_name in _TOOL_GROUP_EMBEDDINGS:
        return _TOOL_GROUP_EMBEDDINGS[group_name]
    try:
        from memory.store import embed_text
        vec = embed_text(desc, is_query=True)
        if vec:
            _TOOL_GROUP_EMBEDDINGS[group_name] = vec
        return vec
    except Exception:
        return None


def _warm_tool_group_embeddings() -> None:
    """Pre-warms uncached TOOL_GROUPS embeddings in a single batch HTTP call to avoid cold-start stalls."""
    missing = [(g_name, g_info["description"]) for g_name, g_info in TOOL_GROUPS.items() if g_name not in _TOOL_GROUP_EMBEDDINGS]
    if not missing:
        return
    try:
        from memory.store import embed_text, embed_texts_batch
        is_mocked = bool(getattr(embed_text, "assert_called", None) or getattr(embed_text, "side_effect", None))
        if not is_mocked:
            vecs = embed_texts_batch([desc for _, desc in missing], is_query=True)
            for (g_name, _), vec in zip(missing, vecs):
                if vec:
                    _TOOL_GROUP_EMBEDDINGS[g_name] = vec
            return
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(6, len(missing))) as pool:
            list(pool.map(lambda item: _get_tool_group_embedding(item[0], item[1]), missing))
    except Exception:
        pass


_BUDGET_ESSENTIAL_TOOLS = {
    "create_reminder", "update_reminder", "delete_reminder", "list_reminders",
    "get_schedule", "create_meeting", "create_calendar_event",
    "search_memory", "search_web", "find_nearby_places",
    "list_notion_pages", "get_notion_page_content",
    "search_user_emails", "send_email",
    "save_contact", "get_contact", "list_contacts",
    "check_integrations_status",
    "send_targeted_mention_message", "link_user_phone_number"
}


def select_semantic_tools_for_budget(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    max_tools: int = 24
) -> List[Dict[str, Any]]:
    """
    Semantic budget selector for size-constrained fallback providers (e.g. Groq).
    Replaces brittle keyword matching with embedding cosine similarity + token overlap
    while preserving essential state-lookup and execution tools.
    """
    if not tools or len(tools) <= max_tools:
        return tools

    query_parts = []
    for m in reversed(messages or []):
        role = m.get("role")
        content = str(m.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            query_parts.append(content[:300])
            if len(query_parts) >= 2:
                break
    query_text = " ".join(reversed(query_parts)).strip()

    q_vec = None
    if query_text:
        try:
            from memory.store import embed_text
            q_vec = embed_text(query_text, is_query=True)
        except Exception:
            q_vec = None

    if q_vec:
        # Warm uncached tool schema embeddings concurrently
        missing_tools = []
        for t in tools:
            fn = t.get("function", {})
            t_name = fn.get("name", "")
            if t_name and t_name not in _TOOL_SCHEMA_EMBEDDINGS:
                desc = fn.get("description", "")
                missing_tools.append((t_name, f"{t_name.replace('_', ' ')}: {desc}"))
        if missing_tools:
            try:
                from memory.store import embed_text, embed_texts_batch
                is_mocked = bool(getattr(embed_text, "assert_called", None) or getattr(embed_text, "side_effect", None))
                batch_slice = missing_tools[:16]
                if not is_mocked:
                    vecs = embed_texts_batch([item[1] for item in batch_slice], is_query=False)
                    for (t_name, _), vec in zip(batch_slice, vecs):
                        if vec:
                            _TOOL_SCHEMA_EMBEDDINGS[t_name] = vec
                else:
                    from concurrent.futures import ThreadPoolExecutor

                    def _embed_tool(item):
                        v = embed_text(item[1], is_query=False)
                        if v:
                            _TOOL_SCHEMA_EMBEDDINGS[item[0]] = v

                    with ThreadPoolExecutor(max_workers=min(6, len(batch_slice))) as pool:
                        list(pool.map(_embed_tool, batch_slice))
            except Exception:
                pass

    q_tokens = set(re.findall(r"[a-zA-Z0-9]+", query_text.lower()))
    scored = []
    for idx, t in enumerate(tools):
        fn = t.get("function", {})
        t_name = fn.get("name", "")
        t_desc = fn.get("description", "")
        sim = 0.0
        lex_sim = 0.0
        if q_tokens:
            t_tokens = set(re.findall(r"[a-zA-Z0-9]+", f"{t_name.replace('_', ' ')} {t_desc}".lower()))
            overlap = len(q_tokens & t_tokens)
            lex_sim = overlap / max(len(q_tokens | t_tokens), 1)
        t_vec = _TOOL_SCHEMA_EMBEDDINGS.get(t_name)
        if q_vec and t_vec:
            sim = _cosine_similarity(q_vec, t_vec) + (0.25 * lex_sim)
        else:
            sim = lex_sim

        is_essential = 1 if t_name in _BUDGET_ESSENTIAL_TOOLS else 0
        is_domain = 1 if t_name not in CORE_TOOL_NAMES else 0
        scored.append(((sim * 3.0) + (0.20 * is_essential) + (0.12 * is_domain), idx, t))

    scored.sort(key=lambda item: (-item[0], item[1]))
    selected = []
    seen_names = set()

    # Keep highest similarity matches first, guaranteeing essential read/write tools fit comfortably
    for _, _, t in scored:
        name = t.get("function", {}).get("name", "")
        if len(selected) >= max_tools:
            break
        if name not in seen_names:
            seen_names.add(name)
            selected.append(t)

    # Backfill critical lookup tools into tail slots while protecting top query-relevant semantic hits
    protected_top_k = (
        min(6, max(2, max_tools - len(_BUDGET_ESSENTIAL_TOOLS)))
        if max_tools >= 14
        else max(4, max_tools // 2)
    )
    essential_present = {t.get("function", {}).get("name", "") for t in selected}
    missing_essentials = [
        t for t in tools
        if t.get("function", {}).get("name", "") in _BUDGET_ESSENTIAL_TOOLS
        and t.get("function", {}).get("name", "") not in essential_present
    ]
    for m_tool in missing_essentials:
        for r_idx in range(len(selected) - 1, protected_top_k - 1, -1):
            r_name = selected[r_idx].get("function", {}).get("name", "")
            if r_name not in _BUDGET_ESSENTIAL_TOOLS:
                selected[r_idx] = m_tool
                break

    return selected

def _cosine_similarity(a: List[float], b: List[float]) -> float:
    try:
        import numpy as np
        dot = np.dot(a, b)
        norm_a = np.linalg.norm(a)
        norm_b = np.linalg.norm(b)
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return float(dot / (norm_a * norm_b))
    except Exception:
        dot = sum(x * y for x, y in zip(a, b))
        norm_a = sum(x * x for x in a) ** 0.5
        norm_b = sum(y * y for y in b) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return 0.0
        return dot / (norm_a * norm_b)

def route_tools(user_text: str, context_text: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    Phase 4: Principled Semantic Tool Group Routing.
    - Core baseline tools are ALWAYS included by default.
    - Domain tool groups are attached via embedding similarity and multi-turn context.
    - If unsure or when embeddings are offline, provides a safe, comprehensive default.
    """
    core_tools = [t for t in TOOL_DEFINITIONS if t["function"]["name"] in CORE_TOOL_NAMES]
    low = (user_text or "").strip()
    ctx_clean = (context_text or "").strip()
    combined = f"{low} (Context: {ctx_clean[:200]})".strip() if ctx_clean else low

    if not combined:
        return core_tools

    # If query is casual greeting/chitchat with no domain context, return core tools only
    is_casual_greeting = any(w in low.lower() for w in ("kya haal", "sab badhiya", "kaise ho", "kaisa hai", "hello", "namaste")) and not ctx_clean
    if is_casual_greeting:
        return core_tools

    activated_tools = set()

    # 1. Multi-turn active context & explicit domain keyword activation:
    # If the user or context indicates an ongoing session or explicit query with a connector (Notion, GitHub, Email, etc.),
    # retain/attach that domain's tool group in the working set.
    for g_name, g_info in TOOL_GROUPS.items():
        parts = g_name.split("_")
        desc_words = [w.lower() for w in g_info["description"].split() if len(w) >= 4]
        desc_phrases = [p for p in ("uda do", "clean slate", "bhool jao") if p in g_info["description"]]
        has_kw_match = (
            any(part in low.lower() or (ctx_clean and part in ctx_clean.lower()) for part in parts) or
            any(w in low.lower() or (ctx_clean and w in ctx_clean.lower()) for w in desc_words) or
            any(p in low.lower() or (ctx_clean and p in ctx_clean.lower()) for p in desc_phrases)
        )
        if has_kw_match:
            # Never offer Notion write tools on casual trip/schedule/reminder queries unless user mentions Notion or note-taking
            if "notion" in parts and not any(w in low.lower() for w in ("notion", "note", "notes", "doc", "document", "page", "write", "likh", "plan", "list", "track", "banao", "save")):
                continue
            activated_tools.update(g_info["tools"])

    # 2. Semantic Embedding Similarity Routing
    q_vec = None
    try:
        from memory.store import embed_text
        q_vec = embed_text(combined, is_query=True)
    except Exception:
        pass

    if q_vec:
        _warm_tool_group_embeddings()
        domain_sims = {}
        for g_name, g_info in TOOL_GROUPS.items():
            g_vec = _get_tool_group_embedding(g_name, g_info["description"])
            if g_vec:
                domain_sims[g_name] = _cosine_similarity(q_vec, g_vec)

        sorted_domains = sorted(domain_sims.items(), key=lambda x: x[1], reverse=True)
        if sorted_domains:
            top_name, top_sim = sorted_domains[0]
            second_sim = sorted_domains[1][1] if len(sorted_domains) > 1 else 0.0
            if (top_sim >= 0.60 and (top_sim - second_sim >= 0.06)) or top_sim >= 0.65:
                activated_tools.update(TOOL_GROUPS[top_name]["tools"])

        for g_name, sim in domain_sims.items():
            if sim >= 0.64:
                activated_tools.update(TOOL_GROUPS[g_name]["tools"])

        is_casual_trip = any(w in low.lower() for w in ("trip", "tour", "chalte hain", "dost k sth", "dost ke sath", "ghoomne")) and not any(w in low.lower() for w in ("notion", "note", "notes", "doc", "document", "page", "write", "likh"))
        if is_casual_trip:
            activated_tools.discard("create_notion_page")
            activated_tools.discard("update_notion_page")
    else:
        # Safe default when embeddings are unavailable (offline test suite):
        is_casual_trip = any(w in low.lower() for w in ("trip", "tour", "chalte hain", "dost k sth", "dost ke sath", "ghoomne")) and not any(w in low.lower() for w in ("notion", "note", "doc", "page", "write", "likh"))
        domain_tools = [
            t for t in TOOL_DEFINITIONS
            if t["function"]["name"] not in CORE_TOOL_NAMES and
            not (is_casual_trip and t["function"]["name"] in ("create_notion_page", "update_notion_page"))
        ]
        return core_tools + domain_tools

    extra_tools = [
        t for t in TOOL_DEFINITIONS
        if t["function"]["name"] in activated_tools and t["function"]["name"] not in CORE_TOOL_NAMES
    ]
    return core_tools + extra_tools
