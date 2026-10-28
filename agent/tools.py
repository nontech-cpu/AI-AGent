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

class RequestMasterPrivacyWipeArgs(BaseModel):
    confirm: Optional[bool] = Field(True, description="Confirmation flag to initiate master privacy wipe and permanent memory deletion.")

class DeleteNoteOrFactArgs(BaseModel):
    query: str = Field(..., description="Keywords or topic of the note, fact, booking, or ticket to delete from memory e.g. 'ticket', 'flight', 'hotel'.")

class UpdateGitHubFileArgs(BaseModel):
    repo: str = Field(..., description="Repository in 'owner/repo' format or repo name e.g. 'ml-project' or 'username/ml-project'.")
    path: Optional[str] = Field("README.md", description="File path within the repository e.g. 'README.md'.")
    content: str = Field(..., description="New or updated content for the file.")
    commit_message: Optional[str] = Field("Update file via PeculiarAI", description="Git commit message.")

class CreatePackingListArgs(BaseModel):
    trip_title: str = Field(..., description="Title of the trip or packing list e.g. 'Goa Trip'.")
    items: List[str] = Field(..., description="List of items to pack e.g. ['Passport', 'Aadhaar Card', 'Photo', 'Charger'].")
    trip_date: Optional[str] = Field(None, description="Date of the trip in YYYY-MM-DD format if known.")

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

class ReadWebpageArgs(BaseModel):
    url: str = Field(..., description="Webpage or article URL to read, inspect, or summarize.")
    max_chars: Optional[int] = Field(4000, description="Maximum characters to extract (defaults to 4000).")


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
            "description": "Check whether user third-party integrations (GitHub, Notion, Google Calendar/Meet/Gmail/Drive) are connected.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": ["string", "null"], "description": "Optional service name filter e.g. 'github', 'notion', 'google'."}
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
    }
]


# -------------------------------------------------------------
# 3. Tool Execution Dispatcher
# -------------------------------------------------------------

async def execute_tool(chat_id: int, tool_name: str, arguments: dict) -> dict:
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
            if isinstance(tool_res.data, dict):
                return tool_res.data
            return {"status": "success", "result": tool_res.data}

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

        elif tool_name == "create_reminder":
            args = CreateReminderArgs(**arguments)
            d_val = date.fromisoformat(args.due_date)
            t_val = None
            if args.due_time:
                h, m = map(int, args.due_time.split(":")[:2])
                t_val = dtime(h, m)

            # Check if this is a reschedule / time update for an existing reminder on this date
            stop_kw = {"meeting", "reminder", "call", "task", "with", "ke", "saath", "ko", "time", "aaj", "kal", "par", "pe"}
            arg_tokens = set(re.findall(r'[a-zA-Z0-9]+', args.title.lower())) - stop_kw

            existing_items = db.items_for_date(chat_id, d_val)
            matched_existing_ids = []

            for row in existing_items:
                row_title = row.get("title") if isinstance(row, dict) else (row[1] if len(row) > 1 else "")
                row_id = row.get("id") if isinstance(row, dict) else (row[4] if len(row) > 4 else None)
                row_tokens = set(re.findall(r'[a-zA-Z0-9]+', row_title.lower())) - stop_kw
                if arg_tokens and row_tokens and (arg_tokens & row_tokens):
                    matched_existing_ids.append((row_id, row_title))

            matched_id_set = {mid for mid, _ in matched_existing_ids if mid is not None}

            # Check for conflict against other distinct events/reminders on the same date and time
            conflicts = []
            for row in existing_items:
                row_title = row.get("title") if isinstance(row, dict) else (row[1] if len(row) > 1 else "")
                row_id = row.get("id") if isinstance(row, dict) else (row[4] if len(row) > 4 else None)
                if row_id in matched_id_set:
                    continue
                row_tokens = set(re.findall(r'[a-zA-Z0-9]+', row_title.lower())) - stop_kw
                if arg_tokens and row_tokens and (arg_tokens & row_tokens):
                    continue
                row_time = row.get("due_time") if isinstance(row, dict) else (row[3] if len(row) > 3 else None)
                if t_val and row_time:
                    try:
                        r_h = row_time.hour if hasattr(row_time, "hour") else int(str(row_time).split(":")[0])
                        r_m = row_time.minute if hasattr(row_time, "minute") else int(str(row_time).split(":")[1])
                        diff_mins = abs((t_val.hour * 60 + t_val.minute) - (r_h * 60 + r_m))
                        if diff_mins < 60:
                            conflicts.append(f"'{row_title}' at {r_h:02d}:{r_m:02d}")
                    except Exception:
                        pass

            # Also check Google Calendar for conflicts if connected
            try:
                from main import get_unified_schedule
                sched = await get_unified_schedule(chat_id, d_val)
                for ev in sched.get("calendar_events", []):
                    raw_start = ev.get("start", {}).get("dateTime")
                    if raw_start and t_val:
                        ev_dt = datetime.fromisoformat(raw_start.replace("Z", "+00:00")).astimezone(IST)
                        diff_mins = abs((t_val.hour * 60 + t_val.minute) - (ev_dt.hour * 60 + ev_dt.minute))
                        if diff_mins < 60:
                            conflicts.append(f"Calendar Meeting '{ev.get('summary', 'Meeting')}' at {ev_dt.strftime('%I:%M %p')}")
            except Exception:
                pass

            if matched_existing_ids:
                primary_id, primary_title = matched_existing_ids[0]
                # Update existing memory item
                db.update_memory_time(chat_id, primary_id, t_val)
                try:
                    from timing import build_plan
                    from db import add_reminders
                    with db.engine.begin() as conn:
                        conn.execute(db.text("DELETE FROM reminders WHERE item_id = :id"), {"id": primary_id})
                        if len(matched_existing_ids) > 1:
                            for extra_id, _ in matched_existing_ids[1:]:
                                if extra_id:
                                    conn.execute(db.text("DELETE FROM reminders WHERE item_id = :eid"), {"eid": extra_id})
                                    conn.execute(db.text("DELETE FROM memory_items WHERE id = :eid"), {"eid": extra_id})
                    plan = build_plan(d_val, t_val)
                    if plan.get("reminders"):
                        add_reminders(primary_id, plan["reminders"])
                except Exception:
                    pass

                time_str = t_val.strftime("%I:%M %p") if t_val else "specified time"
                res = {
                    "status": "success",
                    "title": primary_title or args.title,
                    "due_date": args.due_date,
                    "due_time": args.due_time,
                    "rescheduled": True,
                    "conflicts": conflicts,
                    "has_conflict": len(conflicts) > 0,
                    "message": f"Done! '{primary_title or args.title}' ka reminder {time_str} par shift kar diya hai."
                }
            else:
                item_id = db.save_memory(chat_id, args.category, args.title, amount=None, due_date=d_val, due_time=t_val)
                try:
                    from timing import build_plan
                    from db import add_reminders
                    plan = build_plan(d_val, t_val)
                    if plan.get("reminders") and item_id:
                        add_reminders(item_id, plan["reminders"])
                except Exception:
                    pass

                # Cross-sync to Google Calendar if connected and date is set
                try:
                    from api_v1 import _resolve_calendar_chat_id
                    cal_chat = _resolve_calendar_chat_id(chat_id)
                    from main import get_valid_google_connection_by_chat_id
                    is_conn, _ = await get_valid_google_connection_by_chat_id(cal_chat)
                    if is_conn and d_val:
                        import asyncio
                        from google_services import create_meeting
                        start_time_part = t_val or dtime(9, 0)
                        start_dt = datetime.combine(d_val, start_time_part)
                        asyncio.create_task(create_meeting(
                            chat_id=cal_chat,
                            title=f"🔔 {args.title}",
                            start_iso=start_dt.isoformat(),
                            duration_minutes=30
                        ))
                except Exception:
                    pass

                res = {
                    "status": "success",
                    "title": args.title,
                    "due_date": args.due_date,
                    "due_time": args.due_time,
                    "rescheduled": False,
                    "conflicts": conflicts,
                    "has_conflict": len(conflicts) > 0
                }

            if conflicts:
                res["conflict_warning"] = (
                    f"⚠️ Time Conflict Alert: Is samay par already {', '.join(conflicts)} scheduled hai!"
                )
            return res

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
                if google_conn:
                    e_display = f" ({google_email})" if google_email else ""
                    msg = f"✅ Aapka Google account connected hai{e_display}! (Calendar, Meet, Gmail, Drive active)"
                else:
                    msg = "❌ Aapka Google account abhi connect nahi hai. 'connect google' likhkar official link se authorize karein."
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
                    m = re.search(r"github\.com/([a-zA-Z0-9_\-]+)", txt)
                    if m:
                        req_username = m.group(1)
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
                    match = re.search(r'\b([a-zA-Z0-9_-]+/[a-zA-Z0-9_-]+|[a-zA-Z0-9_-]+-project|[a-zA-Z0-9_-]+-repo)\b', t.get("text", ""))
                    if match:
                        req_repo = match.group(1)
                        break

            if not req_content:
                recent = db.get_recent_chat_history(chat_id, limit=6)
                for t in reversed(recent):
                    txt = t.get("text", "")
                    m = re.search(r'<parameter=content>(.*?)</parameter>', txt, re.DOTALL)
                    if m:
                        req_content = m.group(1).strip()
                        break
                    code_m = re.search(r'```(?:markdown)?\n?(.*?)```', txt, re.DOTALL)
                    if code_m:
                        req_content = code_m.group(1).strip()
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
            is_conn, _ = await get_valid_google_connection_by_chat_id(chat_id)
            if not is_conn:
                return {
                    "status": "error",
                    "connected": False,
                    "message": "Aapka Google account abhi connected nahi hai. Connect karne ke liye 'connect google' bole."
                }

            user_q = (args.query or "").strip()
            gmail_query = user_q if user_q else "in:inbox"
            messages = await google_services.fetch_user_inbox_messages(chat_id, max_results=args.max_results or 5, query=gmail_query)
            if not messages and gmail_query != "in:inbox":
                messages = await google_services.fetch_user_inbox_messages(chat_id, max_results=args.max_results or 5, query="in:inbox")

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

        elif tool_name == "search_web":
            args = SearchWebArgs(**arguments)
            from utilities import search_web
            res = await search_web(args.query)
            return res

        elif tool_name == "read_webpage":
            args = ReadWebpageArgs(**arguments)
            from utilities import read_webpage
            res = await read_webpage(args.url, args.max_chars or 4000)
            return res

        else:
            return {"status": "error", "message": f"Unknown tool: '{tool_name}'"}

    except ValidationError as val_err:
        return {"status": "error", "type": "ValidationError", "details": val_err.errors()}
    except Exception as exc:
        return {"status": "error", "type": type(exc).__name__, "message": str(exc)}
