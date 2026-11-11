"""
PeculiarAI Universal Multi-Tool Schema Metadata & Pre-Execution Policy (Step 9 / Part D)
Single source of truth for:
1. Tool risk level: 'read' | 'reversible_write' | 'external_or_irreversible'
2. Parameter metadata: required (bool), default (optional only), can_be_found_in (candidate context sources).
3. Evidence validation: plain string containment for grounded inputs.
"""
from typing import Dict, Any, List, Optional, Tuple
import re
import unicodedata
import logging

logger = logging.getLogger("tool_metadata")

TOOL_RISK_READ = "read"
TOOL_RISK_REVERSIBLE_WRITE = "reversible_write"
TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE = "external_or_irreversible"

# Master single-source-of-truth metadata catalog for all registered tools
TOOL_METADATA: Dict[str, Dict[str, Any]] = {
    # ----------------- Read Tools -----------------
    "search_memory": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "query": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "time_range": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "get_measurements": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "subject": {"required": False, "default": None, "can_be_found_in": ["user_message", "profile"]},
            "metric": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "date_from": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "date_to": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "limit": {"required": False, "default": 20, "can_be_found_in": []}
        }
    },
    "get_contact": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "name_or_alias": {"required": True, "default": None, "can_be_found_in": ["contacts", "user_message"]}
        }
    },
    "list_contacts": {
        "risk": TOOL_RISK_READ,
        "parameters": {}
    },
    "get_commitments": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "contact": {"required": False, "default": None, "can_be_found_in": ["contacts", "user_message"]}
        }
    },
    "list_reminders": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "date_str": {"required": False, "default": None, "can_be_found_in": ["user_message", "calendar"]}
        }
    },
    "get_schedule": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "target_date": {"required": False, "default": None, "can_be_found_in": ["user_message", "calendar"]}
        }
    },
    "check_availability": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "start_time": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "target_date": {"required": False, "default": None, "can_be_found_in": ["user_message", "calendar"]},
            "duration_minutes": {"required": False, "default": 60, "can_be_found_in": []},
            "activity_type": {"required": False, "default": "general", "can_be_found_in": []}
        }
    },
    "get_meet_link": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "event_title": {"required": False, "default": None, "can_be_found_in": ["calendar", "user_message"]}
        }
    },
    "search_user_emails": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "query": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "sender": {"required": False, "default": None, "can_be_found_in": ["contacts", "user_message"]},
            "to": {"required": False, "default": None, "can_be_found_in": ["contacts", "user_message"]},
            "folder": {"required": False, "default": "inbox", "can_be_found_in": []},
            "max_results": {"required": False, "default": 5, "can_be_found_in": []}
        }
    },
    "search_web": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "query": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "web_search": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "query": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "read_webpage": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "url": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "fetch_url": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "url": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "get_weather": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "city": {"required": False, "default": None, "can_be_found_in": ["memory", "profile", "user_message"]},
            "target_date": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "find_nearby_places": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "query": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "city": {"required": False, "default": None, "can_be_found_in": ["memory", "profile", "user_message"]}
        }
    },
    "get_movie_showtimes": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "city": {"required": False, "default": None, "can_be_found_in": ["memory", "profile", "user_message"]},
            "movie_name": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "convert_currency": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "amount": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "from_currency": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "to_currency": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "get_market_price": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "symbol": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "execute_python_code": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "code": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "purpose": {"required": False, "default": "", "can_be_found_in": ["user_message"]}
        }
    },
    "calculate": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "expression": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "check_integrations_status": {
        "risk": TOOL_RISK_READ,
        "parameters": {}
    },
    "list_github_repos": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "username": {"required": False, "default": None, "can_be_found_in": ["profile", "user_message"]}
        }
    },
    "get_github_file": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "repo": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "path": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "branch": {"required": False, "default": "main", "can_be_found_in": []}
        }
    },
    "list_github_issues": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "repo": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "state": {"required": False, "default": "open", "can_be_found_in": []}
        }
    },
    "list_notion_pages": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "query": {"required": False, "default": "", "can_be_found_in": ["user_message"]}
        }
    },
    "get_notion_page_content": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "page_id": {"required": True, "default": None, "can_be_found_in": ["notion", "user_message"]}
        }
    },

    # ----------------- Reversible Write Tools -----------------
    "create_reminder": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "title": {"required": True, "default": None, "can_be_found_in": ["user_message", "memory"]},
            "due_date": {"required": True, "default": None, "can_be_found_in": ["user_message", "calendar"]},
            "due_time": {"required": True, "default": None, "can_be_found_in": ["user_message", "calendar"]},
            "recurrence": {"required": False, "default": "none", "can_be_found_in": ["user_message"]},
            "duration_minutes": {"required": False, "default": 30, "can_be_found_in": []},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "add_reminder": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "title": {"required": True, "default": None, "can_be_found_in": ["user_message", "memory"]},
            "due_date": {"required": True, "default": None, "can_be_found_in": ["user_message", "calendar"]},
            "due_time": {"required": True, "default": None, "can_be_found_in": ["user_message", "calendar"]},
            "recurrence": {"required": False, "default": "none", "can_be_found_in": ["user_message"]},
            "duration_minutes": {"required": False, "default": 30, "can_be_found_in": []},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "update_reminder": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "title": {"required": False, "default": None, "can_be_found_in": ["reminders", "user_message"]},
            "reminder_id": {"required": False, "default": None, "can_be_found_in": ["reminders"]},
            "due_date": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "due_time": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "duration_minutes": {"required": False, "default": None, "can_be_found_in": []},
            "scope": {"required": False, "default": "all", "can_be_found_in": []},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "restore_reminder": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "reminder_id": {"required": True, "default": None, "can_be_found_in": ["reminders"]}
        }
    },
    "save_contact": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "name": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "phone": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "email": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "role": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "aliases": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "notes": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "update_contact_commitment": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "contact_name": {"required": True, "default": None, "can_be_found_in": ["contacts", "user_message"]},
            "commitment": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "due_date": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "save_note_or_fact": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "text": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "category": {"required": False, "default": "fact", "can_be_found_in": []},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "manage_smart_list": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "action": {"required": True, "default": "add", "can_be_found_in": []},
            "name": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "item": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "create_packing_list": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "destination": {"required": True, "default": None, "can_be_found_in": ["user_message", "memory"]},
            "duration_days": {"required": False, "default": 3, "can_be_found_in": ["user_message"]},
            "purpose": {"required": False, "default": "general", "can_be_found_in": ["user_message"]}
        }
    },
    "set_user_timezone": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "timezone": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "set_user_language": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "language": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "set_user_preference": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "reply_language": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "tone": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },

    # ----------------- External or Irreversible Tools -----------------
    "create_calendar_event": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "title": {"required": True, "default": None, "can_be_found_in": ["user_message", "reminders", "memory"]},
            "start_iso": {"required": True, "default": None, "can_be_found_in": ["user_message", "reminders", "calendar"]},
            "duration_minutes": {"required": False, "default": 30, "can_be_found_in": []},
            "recurrence": {"required": False, "default": "none", "can_be_found_in": ["user_message"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "schedule_event": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "title": {"required": True, "default": None, "can_be_found_in": ["user_message", "reminders", "memory"]},
            "start_iso": {"required": True, "default": None, "can_be_found_in": ["user_message", "reminders", "calendar"]},
            "duration_minutes": {"required": False, "default": 30, "can_be_found_in": []},
            "recurrence": {"required": False, "default": "none", "can_be_found_in": ["user_message"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "create_meeting": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "title": {"required": True, "default": None, "can_be_found_in": ["user_message", "contacts", "calendar"]},
            "start_iso": {"required": True, "default": None, "can_be_found_in": ["user_message", "calendar"]},
            "duration_minutes": {"required": False, "default": 30, "can_be_found_in": []},
            "attendees": {"required": False, "default": [], "can_be_found_in": ["contacts", "user_message"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "update_meeting": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "title": {"required": False, "default": None, "can_be_found_in": ["calendar", "user_message"]},
            "event_id": {"required": False, "default": None, "can_be_found_in": ["calendar"]},
            "start_iso": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "duration_minutes": {"required": False, "default": None, "can_be_found_in": []},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "delete_meeting": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "title": {"required": False, "default": None, "can_be_found_in": ["calendar", "user_message"]},
            "event_id": {"required": False, "default": None, "can_be_found_in": ["calendar"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "delete_reminder": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "title": {"required": False, "default": None, "can_be_found_in": ["reminders", "user_message"]},
            "reminder_id": {"required": False, "default": None, "can_be_found_in": ["reminders"]},
            "target_ids": {"required": False, "default": None, "can_be_found_in": ["reminders"]},
            "scope": {"required": False, "default": "all", "can_be_found_in": []},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "send_email": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "to": {"required": True, "default": None, "can_be_found_in": ["contacts", "user_message"]},
            "subject": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "body": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "upload_to_drive": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "filename": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "content": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "folder_id": {"required": False, "default": None, "can_be_found_in": []},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "create_notion_page": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "title": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "content": {"required": False, "default": "", "can_be_found_in": ["user_message"]},
            "parent_id": {"required": False, "default": None, "can_be_found_in": ["notion"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "update_notion_page": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "page_id": {"required": True, "default": None, "can_be_found_in": ["notion"]},
            "title": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "content": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "update_github_file": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "repo": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "path": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "content": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "commit_message": {"required": False, "default": "Update file via PeculiarAI", "can_be_found_in": []},
            "branch": {"required": False, "default": "main", "can_be_found_in": []},
            "evidence": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "request_master_privacy_wipe": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "confirm": {"required": False, "default": True, "can_be_found_in": []}
        }
    },
    "clear_chat_history": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "confirm": {"required": False, "default": True, "can_be_found_in": []}
        }
    },
    "resolve_pending_action": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "decision": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "corrected_request": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "ask_user": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "question": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "options": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "deep_research": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "topic": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "goal": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "generate_pdf": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "title": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "content": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "generate_excel": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "data_type": {"required": False, "default": "all", "can_be_found_in": ["user_message"]}
        }
    },
    "get_memory_health_radar": {
        "risk": TOOL_RISK_READ,
        "parameters": {}
    },
    "validate_ticket_date": {
        "risk": TOOL_RISK_READ,
        "parameters": {
            "datetime_str": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "delete_note_or_fact": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "query": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "learn_user_correction": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "wrong_concept": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "correct_rule": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "category": {"required": False, "default": "user_feedback", "can_be_found_in": []}
        }
    },
    "manage_commitment": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "action": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "title": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "description": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "target_date": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "condition_type": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "condition_details": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "commitment_id": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "status": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "manage_relationship": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "action": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "name": {"required": False, "default": None, "can_be_found_in": ["user_message", "contacts"]},
            "relationship": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "phone": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "birthday": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "notes": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "manage_subscription": {
        "risk": TOOL_RISK_REVERSIBLE_WRITE,
        "parameters": {
            "action": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "service_name": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "amount": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "billing_cycle": {"required": False, "default": "monthly", "can_be_found_in": ["user_message"]},
            "renewal_date": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "is_trial": {"required": False, "default": False, "can_be_found_in": []},
            "cancel_url": {"required": False, "default": None, "can_be_found_in": []}
        }
    },
    "delete_notion_page": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "page_id_or_title": {"required": True, "default": None, "can_be_found_in": ["user_message", "notion"]},
            "confirm": {"required": False, "default": False, "can_be_found_in": []}
        }
    },
    "connect_integration": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "service": {"required": True, "default": None, "can_be_found_in": ["user_message"]},
            "value": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "disconnect_integration": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "service": {"required": True, "default": None, "can_be_found_in": ["user_message"]}
        }
    },
    "append_notion_page": {
        "risk": TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE,
        "parameters": {
            "page_id": {"required": True, "default": None, "can_be_found_in": ["notion"]},
            "title": {"required": False, "default": None, "can_be_found_in": ["user_message"]},
            "content": {"required": False, "default": None, "can_be_found_in": ["user_message"]}
        }
    }
}


def normalize_string_for_containment(s: str) -> str:
    """Normalizes Unicode, whitespace, and case for plain string containment checks."""
    if not s:
        return ""
    norm = unicodedata.normalize("NFKC", str(s)).strip().lower()
    return re.sub(r"\s+", " ", norm)


def get_tool_metadata(tool_name: str) -> Dict[str, Any]:
    """Returns single source of truth metadata for tool_name, or default read metadata if unlisted."""
    return TOOL_METADATA.get(tool_name, {
        "risk": TOOL_RISK_READ,
        "parameters": {}
    })


def validate_evidence(
    tool_name: str,
    arguments: Dict[str, Any],
    recent_user_messages: List[str],
    turn_evidence_context: Optional[str] = None
) -> Tuple[bool, List[str]]:
    """
    Plain string containment evidence validator (D3).
    Verifies that quotes claimed for required parameters exist in normalized recent user messages
    or turn evidence context.
    Returns: (is_valid: bool, unverified_fields: List[str])
    """
    meta = get_tool_metadata(tool_name)
    if meta.get("risk") == TOOL_RISK_READ:
        return True, []

    evidence_dict = arguments.get("evidence")
    if not evidence_dict or not isinstance(evidence_dict, dict):
        # If no evidence passed, allow schema defaults or check required fields
        return True, []

    normalized_corpus = " ".join([normalize_string_for_containment(m) for m in recent_user_messages])
    if turn_evidence_context:
        normalized_corpus += " " + normalize_string_for_containment(turn_evidence_context)

    unverified = []
    for field_name, ev in evidence_dict.items():
        if not isinstance(ev, dict):
            continue
        quote = ev.get("quote")
        source = ev.get("source", "user_message")
        if source in ("schema_default", "system"):
            continue
        if quote:
            norm_q = normalize_string_for_containment(quote)
            if norm_q and norm_q not in normalized_corpus:
                unverified.append(field_name)

    return (len(unverified) == 0, unverified)


def validate_tool_call_parameters(
    tool_name: str,
    arguments: Dict[str, Any],
    user_context: Optional[Dict[str, Any]] = None
) -> Tuple[bool, Optional[Dict[str, Any]]]:
    """
    Generic Pre-Execution Validator (D2).
    Checks that all required fields are present and non-empty.
    If an essential field is missing, returns needs_info with zero side effects.
    """
    meta = get_tool_metadata(tool_name)
    params_meta = meta.get("parameters", {})
    missing = []
    can_be_found = set()

    for param_name, p_info in params_meta.items():
        if p_info.get("required"):
            val = arguments.get(param_name)
            if val is None or (isinstance(val, str) and not val.strip()):
                missing.append(param_name)
                can_be_found.update(p_info.get("can_be_found_in", []))

    if missing:
        # Pre-execution validator intercepts before any side effect occurs!
        return False, {
            "status": "needs_info",
            "action": tool_name,
            "tool_name": tool_name,
            "missing": missing,
            "can_be_found_in": sorted(list(can_be_found)),
            "suggestions": [],
            "facts": {
                "reason": f"Required parameter(s) {missing} missing for {tool_name}."
            },
            "next_options": ["ask_user", "lookup_context"]
        }

    return True, None
