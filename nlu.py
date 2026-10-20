import re
from difflib import get_close_matches

PERSONA_LABELS = {
    "job": "Job",
    "homemaker": "Housework",
    "business": "Business",
    "student": "Student",
    "other": "Other",
}

# Number 1 se 5 isi order me dikhte hain
PERSONA_ORDER = ["job", "homemaker", "business", "student", "other"]

PERSONA_WORDS = {
    "job": ["job", "jobs", "employee", "service", "naukri", "office", "salaried", "working"],
    "homemaker": ["housework", "house", "home", "homemaker", "housewife", "household",
                  "ghar", "gharelu"],
    "business": ["business", "businessman", "businesswoman", "buisness", "shop",
                 "dukaan", "vyapar", "vyapari", "entrepreneur", "owner"],
    "student": ["student", "students", "study", "studying", "studies", "padhai",
                "college", "school"],
    "other": ["other", "others", "none", "else"],
}
WORD_TO_PERSONA = {w: p for p, words in PERSONA_WORDS.items() for w in words}

YES = {"yes", "y", "yeah", "yep", "yup", "ok", "okay", "k", "haan", "han", "ha", "haa",
       "hanji", "haanji", "haaji", "sure", "agree", "agreed", "theek", "thik", "correct", "right", "sahi",
       "continue", "start", "done", "bilkul", "zaroor", "zarur"}
NO = {"no", "n", "nope", "nah", "nahi", "nahin", "na", "naa", "cancel", "disagree", "mat",
      "stop", "galat", "wrong", "rehne do", "rahne do"}



def _tokens(text):
    return re.findall(r"[a-zA-Z]+|\d+", text.lower())


HINDI_YES = {"हाँ", "हां", "हाँजी", "हाँ जी", "जी", "जी हाँ", "बिल्कुल", "सही", "ठीक", "ज़रूर", "कर दो", "रख दो"}
HINDI_NO = {"नहीं", "ना", "मत", "मत करो", "रद्द", "कैंसिल"}


def yes_no(text):
    """True = yes, False = no, None = samajh nahi aaya"""
    if "👍" in text:
        return True
    low = text.strip().lower()

    # Check whole Hindi tokens to prevent false substring matches (e.g. 'रखना' matching 'ना' or 'मतेशाम' matching 'मत')
    hindi_tokens = set(re.findall(r"[\u0900-\u097F]+", low))
    if any(w in hindi_tokens for w in {"हाँ", "हां", "हाँजी", "जी", "बिल्कुल", "सही", "ठीक", "ज़रूर"}):
        return True
    if any(p in low for p in {"हाँ जी", "जी हाँ", "कर दो", "रख दो", "सेट कर दो", "kar do", "kardo", "rakh do", "rakhdo", "set kar do", "set kardo", "haan ji", "haan bhai"}):
        return True
    if any(w in hindi_tokens for w in {"नहीं", "ना", "मत", "रद्द", "कैंसिल"}):
        return False
    if any(p in low for p in {"मत करो", "रहने दो", "नहीं चाहिए", "mat karo", "rehne do", "rahne do", "nahi chahiye", "nahi bhai"}):
        return False

    for t in _tokens(text):
        if t in YES:
            return True
        if t in NO:
            return False
    return None


def match_persona(text):
    """Returns (persona, 'exact' | 'typo') ya (None, None)"""
    tokens = _tokens(text)
    nums = [t for t in tokens if t in ("1", "2", "3", "4", "5")]
    if len(nums) == 1 and len(tokens) <= 3:
        return PERSONA_ORDER[int(nums[0]) - 1], "exact"
    for t in tokens:
        if t in WORD_TO_PERSONA:
            return WORD_TO_PERSONA[t], "exact"
    for t in tokens:
        if len(t) >= 4:
            close = get_close_matches(t, list(WORD_TO_PERSONA.keys()), n=1, cutoff=0.78)
            if close:
                return WORD_TO_PERSONA[close[0]], "typo"
    return None, None