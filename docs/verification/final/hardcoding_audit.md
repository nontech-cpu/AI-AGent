# Part B: Hardcoding Audit & Elimination Report

**Branch**: `fix/final-hardening`  
**Base Commit (Pre-7c)**: `4c042fb`  
**Audit Target**: `4c042fb..HEAD` across `agent/`, `state/`, `connectors/`, `core/`, `main.py`, `db.py`, `morning_briefing.py`.  
**Standard**: Golden Standard Architecture & Non-Negotiable Zero-Hardcoding Rule. Meaning decided strictly by LLM structured outputs, embeddings/hybrid retrieval, or tool schema metadata.

---

## 1. Audit Findings Inventory (Pre-Fix vs. Replacement)

| Finding ID | File:Line | Category | What It Decided | Severity | Chosen Replacement |
|---|---|---|---|---|---|
| **HB-01** | `agent/tools.py:2497` | Natural Language Word List & Regex | `for d_kw in ("daily", "roj", "roz", "har din", ...):` decided whether a reminder was recurring/daily. | **HIGH** | **Model Structured Output**: Removed keyword loop. Model emits `recurrence="daily"` directly in `CreateReminderArgs` schema based on native multilingual understanding. |
| **HB-02** | `agent/tools.py:3199` | Natural Language Word List & Regex | `for d_kw in ("daily", "roj", ...):` decided calendar recurrence in `create_calendar_event`. | **HIGH** | **Model Structured Output**: Removed keyword loop. Model emits `recurrence` in `CreateCalendarEventArgs` directly. |
| **HB-03** | `agent/tools.py:2045` | Mapping Dictionary | `{"en": "english", "eng": "english", "hi": "hindi", "hing": "hinglish"}` hardcoded language tag mappings in `set_user_preference`. | **MEDIUM** | **Free-form Preference Ingestion**: Removed dictionary. Takes language exactly as model states it (BCP-47 or natural name) and injects directly into prompt. |
| **HB-04** | `db.py:185` | Mapping Dictionary | `if raw_lang in ("en", "eng"): lang = "english"` in `get_user_preference`. | **MEDIUM** | **Direct Readout**: Returns user's stored language preference directly without normalization dictionary. |
| **HB-05** | `agent/loop.py:283` | Keyword List | `clean_low in ("cancel", "no", "nahi", "nahin", "mat karo", "rehne do", ...)` decided whether to cancel pending action. | **HIGH** | **Structural Intent Inspection**: Checks `not bool(extra and extra.get("replacement_instruction"))`. If no replacement, cancels cleanly. In Part E, fully replaced by `resolve_pending_action` tool and inline buttons. |
| **HB-06** | `agent/loop.py:479` | Natural Language Word Lists | `_sentiment_words = {...}` and `_has_task_words = any(w in ("morning", "kal", "aaj", ...))` decided whether message was small talk. | **HIGH** | **Semantic Routing**: Deleted word lists. `route_tools` delegates directly to semantic embedding group routing. |
| **HB-07** | `agent/loop.py:1206` | Hardcoded City List & Travel Indicators | `for c_cand in ("udaipur", "jaipur", "delhi", "mumbai", "bangalore", ...)` parsed travel city. | **HIGH** | **Structured Memory Extraction**: Deleted hardcoded tuple. City/location changes are parsed as structured memory entities during document/ticket ingestion. |
| **HB-08** | `morning_briefing.py:57` | Hardcoded City List | `for known in ("jodhpur", "udaipur", "jaipur", ...)` scanned city for morning weather. | **HIGH** | **Structured Context Lookup**: Replaced with database lookup for active location state (`memories.structured['city']` or active travel ticket origin). Zero hardcoded cities. |
| **HB-09** | `db.py:2673` | Hardcoded City Literal | `if "jodhpur" in txt: set_user_location_state(..., "Jodhpur")` hardcoded city fallback. | **CRITICAL** | **Structured Fallback**: Replaced with query for structured `location` or `profile` memory items. |
| **HB-10** | `agent/verifier.py:355` | Keyword Word Lists | `sentiment_words = {...}` and `has_task_words = any(...)` bypassed fact verification. | **HIGH** | **Zero-Keyword Fast-Path**: Fast path checks only structural shape (`draft_reply.strip().endswith("?")` or `len(words) <= 3`). All substantive claims audited by LLM Claim Checker. |
| **HB-11** | `agent/verifier.py:285` | Route / Travel Claim Detection | Verification of navigation and travel time claims. | **HIGH** | **Structured Schema Flag**: Handled structurally via LLM claim checker output flag `claims_route_or_travel_time: bool`, eliminating string parsing. |

---

## 2. Before vs. After Code Verifications

### A. Recurrence Resolution (Hinglish/Hindi/English)
- **Before**: Fragile python loop checking 8 romanized Hindi variations (`roj`, `roz`, `har din`, `har subah`, `har shaam`). Failed on typos (`rozz`, `dayly`, `partidin`), Devanagari (रोज़, हर दिन), and regional phrasing.
- **After**: Model tool schema specifies `recurrence: enum['daily', 'weekly', 'weekdays', 'monthly', 'none']`. The LLM natively maps English, Devanagari, Hinglish, and typos to `"daily"`. Zero regex in tools.py.

### B. User Language Preference
- **Before**: `en`/`eng` -> `english`, `hi`/`hin` -> `hindi`. Storing unsupported languages (e.g. `fr`, `es`, `marathi`, `tamil`) was blocked or defaulted to English.
- **After**: `set_user_preference(reply_language="french", tone="formal")` stores `"french"` as given. Injected as `LOCKED REPLY LANGUAGE: FRENCH` in prompt.

### C. Location & City Determination
- **Before**: Static list of 10 Indian cities. Users living in Ahmedabad, Kolkata, Chennai, Hyderabad, Chandigarh, or New York received empty or wrong city weather.
- **After**: Structured location state machine reads `structured.city` from user memories or active travel tickets. Fully general across all global cities.

---

## 3. Automated Regression & Hardcoding Test Suite

Created `tests/test_no_hardcoding.py`:
1. Asserts no natural language word lists or tuples used in membership checks (`in ('cancel', 'no', 'nahi')`).
2. Asserts no hardcoded person/city/brand names in runtime logic (`'Jodhpur'`, `'Ananya'`, `'Devender'`).
3. Asserts no keyword-to-tool dictionaries in `agent/tools.py`.
4. Asserts no hardcoded language normalization dictionaries in `set_user_preference`.
5. Verifies that `scripts/audit_regex_meaning.py` violations in modified core paths are 0.
