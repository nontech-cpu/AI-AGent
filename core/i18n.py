"""
PeculiarAI Internationalization Table (Phase 7e Step 1)
Centralized deterministic text repository keyed by user preferred language (English default).
Used strictly where deterministic system responses are unavoidable (e.g. cancellations, gates).
"""
from typing import Dict, Any

I18N: Dict[str, Dict[str, str]] = {
    "action_cancelled": {
        "english": "Action cancelled. Nothing was scheduled or sent.",
        "hinglish": "Action cancel kar diya gaya hai. Kuch bhi schedule ya send nahi kiya gaya.",
        "hindi": "कार्रवाई रद्द कर दी गई है। कुछ भी शेड्यूल या भेजा नहीं गया।"
    },
    "confirm_meeting": {
        "english": (
            "Please confirm — create this Google Calendar meeting with a Google Meet link?\n"
            "Title: {title}\n"
            "Time: {time}\n"
            "Duration: {duration} minutes\n"
            "Attendees: {attendees}\n\n"
            "Reply yes to create or no to cancel."
        ),
        "hinglish": (
            "Please confirm — create this Google Calendar meeting with a Google Meet link?\n"
            "Title: {title}\n"
            "Time: {time} (India time)\n"
            "Duration: {duration} minutes\n"
            "Attendees: {attendees}\n\n"
            "Reply yes to create or no to cancel."
        )
    },
    "confirm_calendar_event": {
        "english": (
            "Please confirm — add this event to your Google Calendar?\n"
            "Title: {title}\n"
            "Time: {time}\n"
            "Duration: {duration} minutes\n\n"
            "Reply yes to create or no to cancel."
        ),
        "hinglish": (
            "Please confirm — add this event to your Google Calendar?\n"
            "Title: {title}\n"
            "Time: {time} (India time)\n"
            "Duration: {duration} minutes\n\n"
            "Reply yes to create or no to cancel."
        )
    }
}

def get_i18n_string(key: str, lang: str = "english", **kwargs) -> str:
    lang_clean = (lang or "english").strip().lower()
    entry = I18N.get(key, {})
    tmpl = entry.get(lang_clean) or entry.get("english") or ""
    if kwargs:
        try:
            return tmpl.format(**kwargs)
        except Exception:
            return tmpl
    return tmpl

