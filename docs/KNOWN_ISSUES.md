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
