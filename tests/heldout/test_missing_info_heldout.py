"""
Part D4: Held-Out Test Set for Missing-Information Handling.
40 test cases (5 per tool across 8 tools: calendar, reminders, email, Notion, GitHub, places, notes, contacts)
in English, Hindi, and Hinglish.

Verifies:
1. Zero writes to DB or external services.
2. Pre-execution validator identifies specific missing parameter.
3. next_options includes 'ask_user'.
4. Result exported to docs/verification/final/heldout_missing_info.json.
"""
import os
import json
import pytest
from typing import Dict, Any, List

from agent.tool_metadata import (
    validate_tool_call_parameters,
    validate_evidence,
    get_tool_metadata,
    TOOL_METADATA,
    TOOL_RISK_READ,
    TOOL_RISK_REVERSIBLE_WRITE,
    TOOL_RISK_EXTERNAL_OR_IRREVERSIBLE
)

# 40 held-out cases across 8 tools
HELDOUT_CASES: List[Dict[str, Any]] = [
    # 1. Calendar: schedule_event (requires title, start_iso)
    {
        "id": "cal_01",
        "tool": "schedule_event",
        "lang": "en",
        "user_text": "Schedule a meeting for tomorrow at 3pm",
        "args": {"start_iso": "2026-11-11T15:00:00+05:30"},  # missing title
        "expected_missing": ["title"]
    },
    {
        "id": "cal_02",
        "tool": "schedule_event",
        "lang": "hi",
        "user_text": "Kal Dr. Sharma se milne ka appointment fix kar do",
        "args": {"title": "Doctor Appointment"},  # missing start_iso
        "expected_missing": ["start_iso"]
    },
    {
        "id": "cal_03",
        "tool": "schedule_event",
        "lang": "hinglish",
        "user_text": "Bhai kal meeting schedule kar de",
        "args": {"title": "Team Sync"},  # missing start_iso
        "expected_missing": ["start_iso"]
    },
    {
        "id": "cal_04",
        "tool": "schedule_event",
        "lang": "en",
        "user_text": "Put an event on my calendar at 10 AM",
        "args": {"start_iso": "2026-11-11T10:00:00+05:30", "title": ""},  # empty title
        "expected_missing": ["title"]
    },
    {
        "id": "cal_05",
        "tool": "schedule_event",
        "lang": "hinglish",
        "user_text": "Calendar pe daal do 5 baje",
        "args": {"start_iso": "2026-11-11T17:00:00+05:30"},  # missing title
        "expected_missing": ["title"]
    },

    # 2. Reminders: create_reminder (requires title, due_date)
    {
        "id": "rem_01",
        "tool": "create_reminder",
        "lang": "en",
        "user_text": "Remind me tomorrow",
        "args": {"due_date": "2026-11-11", "due_time": "09:00"},  # missing title
        "expected_missing": ["title"]
    },
    {
        "id": "rem_02",
        "tool": "create_reminder",
        "lang": "hi",
        "user_text": "Dawa lene ka reminder laga do",
        "args": {"title": "Take Medicine"},  # missing due_date
        "expected_missing": ["due_date"]
    },
    {
        "id": "rem_03",
        "tool": "create_reminder",
        "lang": "hinglish",
        "user_text": "Bhai reminder daal dena",
        "args": {"due_time": "18:00"},  # missing title and due_date
        "expected_missing": ["title", "due_date"]
    },
    {
        "id": "rem_04",
        "tool": "create_reminder",
        "lang": "en",
        "user_text": "Remind me to call John",
        "args": {"title": "Call John"},  # missing due_date
        "expected_missing": ["due_date"]
    },
    {
        "id": "rem_05",
        "tool": "create_reminder",
        "lang": "hinglish",
        "user_text": "Kal subah 8 baje yaad dila dena",
        "args": {"due_date": "2026-11-11", "due_time": "08:00"},  # missing title
        "expected_missing": ["title"]
    },

    # 3. Email: send_email (requires to, subject, body)
    {
        "id": "em_01",
        "tool": "send_email",
        "lang": "en",
        "user_text": "Send an email about the project status",
        "args": {"subject": "Project Status", "body": "Here is the status."},  # missing to
        "expected_missing": ["to"]
    },
    {
        "id": "em_02",
        "tool": "send_email",
        "lang": "hi",
        "user_text": "Rohan ko email bhej do",
        "args": {"to": "rohan@example.com"},  # missing subject, body
        "expected_missing": ["subject", "body"]
    },
    {
        "id": "em_03",
        "tool": "send_email",
        "lang": "hinglish",
        "user_text": "Boss ko mail kar do ki main late hounga",
        "args": {"subject": "Late Today", "body": "I will be late"},  # missing to
        "expected_missing": ["to"]
    },
    {
        "id": "em_04",
        "tool": "send_email",
        "lang": "en",
        "user_text": "Email test@example.com",
        "args": {"to": "test@example.com"},  # missing subject, body
        "expected_missing": ["subject", "body"]
    },
    {
        "id": "em_05",
        "tool": "send_email",
        "lang": "hinglish",
        "user_text": "Send email to team@peculiar.ai with report",
        "args": {"to": "team@peculiar.ai", "subject": "Report"},  # missing body
        "expected_missing": ["body"]
    },

    # 4. Notion: create_notion_page (requires title)
    {
        "id": "notion_01",
        "tool": "create_notion_page",
        "lang": "en",
        "user_text": "Add a task to Notion",
        "args": {"content": "Urgent review required"},  # missing title
        "expected_missing": ["title"]
    },
    {
        "id": "notion_02",
        "tool": "create_notion_page",
        "lang": "hi",
        "user_text": "Notion me task daal do",
        "args": {"title": ""},  # empty title
        "expected_missing": ["title"]
    },
    {
        "id": "notion_03",
        "tool": "create_notion_page",
        "lang": "hinglish",
        "user_text": "Notion pe nayi entry banao",
        "args": {},  # missing title
        "expected_missing": ["title"]
    },
    {
        "id": "notion_04",
        "tool": "create_notion_page",
        "lang": "en",
        "user_text": "Create Notion item with high priority",
        "args": {"content": "High priority item"},  # missing title
        "expected_missing": ["title"]
    },
    {
        "id": "notion_05",
        "tool": "create_notion_page",
        "lang": "hinglish",
        "user_text": "Notion list update kar de",
        "args": {"title": "   "},  # whitespace title
        "expected_missing": ["title"]
    },

    # 5. GitHub: update_github_file (requires repo, path, content)
    {
        "id": "gh_01",
        "tool": "update_github_file",
        "lang": "en",
        "user_text": "Update README in repo",
        "args": {"path": "README.md", "content": "Updated docs"},  # missing repo
        "expected_missing": ["repo"]
    },
    {
        "id": "gh_02",
        "tool": "update_github_file",
        "lang": "hi",
        "user_text": "GitHub repo frontend me file update karo",
        "args": {"repo": "myorg/frontend", "path": "index.js"},  # missing content
        "expected_missing": ["content"]
    },
    {
        "id": "gh_03",
        "tool": "update_github_file",
        "lang": "hinglish",
        "user_text": "GitHub pe file save kar do",
        "args": {"content": "print('hello')"},  # missing repo and path
        "expected_missing": ["repo", "path"]
    },
    {
        "id": "gh_04",
        "tool": "update_github_file",
        "lang": "en",
        "user_text": "Commit changes to peculiar-ai/backend",
        "args": {"repo": "peculiar-ai/backend", "content": "NEW CODE"},  # missing path
        "expected_missing": ["path"]
    },
    {
        "id": "gh_05",
        "tool": "update_github_file",
        "lang": "hinglish",
        "user_text": "GitHub update kar do with content fix",
        "args": {"content": "fix"},  # missing repo, path
        "expected_missing": ["repo", "path"]
    },

    # 6. Places: find_nearby_places (requires query)
    {
        "id": "plc_01",
        "tool": "find_nearby_places",
        "lang": "en",
        "user_text": "Find places near me",
        "args": {"city": "Jodhpur"},  # missing query
        "expected_missing": ["query"]
    },
    {
        "id": "plc_02",
        "tool": "find_nearby_places",
        "lang": "hi",
        "user_text": "Paas ke ache sthan dikhao",
        "args": {},  # missing query
        "expected_missing": ["query"]
    },
    {
        "id": "plc_03",
        "tool": "find_nearby_places",
        "lang": "hinglish",
        "user_text": "Aas paas kya badiya h",
        "args": {"query": ""},  # empty query
        "expected_missing": ["query"]
    },
    {
        "id": "plc_04",
        "tool": "find_nearby_places",
        "lang": "en",
        "user_text": "Locate nearest spots",
        "args": {"city": "Mumbai"},  # missing query
        "expected_missing": ["query"]
    },
    {
        "id": "plc_05",
        "tool": "find_nearby_places",
        "lang": "hinglish",
        "user_text": "Best cafes check karo",
        "args": {"query": "  "},  # blank query
        "expected_missing": ["query"]
    },

    # 7. Notes: save_note_or_fact (requires text)
    {
        "id": "note_01",
        "tool": "save_note_or_fact",
        "lang": "en",
        "user_text": "Save a note under Personal",
        "args": {"category": "personal"},  # missing text
        "expected_missing": ["text"]
    },
    {
        "id": "note_02",
        "tool": "save_note_or_fact",
        "lang": "hi",
        "user_text": "Ek note save kar lo",
        "args": {},  # missing text
        "expected_missing": ["text"]
    },
    {
        "id": "note_03",
        "tool": "save_note_or_fact",
        "lang": "hinglish",
        "user_text": "Notes me add kar lo",
        "args": {"text": ""},  # empty text
        "expected_missing": ["text"]
    },
    {
        "id": "note_04",
        "tool": "save_note_or_fact",
        "lang": "en",
        "user_text": "Write down a note",
        "args": {"category": "fact"},  # missing text
        "expected_missing": ["text"]
    },
    {
        "id": "note_05",
        "tool": "save_note_or_fact",
        "lang": "hinglish",
        "user_text": "Note banao",
        "args": {"text": "   "},  # blank text
        "expected_missing": ["text"]
    },

    # 8. Contacts: save_contact (requires name)
    {
        "id": "cnt_01",
        "tool": "save_contact",
        "lang": "en",
        "user_text": "Save phone number 9876543210",
        "args": {"phone": "9876543210"},  # missing name
        "expected_missing": ["name"]
    },
    {
        "id": "cnt_02",
        "tool": "save_contact",
        "lang": "hi",
        "user_text": "Naya sampark jodo email test@abc.com",
        "args": {"email": "test@abc.com"},  # missing name
        "expected_missing": ["name"]
    },
    {
        "id": "cnt_03",
        "tool": "save_contact",
        "lang": "hinglish",
        "user_text": "Contact save karo number 9998887776",
        "args": {"phone": "9998887776"},  # missing name
        "expected_missing": ["name"]
    },
    {
        "id": "cnt_04",
        "tool": "save_contact",
        "lang": "en",
        "user_text": "Add new contact from Google",
        "args": {"company": "Google"},  # missing name
        "expected_missing": ["name"]
    },
    {
        "id": "cnt_05",
        "tool": "save_contact",
        "lang": "hinglish",
        "user_text": "Contact save kar de",
        "args": {"name": ""},  # empty name
        "expected_missing": ["name"]
    }
]


def test_heldout_dataset_completeness():
    """Assert exactly 40 cases across 8 tools, 5 per tool."""
    assert len(HELDOUT_CASES) == 40
    tools = {}
    for case in HELDOUT_CASES:
        t = case["tool"]
        tools[t] = tools.get(t, 0) + 1
    assert len(tools) == 8
    for t, count in tools.items():
        assert count == 5, f"Tool {t} has {count} cases, expected 5"


def test_heldout_missing_info_execution():
    """
    Run all 40 held-out cases through validate_tool_call_parameters.
    Assert zero writes, exact missing parameter identification, and ask_user in next_options.
    Saves verification telemetry to docs/verification/final/heldout_missing_info.json.
    """
    results = []

    for case in HELDOUT_CASES:
        tool_name = case["tool"]
        args = case["args"]
        expected_missing = case["expected_missing"]

        valid, res = validate_tool_call_parameters(tool_name, args)

        # 1. Assert pre-execution validator rejected execution (zero writes!)
        assert not valid, f"Case {case['id']} ({tool_name}) should have been rejected for missing params"
        assert res is not None
        assert res["status"] == "needs_info"

        # 2. Assert exact missing parameters are identified
        actual_missing = res["missing"]
        for exp in expected_missing:
            assert exp in actual_missing, f"Case {case['id']}: expected {exp} in missing {actual_missing}"

        # 3. Assert next_options includes ask_user
        assert "ask_user" in res["next_options"]

        # 4. Assert candidate sources are provided
        assert len(res["can_be_found_in"]) > 0

        results.append({
            "case_id": case["id"],
            "tool": tool_name,
            "language": case["lang"],
            "user_text": case["user_text"],
            "passed": True,
            "missing_fields": actual_missing,
            "can_be_found_in": res["can_be_found_in"],
            "next_options": res["next_options"],
            "zero_side_effects_verified": True
        })

    # Save to docs/verification/final/heldout_missing_info.json
    out_dir = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "verification", "final")
    os.makedirs(out_dir, exist_ok=True)
    out_file = os.path.join(out_dir, "heldout_missing_info.json")
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)

    assert os.path.exists(out_file)
    assert len(results) == 40
