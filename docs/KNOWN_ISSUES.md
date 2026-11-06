# Known Issues & Technical Debt Tracker

This document tracks identified architectural limitations, temporary workarounds, and slated refactoring phases for PeculiarAI.

---

## 1. Tool Routing Mechanism (Slated for Phase 4 Replacement)

- **Location**: [`agent/tools.py:3676-3720`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/tools.py#L3676-L3720) (`route_tools`)
- **Current Behavior**:
  `route_tools` currently relies on static keyword matching (e.g. searching for `"notion"`, `"mail"`, `"weather"`, `"github"` in the prompt and recent turn context) to filter domain-specific tools down to ~15 tools per turn, preventing token budget overflow.
- **Limitation**:
  Keyword routing cannot capture nuanced semantic intent (e.g. "I want to share my thoughts on paper" -> Notion, or "check atmospheric conditions" -> Weather) unless explicit target keywords are used.
- **Phase 4 Plan**:
  Replace keyword-based tool catalog pruning with semantic embeddings-based vector routing or a lightweight classifier model that dynamically projects relevant tool schemas into context based on meaning rather than string matching.

---

## 2. Model Candidate Ordering & Selection (Gemini vs. Groq)

- **Location**: [`agent/loop.py:500-515`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L500-L515)
- **Current Configuration**:
  1. `gemini-3.1-flash-lite-preview` (Tier 1 Primary)
  2. `gemini-3-flash-preview` (Tier 1 Secondary)
  3. `openai/gpt-oss-120b` (Tier 2 Groq Fallback)
- **Empirical Justification (Real API 10-Prompt Benchmark)**:
  - `gemini-3.1-flash-lite-preview`: **10 / 10 Success (100%)**, Avg Latency: 1598ms. Accurate tool calling across reminders, contacts, email, places, and math code interpreter.
  - `gemini-3-flash-preview`: **0 / 10 Success (100% Failure with HTTP 429 Quota Exceeded)** under the current Google Generative Language API key quota tier.
  - `openai/gpt-oss-120b` (Groq): **5 / 10 Success (50% Failure)** due to organization TPM rate limits and missed tool calls on Hinglish schedule queries.
- **Decision**:
  `gemini-3.1-flash-lite-preview` is positioned first to prevent immediate 429 failover cascading into Groq rate limits.

---

## 3. Pre-LLM Hardcoded Shortcuts (Slated for Phase 6 Removal)

The following shortcuts intercept user requests and generate responses *before* the LLM agent loop is invoked. These will be systematically retired or unified into autonomous tool calling in Phase 6:

1. **Notion Token Direct Pasting Fast-Path**:
   - **File:Line**: [`agent/loop.py:233-247`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L233-L247)
   - **Mechanism**: Detects string prefixes `secret_` or `ntn_` and directly validates and stores Notion workspace tokens without model interaction.
2. **GitHub Token Direct Pasting Fast-Path**:
   - **File:Line**: [`agent/loop.py:249-268`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L249-L268)
   - **Mechanism**: Detects string prefixes `ghp_` or `github_pat_` and directly validates and stores GitHub integration tokens.
3. **Bare Email Google OAuth Fast-Path**:
   - **File:Line**: [`agent/loop.py:270-285`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L270-L285)
   - **Mechanism**: Regex `re.fullmatch(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", trimmed_text)` that immediately returns the Google OAuth linking URL if the user pastes an email address when Google is disconnected.
4. **Deterministic Pending Confirmation Intercept**:
   - **File:Line**: [`agent/loop.py:288-340`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L288-L340)
   - **Mechanism**: If `active_pending` exists in state and user replies with affirmative/negative phrases (`yes`, `no`, `cancel`, `proceed`), bypasses LLM to execute or abort the pending action directly.
5. **Telegram Slash Commands Fast-Path**:
   - **File:Line**: [`main.py:120-210`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L120-L210)
   - **Mechanism**: Commands like `/start`, `/reset`, `/connect_google`, `/wipe_my_data` are handled via direct string routing prior to invoking `run_agent_turn()`.

---

## 4. Remaining Keyword & Meaning Heuristics (Audited file:line Tracker)

The following heuristics determine semantic meaning or intent via keywords and regexes outside the pre-LLM shortcuts above:

1. **Pending Action Confirmation/Cancellation Phrase Sets**:
   - **File:Line**: [`state/pending.py:211-219`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/state/pending.py#L211-L219)
   - **Details**: `STRUCTURAL_CONFIRM_COMMANDS` and `STRUCTURAL_CANCEL_COMMANDS` include conversational phrases (`"haan"`, `"kar do"`, `"kardo"`, `"bilkul"`, `"create it"`, `"haanji"`, `"sure"`, `"proceed"`, `"send"`, `"send it"`, `"bhej do"`, `"mat karo"`, `"rehne do"`, `"nahi"`, `"galat"`, `"stop"`, `"abort"`, `"drop it"`).

2. **Pending Action Interrogative & Entity Keywords Interceptor**:
   - **File:Line**: [`state/pending.py:243`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/state/pending.py#L243)
   - **Details**: String tuple check `any(q in low for q in ("schedule", "free time", "meeting", "ananya", "batao", "kya", "before", "pehle"))` intercepting questions before LLM classification, including a hardcoded contact name (`"ananya"`).

3. **Domain Pruning Keyword Dictionaries**:
   - **File:Line**: [`agent/tools.py:3745-3765`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/tools.py#L3745-L3765)
   - **Details**: `route_tools` maps query tokens like `"sona"`, `"chandi"`, `"bhav"`, `"daam"`, `"gold"`, `"silver"`, `"rate"`, `"price"`, `"weather"`, `"mausam"` to the `"utilities_finance_weather"` tool category.

4. **Error Fallback Greeting Interceptor**:
   - **File:Line**: [`main.py:2442`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L2442)
   - **Details**: `clean_l in ("hi", "hello", "hey", "hlo", "hii", "namaste")` in the agent error handler to select a friendly conversational fallback when an initial turn errors out.

5. **Travel/Booking Entity Regex Extractors**:
   - **File:Line**: [`state/active_context.py:288-343`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/state/active_context.py#L288-L343)
   - **Details**: Regexes matching PNRs, routes, seat assignments, and carrier names (`re.search(r"\b(pnr|ticket|booking|train|flight|journey)\b", ...)` and `re.search(r"pnr\s*[:\-#]?\s*([a-zA-Z0-9]{6,12})", ...)`).

6. **Temporal Date Expression Regex**:
   - **File:Line**: [`memory/retrieval.py:120`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/memory/retrieval.py#L120)
   - **Details**: Regex `re.search(r'\b(\d{1,2}(?:st|nd|rd|th)?\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*)\b', user_text, re.I)` for pinning temporal memory lookups to explicit dates.


