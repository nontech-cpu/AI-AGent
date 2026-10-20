"""
Unit tests for NLU, timing parsing, Devanagari numerals, and sensitive data detection.
"""
import pytest
from datetime import datetime, date, time
from zoneinfo import ZoneInfo
import llm
import nlu

IST = ZoneInfo("Asia/Kolkata")


def test_sensitive_detection():
    # Passwords, OTPs, CVVs, Card Numbers must be flagged
    assert llm.is_sensitive("My OTP is 482910") is True
    assert llm.is_sensitive("here is my password: secret123") is True
    assert llm.is_sensitive("card number 4111 2222 3333 4444") is True
    assert llm.is_sensitive("cvv 992") is True

    # Normal reminders must NOT be flagged
    assert llm.is_sensitive("Electricity bill 1200 due on 25 Oct") is False
    assert llm.is_sensitive("Meeting tomorrow at 5 PM") is False
    assert llm.is_sensitive("Call Devender at 3 PM") is False


def test_explicit_calendar_action_detection():
    # Explicit scheduling/hosting triggers
    assert llm.is_explicit_calendar_action("host a meeting with Devender tomorrow at 3 PM") is True
    assert llm.is_explicit_calendar_action("Google calendar me meeting schedule kar do") is True
    assert llm.is_explicit_calendar_action("create a Google Meet link for 5 PM") is True
    assert llm.is_explicit_calendar_action("Devender ke sath meeting host kar do") is True
    assert llm.is_explicit_calendar_action("मीटिंग होस्ट कर दे आज शाम 5 बजे") is True

    # Mere statements or queries should NOT trigger calendar event creation
    assert llm.is_explicit_calendar_action("I have a meeting tomorrow at 5 PM") is False
    assert llm.is_explicit_calendar_action("what meetings do I have today?") is False
    assert llm.is_explicit_calendar_action("show today's agenda") is False


def test_word_numbers_devanagari_and_hinglish():
    # Devanagari numerals
    assert llm._WORD_NUMBERS["एक"] == "1"
    assert llm._WORD_NUMBERS["दो"] == "2"
    assert llm._WORD_NUMBERS["तीन"] == "3"
    assert llm._WORD_NUMBERS["चार"] == "4"
    assert llm._WORD_NUMBERS["पाँच"] == "5"

    # Hinglish words
    assert llm._WORD_NUMBERS["teen"] == "3"
    assert llm._WORD_NUMBERS["paanch"] == "5"
    assert llm._WORD_NUMBERS["saat"] == "7"


def test_parse_followup_time():
    now = datetime(2026, 10, 20, 10, 0, tzinfo=IST)

    # Specific time tomorrow
    due_time, due_date = llm.parse_followup_time("kal subah 11:00 AM", now)
    assert due_time == time(11, 0)
    assert due_date == date(2026, 10, 21)

    # Evening time today
    due_time, due_date = llm.parse_followup_time("aaj shaam 5 baje", now)
    assert due_time == time(17, 0)
    assert due_date == date(2026, 10, 20)

    # Devanagari time
    due_time, due_date = llm.parse_followup_time("तीन बजे", now)
    assert due_time == time(15, 0)


def test_multilingual_yes_no_fast_paths():
    # Affirmatives
    assert nlu.yes_no("yes") is True
    assert nlu.yes_no("haan") is True
    assert nlu.yes_no("haanji") is True
    assert nlu.yes_no("kar do") is True
    assert nlu.yes_no("rakh do") is True
    assert nlu.yes_no("हाँ") is True
    assert nlu.yes_no("बिल्कुल") is True

    # Negatives
    assert nlu.yes_no("no") is False
    assert nlu.yes_no("nahi") is False
    assert nlu.yes_no("nahin") is False
    assert nlu.yes_no("mat karo") is False
    assert nlu.yes_no("rehne do") is False
    assert nlu.yes_no("cancel") is False
    assert nlu.yes_no("नहीं") is False

    # Ambiguous / Non-yes/no
    assert nlu.yes_no("who is attending?") is None
    assert nlu.yes_no("tell me more") is None
    assert nlu.yes_no("what time?") is None


def test_meeting_title_extraction():
    assert "Devender" in llm._meeting_title("Meeting with Devender tomorrow at 5 PM")
    assert "Sharma" in llm._meeting_title("meri Sharma ji ke saath meeting")
    assert llm._meeting_title("I have an appointment tomorrow") == "Meeting"
