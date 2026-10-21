# PeculiarAI — Hyper-Intelligent Personal AI Assistant

> **Production-Grade Personal AI Assistant on Telegram & Web Portal**  
> Powered by FastAPI, Groq LLM (Llama 3.3 70B Versatile), PostgreSQL (Neon), Google Workspace APIs (Calendar, Meet, Drive, Gmail), Notion, GitHub, and Multimodal Vision.

---

## 📑 Table of Contents
1. [Project Overview](#project-overview)
2. [High-Level Architecture](#high-level-architecture)
3. [Exhaustive File-by-File Documentation (0% to 100%)](#exhaustive-file-by-file-documentation-0-to-100)
   - [Core Application & Orchestration](#1-core-application--orchestration)
   - [Database & Persistent Storage](#2-database--persistent-storage)
   - [Intelligence, NLU & LLM Engine](#3-intelligence-nlu--llm-engine)
   - [Google Workspace & OAuth Subsystem](#4-google-workspace--oauth-subsystem)
   - [Third-Party Integrations (Notion, GitHub)](#5-third-party-integrations-notion-github)
   - [Multimodal Vision & Voice Processing](#6-multimodal-vision--voice-processing)
   - [Document & Export Generators](#7-document--export-generators)
   - [REST APIs & Web Portal](#8-rest-apis--web-portal)
   - [Security & Cryptography](#9-security--cryptography)
   - [Support Utilities & Configuration](#10-support-utilities--configuration)
   - [Automated Test Suite (104 Tests)](#11-automated-test-suite-104-tests)
4. [Database Schema & Data Models](#database-schema--data-models)
5. [Key Feature Workflows](#key-feature-workflows)
6. [Setup, Testing & Deployment](#setup-testing--deployment)

---

## 🌟 Project Overview

**PeculiarAI** is an autonomous, context-aware AI personal assistant designed to run 24/7 on Telegram and a web dashboard. It acts as an executive chief of staff that:
- **Tracks Life & Schedule:** Aggregates Google Calendar, personal reminders, task board cards, and contact commitments into a unified agenda with intelligent conflict detection.
- **Remembers Networking & Contacts (Memorae-Grade):** Scans business card images, remembers people, roles, companies, and explicit deliverables/commitments with typo-tolerant fuzzy matching.
- **Executes Real Actions:** Schedules Google Calendar events with instant Google Meet links, sends emails, uploads files to Google Drive, appends notes to Notion, and queries GitHub repositories.
- **Processes Multimodal Inputs:** Transcribes Telegram voice notes via Whisper, extracts details from bills/receipts/documents/photos via vision models, and answers queries about uploaded PDFs.
- **Zero-Hallucination Guardrails:** Strictly forbids claiming actions or integrations were executed without actual API confirmation.

---

## 🏗️ High-Level Architecture

```mermaid
graph TD
    UserTelegram["Telegram User (Mobile / Desktop)"] -->|Webhook POST /webhook| FastAPI["FastAPI Engine (main.py)"]
    UserWeb["Web Portal User (connect.html / Dashboard)"] -->|REST API /api/v1| FastAPI
    
    FastAPI --> NLU["NLU & Parsing Engine (llm.py, nlu.py, timing.py)"]
    NLU --> Groq["Groq API (Llama 3.3 70B & Vision)"]
    
    FastAPI --> DB[("PostgreSQL Database (Neon / Supabase)")]
    FastAPI --> GoogleAuth["Google OAuth & Services (google_auth.py, google_services.py)"]
    GoogleAuth --> GoogleAPI["Google APIs (Calendar, Meet, Drive, Gmail)"]
    
    FastAPI --> ThirdParty["Integrations Engine (integrations.py)"]
    ThirdParty --> NotionAPI["Notion API"]
    ThirdParty --> GitHubAPI["GitHub REST API"]
    
    FastAPI --> Multimodal["Multimodal Engine (multimodal.py, voice.py)"]
    FastAPI --> Generators["File Generators (file_generator.py - PDF/Excel)"]
```

---

## 📂 Exhaustive File-by-File Documentation (0% to 100%)

### 1. Core Application & Orchestration

#### [`main.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py) (The Master Orchestrator)
- **Purpose:** Entry point for FastAPI, Telegram webhook routing, conversation state machine, and unified cognitive execution.
- **Key Modules & Responsibilities:**
  - `telegram_webhook()`: Receives Telegram updates (messages, photos, voice, callback queries) and dispatches them asynchronously.
  - `handle_user_text()`: Top-level dispatcher handling pending states, confirmation gates, slash commands, and active commands.
  - `handle_active()`: High-level intent router (Notion, GitHub, Meet links, Smart PDF/Excel, contact commitments, and general queries).
  - `handle_conversational_chat()`: Backing conversational intelligence using Groq Llama 3.3 70B with persistent memory, episodic chat history retrieval, and strict zero-hallucination rules.
  - `get_unified_schedule()`: Aggregates Google Calendar, saved reminders, contact commitments, and task cards for any target date.
  - `show_range()`: Formats agendas, converts UTC timestamps to Indian Standard Time (IST), and alerts on schedule overlaps (e.g. 5:00 PM movie vs 6:00 PM meeting).
  - `find_matching_contact()`: Typo-tolerant contact search using `difflib.SequenceMatcher` (e.g. `annaya` / `ananay` -> `Ananya Roy`).
  - `prepare_google_action()` & `handle_google_confirmation()`: Two-phase commit gate for Google Workspace actions (Create/Update/Delete meetings, Send emails, Upload files).

---

### 2. Database & Persistent Storage

#### [`db.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/db.py) (Relational Data Access Layer)
- **Purpose:** Centralized SQLAlchemy data access layer targeting PostgreSQL (Neon) with connection pooling and SSL support.
- **Key Functions:**
  - **User Lifecycle:** `ensure_user()`, `give_consent()`, `has_consent()`, `set_persona()`, `get_persona()`, `set_categories()`, `get_categories()`.
  - **Memory Items & Reminders:** `save_memory()`, `list_memories()`, `items_between()`, `items_for_date()`, `delete_memory_items()`, `add_reminders()`, `get_pending_reminders()`, `mark_sent()`.
  - **Contact & Networking Memory:** `save_contact_memory()`, `get_contact_memories()`, `update_contact_commitment()`.
  - **Episodic Chat Logging & Search:** `log_chat_message()`, `get_recent_chat_history()`, `get_chat_turns_mentioning_keyword()`.
  - **User Facts & Behavioral Rules:** `save_user_fact()`, `get_user_facts()`, `get_user_rules()`, `add_user_rule()`, `delete_user_rule()`.
  - **Tokens & Integrations:** `save_google_token()`, `get_google_token()`, `delete_google_token()`, `save_user_integration()`, `get_user_integration()`, `delete_user_integration()`.
  - **Kanban Board & Smart Lists:** `get_board_cards()`, `create_smart_list()`, `add_smart_list_item()`.

#### [`create_tables.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/create_tables.py) (Database Migration Script)
- **Purpose:** Idempotent DDL script creating all PostgreSQL tables, constraints, foreign keys, and indexes.

---

### 3. Intelligence, NLU & LLM Engine

#### [`llm.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/llm.py) (Natural Language Understanding)
- **Purpose:** Intent classification, date/time extraction, temporal anchoring, and Groq API communication.
- **Key Components:**
  - `understand()`: Classifies user messages into structured JSON intents (`save_item`, `show_agenda`, `create_meeting`, `send_email`, `delete_meeting`, etc.).
  - `parse_followup_time()`: Parses absolute and relative times, 12-hour/24-hour clocks, Hindi/Hinglish terms (`baje`, `shaam`, `somwar`, `kal`, `parso`), full weekdays (Monday-Sunday), and common typos (`tuedday`, `tueday`, `mnday`).
  - `_chat_json()`: Resilient JSON-mode Groq caller with retry logic and schema enforcement.
  - `answer_conversation_memory()`: Answers conversational memory recall questions based on recent history.

#### [`nlu.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/nlu.py) (Heuristic Fast-Path NLU)
- **Purpose:** Instant local regex parsing for yes/no confirmations, persona switching, and sensitive data detection without LLM latency.

#### [`timing.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/timing.py) (Temporal Calculations & IST Timezone)
- **Purpose:** Standardizes India Standard Time (`Asia/Kolkata` / UTC+05:30), computes reminder notification intervals, and builds schedule plans.

#### [`lang.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/lang.py) (Multilingual & Hinglish Engine)
- **Purpose:** Detects user language (English, Hindi, Hinglish), manages language preferences per user, and formats localized strings.

#### [`categories.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/categories.py) (Taxonomy & Categories)
- **Purpose:** Defines default reminder categories (`appointments`, `bills`, `medication`, `groceries`, `work`, `travel`, `social`).

---

### 4. Google Workspace & OAuth Subsystem

#### [`google_auth.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/google_auth.py) (OAuth2 Authorization Server)
- **Purpose:** Handles Google OAuth2 3-legged authorization flow.
- **Key Functions:**
  - `create_authorization_url()`: Generates secure Google consent URL with CSRF state tokens.
  - `exchange_code()`: Exchanges authorization codes for access and refresh tokens.
  - `refresh_token()`: Automatically refreshes expired Google access tokens.
  - `revoke_token()`: Disconnects Google access on demand.

#### [`google_services.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/google_services.py) (Google Workspace API Client)
- **Purpose:** Async HTTP client calling official Google REST APIs on behalf of connected users.
- **Capabilities:**
  - **Google Calendar & Meet:** `create_meeting()` (creates event with automatic `hangoutsMeet` conference data), `list_calendar_events()`, `update_meeting()`, `delete_calendar_event()`.
  - **Google Drive:** `upload_text_file()` (uploads text/notes to Google Drive folders).
  - **Gmail:** `send_email()` (sends RFC 2822 base64-encoded emails).

---

### 5. Third-Party Integrations (Notion, GitHub)

#### [`integrations.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/integrations.py) (Integrations Client)
- **Purpose:** Direct API integrations for Notion and GitHub.
- **Capabilities:**
  - **Notion:** `validate_notion_token()`, `create_notion_page()` (appends meeting notes, tasks, or summaries to user workspaces).
  - **GitHub:** `validate_github_token()`, `list_github_repos()`, `list_github_issues()`. Includes strict anti-hallucination validation against live GitHub accounts.

---

### 6. Multimodal Vision & Voice Processing

#### [`multimodal.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/multimodal.py) (Visual Document Intelligence)
- **Purpose:** Analyzes images (business cards, bills, receipts, whiteboard notes) and PDF documents sent via Telegram.
- **Key Functions:**
  - `process_telegram_photo()`: Uses Groq Vision models to extract structured contact details from business card photos.
  - `process_telegram_document()`: Extracts text and tabular data from uploaded documents.
  - `summarize_document_content()`: Provides concise executive summaries of long PDFs and text files.

#### [`voice.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/voice.py) (Voice Note Transcription)
- **Purpose:** Downloads Telegram voice notes (.ogg / Opus) and transcribes them into English/Hinglish text using Whisper via Groq.

#### [`inbox.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/inbox.py) (Email Bill & Booking Ingestion)
- **Purpose:** Checks dedicated bot inbox via IMAP for forwarded bills, receipts, or flight bookings, automatically extracting reminders and due dates.

---

### 7. Document & Export Generators

#### [`file_generator.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/file_generator.py) (PDF & Excel Synthesis)
- **Purpose:** Generates high-quality downloadable documents for users on Telegram.
- **Key Functions:**
  - `build_contacts_excel()`: Generates professional multi-column `.xlsx` spreadsheets containing saved contacts, companies, emails, phone numbers, and commitments.
  - `build_styled_pdf()`: Synthesizes formatted PDF documents with headers, styling, and structured tables.

---

### 8. REST APIs & Web Portal

#### [`api_v1.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/api_v1.py) (FastAPI REST API v1)
- **Purpose:** Secure API backend powering web dashboards, external client integrations, and mobile clients.
- **Endpoints:**
  - `/api/v1/auth/*`: Session authentication, token refresh, and OTP flows.
  - `/api/v1/memories/*`: CRUD operations on reminders, items, and categories.
  - `/api/v1/contacts/*`: Contact cards management and commitments tracking.
  - `/api/v1/integrations/*`: Connect and disconnect Notion, GitHub, and Google.
  - `/api/v1/kanban/*`: Interactive task board cards management.

#### [`web_app.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/web_app.py) (Dashboard Controller)
- **Purpose:** Serving static web views, user verification portals, and OAuth redirect pages.

#### Web Interface Files:
- [`connect.html`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/connect.html): The unified web connection portal allowing users to connect Google, Notion, and GitHub with one click.
- [`index.html`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/index.html): Landing page and overview of PeculiarAI features.
- [`login.html`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/login.html): Authentication and login screen.
- [`privacy.html`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/privacy.html): Comprehensive privacy policy document.
- [`terms.html`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/terms.html): Terms of service document.

---

### 9. Security & Cryptography

#### [`crypto.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/crypto.py) (Token Encryption at Rest)
- **Purpose:** AES-256 GCM authenticated encryption protecting OAuth tokens (Google, Notion, GitHub) at rest in the database.
- **Key Functions:** `encrypt_token()`, `decrypt_token()`, key rotation, and integrity verification.

---

### 10. Support Utilities & Configuration

- [`.env`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/.env): Production and local environment variables (Telegram Bot Token, Groq API Key, Database URL, Google OAuth secrets).
- [`requirements.txt`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/requirements.txt): Python dependencies (`fastapi`, `uvicorn`, `groq`, `httpx`, `sqlalchemy`, `psycopg2-binary`, `reportlab`, `openpyxl`, `cryptography`, `pytest`).
- [`render.yaml`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/render.yaml): Infrastructure-as-code deployment configuration for Render cloud platform.
- [`set_webhook.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/set_webhook.py): Utility script configuring Telegram Bot API webhook to point to the live server.
- [`server.js`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/server.js) & [`package.json`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/package.json): Node.js wrapper/proxy script.

---

### 11. Automated Test Suite (104 Tests)

All automated tests are housed in the [`tests/`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests) directory and run via `pytest`.

| Test File | Tests | Focus Areas |
| :--- | :---: | :--- |
| [`test_contact_and_schedule_intelligence.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_contact_and_schedule_intelligence.py) | 5 | IST clock conversion, activity schedule clash detection, fuzzy contact matching (`annaya` -> Ananya), deliverable queries. |
| [`test_meeting_intelligence.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_meeting_intelligence.py) | 4 | Weekday parsing, in-flight rescheduling during confirmation, direct meet link delivery, default meeting title. |
| [`test_unified_memory_and_schedule.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_unified_memory_and_schedule.py) | 4 | Unified schedule aggregation across Google Calendar, reminders, tasks, and contact commitments. |
| [`test_master_repairs.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_master_repairs.py) | 15 | Regression prevention for state machines, prompt injection defenses, and time parsing. |
| [`test_adversarial_and_security.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_adversarial_and_security.py) | 25 | Prompt injection attacks, SQL injection attempts, XSS payloads, sensitive data isolation. |
| [`test_crypto.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_crypto.py) | 10 | AES-256 GCM token encryption/decryption, tamper detection, and key rotation. |
| [`test_api_v1.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_api_v1.py) | 14 | REST API v1 endpoints, JWT authentication, and CRUD operations. |
| [`test_api_endpoints.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_api_endpoints.py) | 6 | Webhook payload validation, health checks, and rate limiting. |
| [`test_google_services.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_google_services.py) | 4 | Google Calendar event formatting, token refresh, and Drive uploads. |
| [`test_integrations.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_integrations.py) | 3 | Notion page creation and GitHub repository anti-hallucination verification. |
| [`test_multimodal_documents.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_multimodal_documents.py) | 4 | Business card OCR, document summarization, and PDF Q&A. |
| [`test_nlu_and_parsing.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_nlu_and_parsing.py) | 6 | Multilingual yes/no classification, relative time offsets, and category mapping. |
| [`test_oauth_security.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_oauth_security.py) | 4 | CSRF state protection, token exchange security, and revocation. |

---

## 🗄️ Database Schema & Data Models

PeculiarAI utilizes a normalized relational schema on PostgreSQL:

```mermaid
erDiagram
    USERS ||--o{ REMINDERS : owns
    USERS ||--o{ MEMORY_ITEMS : owns
    USERS ||--o{ CONTACT_MEMORIES : saves
    USERS ||--o{ USER_FACTS : has
    USERS ||--o{ USER_RULES : defines
    USERS ||--o{ CHAT_LOGS : records
    USERS ||--o{ SMART_LISTS : creates
    SMART_LISTS ||--o{ SMART_LIST_ITEMS : contains
    USERS ||--o{ BOARD_CARDS : manages
    USERS ||--o{ GOOGLE_TOKENS : connects
    USERS ||--o{ USER_INTEGRATIONS : connects
    USERS ||--o{ EMAIL_LINKS : verifies

    USERS {
        int id PK
        bigint telegram_chat_id UK
        string persona
        string categories
        timestamp consented_at
        timestamp created_at
    }

    CONTACT_MEMORIES {
        int id PK
        int user_id FK
        string name
        string company
        string role_title
        string email
        string phone
        string met_at
        text commitments
        text context_notes
        timestamp created_at
    }

    CHAT_LOGS {
        int id PK
        int user_id FK
        string role
        text message
        timestamp created_at
    }

    MEMORY_ITEMS {
        int id PK
        int user_id FK
        string category
        string title
        decimal amount
        date due_date
        time due_time
        timestamp created_at
    }

    GOOGLE_TOKENS {
        int id PK
        int user_id FK
        text access_token_enc
        text refresh_token_enc
        timestamp expires_at
        timestamp created_at
    }
```

---

## ⚡ Key Feature Workflows

### 1. Smart Meeting Scheduling & Rescheduling
1. **User Request:** User says *"set a meeting for me tuesday 5pm"*.
2. **Parsing:** `parse_followup_time()` identifies the upcoming Tuesday, sets time to 17:00 IST, and sets default title to `"Meeting"`.
3. **Conflict Detection:** Scans Google Calendar and contact commitments for that slot.
4. **Confirmation Gate:** Asks user to confirm. If user changes their mind during confirmation (*"monday 5 bje"*), the bot dynamically updates the slot without dropping out of the flow.
5. **Execution:** On confirmation (`yes` / `haan krdo`), creates Google Calendar event with a Google Meet link and returns the meeting details immediately.
6. **Direct Link Query:** If user later asks *"meeting ka link ya de"*, bot fetches and returns the `meet.google.com` link right inside chat.

### 2. Memorae-Grade Contact & Deliverables Memory
1. **Ingestion:** User sends a business card photo or states *"Ananya ko module bhejna hai Tuesday ko"*.
2. **Extraction & Storage:** The contact details and commitments are saved in `contact_memories`.
3. **Fuzzy Retrieval:** When user asks *"bhy annaya ko mujhe ky bhjna th"*, fuzzy matching matches `annaya` to `Ananya Roy`.
4. **Direct Response:** The bot responds immediately: *"Bhai, Ananya Roy (Elevate Ventures) ko aapko: module bhejna hai Tuesday ko!"* without drafting irrelevant messages.

### 3. Unified Agenda & Schedule Alerts
1. **Query:** User asks *"kal meri movie hai shaam 5 baje, kya main free hu?"*.
2. **Interval Calculation:** Bot inspects Google Calendar events for the day (e.g. 6:00 PM Meeting).
3. **Alert:** Alerts the user: *"⚠️ Schedule Warning: Aapne 5:00 PM ka plan poocha hai, lekin 6:00 PM par aapki 'Meeting' scheduled hai, isliye schedule tight/overlap ho sakta hai!"*.

---

## 🛠️ Setup, Testing & Deployment

### 1. Local Environment Setup
```bash
# Clone the repository
git clone https://github.com/nontech-cpu/AI-AGent.git
cd AI-AGent

# Create and activate virtual environment
python -m venv venv
venv\Scripts\activate   # On Windows
# source venv/bin/activate # On Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

### 2. Configuration (`.env`)
Create a `.env` file with your credentials:
```ini
TELEGRAM_BOT_TOKEN="your-telegram-bot-token"
GROQ_API_KEY="your-groq-api-key"
DATABASE_URL="postgresql://user:password@neon-db-host/dbname?sslmode=require"
ENCRYPTION_KEY="your-32-byte-hex-encryption-key"
GOOGLE_CLIENT_ID="your-google-client-id"
GOOGLE_CLIENT_SECRET="your-google-client-secret"
GOOGLE_REDIRECT_URI="https://your-domain.com/auth/google/callback"
```

### 3. Run Automated Tests
```bash
pytest tests/
```
*Current test suite status: **104 passed, 0 failures** (100% green).*

### 4. Run Development Server
```bash
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 5. Production Deployment (Render)
The repository includes a `render.yaml` configuration. Every commit to branch `main` triggers an automatic container build and deployment on Render with health checks at `/health`.

---

## 📄 License & Attribution
Developed with ❤️ by Nirav Mishra & Antigravity (Google DeepMind Advanced Agentic Coding).  
Licensed under the MIT License.
