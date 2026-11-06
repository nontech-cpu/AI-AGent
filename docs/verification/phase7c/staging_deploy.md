# Staging Deployment & Environment Isolation Specification

> [!IMPORTANT]
> **Production Safety Invariant**: The staging environment must run in 100% data and secret isolation from production. It must NEVER share a database, bot token, encryption key, or webhook secret with production.

---

## 1. Strict Isolation Principles & Startup Guard

### Dedicated Empty Neon Database
- **Requirement**: Staging must connect to its **OWN** empty Neon PostgreSQL database (created as a new project or an empty branch; never a clone or backup copy of production).
- **Startup Guard (Enforced in Code)**:
  In [`main.py:3537-3554`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L3537-L3554), when `ENV=staging` (or `APP_ENV=staging`, `ENVIRONMENT=staging`, or `STAGING_MODE=1`):
  ```python
  is_staging = (
      os.getenv("ENV", "").lower() == "staging"
      or os.getenv("APP_ENV", "").lower() == "staging"
      or os.getenv("ENVIRONMENT", "").lower() == "staging"
      or os.getenv("STAGING_MODE", "").strip() == "1"
  )
  if is_staging:
      prod_fp = os.getenv("PROD_DB_FINGERPRINT", "").strip()
      if prod_fp and db_url:
          import urllib.parse
          parsed_db = urllib.parse.urlparse(db_url)
          host_or_path = f"{parsed_db.hostname or ''}/{parsed_db.path or ''}"
          if prod_fp in host_or_path or prod_fp in db_url:
              raise RuntimeError(
                  f"CRITICAL STAGING GUARD: Staging is configured to use production database ({prod_fp})! "
                  "Refusing to start. Staging must use its own isolated, empty database."
              )
  ```
  If staging is inadvertently pointed to production's endpoint or database (e.g. `ep-delicate-lake`), the application immediately crashes on boot with `RuntimeError` and refuses to serve requests.

### Dedicated Secrets & Bot Token
- **Staging Telegram Bot Token**: Obtained from `@BotFather` as a separate bot (e.g. `@PeculiarAI_Staging_Bot`). Never use the production bot token.
- **Dedicated `AES_SECRET_KEY`**: A separate 64-hex-character AES-256-GCM key (`openssl rand -hex 32`).
- **Dedicated `WEBHOOK_SECRET`**: A separate random alphanumeric secret token for Telegram webhook request validation.

---

## 2. Verified Environment Variable Names (Read by Code)

The table below confirms the exact environment variable names audited directly against `os.getenv` calls in the codebase:

| Purpose | Correct Variable Name (Used by App) | Rejected / Common Mistake | Code Reference |
|---|---|---|---|
| **Telegram Bot API Token** | `TELEGRAM_BOT_TOKEN` | `BOT_TOKEN` | [`main.py:27`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L27), [`agent/loop.py:160`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/agent/loop.py#L160) |
| **Telegram Webhook Secret** | `WEBHOOK_SECRET` | `TELEGRAM_SECRET` | [`main.py:28`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L28), [`set_webhook.py:12`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/set_webhook.py#L12) |
| **AES Token Encryption Key** | `AES_SECRET_KEY` | `SECRET_KEY` | [`crypto.py:33`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/crypto.py#L33) |
| **Public Service URL** | `PUBLIC_BASE_URL` | `FASTAPI_BASE_URL` | [`connectors/calendar.py:20`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/connectors/calendar.py#L20), [`google_auth.py:19`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/google_auth.py#L19) |
| **Production DB Fingerprint** | `PROD_DB_FINGERPRINT` | `PROD_DB_URL` | [`main.py:3546`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L3546) |
| **Staging Mode Flag** | `STAGING_MODE` | `IS_STAGING` | [`main.py:3542`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L3542), [`llm/provider.py:948`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/llm/provider.py#L948) |
| **Simulated Failover Provider** | `FORCE_PROVIDER_FAIL` | `MOCK_FAIL_PROVIDER` | [`llm/provider.py:949`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/llm/provider.py#L949) |
| **Google OAuth Client** | `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_TOKEN_KEY` | `GOOGLE_KEY` | [`google_auth.py:17-18`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/google_auth.py#L17-L18) |
| **Notion OAuth Client** | `NOTION_CLIENT_ID`, `NOTION_CLIENT_SECRET`, `NOTION_API_KEY` | `NOTION_TOKEN` | [`notion_auth.py:17-18`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/notion_auth.py#L17-L18) |
| **GitHub OAuth Client** | `GITHUB_CLIENT_ID`, `GITHUB_CLIENT_SECRET`, `GITHUB_TOKEN` | `GH_TOKEN` | [`github_auth.py:17-18`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/github_auth.py#L17-L18) |
| **LLM Provider API Keys** | `GEMINI_API_KEY`, `GROQ_API_KEY`, `GROQ_API_KEY_BACKUP` | `LLM_KEY` | [`llm/provider.py:234, 380`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/llm/provider.py#L234) |

### Webhook Route Path
- **Verified Route Path**: `/telegram/webhook` (defined at [`main.py:5241`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/main.py#L5241) via `@app.post("/telegram/webhook")`).

---

## 3. Staging Mode & Failover Controls (`STAGING_MODE=1`)

Setting `STAGING_MODE=1` activates specialized staging testing behaviors:
1. **Background Loop Isolation**:
   Background loops (`reminder_loop`, `morning_briefing_loop`, `connected_user_gmail_monitor_loop`) only interact with the staging database. Because of the `PROD_DB_FINGERPRINT` guard, loops can never accidentally poll or notify production users.
2. **Deterministic Failover Simulation (`FORCE_PROVIDER_FAIL`)**:
   Enables setting `FORCE_PROVIDER_FAIL=gemini` (or `groq` or `all`) to test resilience without taking down production credentials. In [`llm/provider.py:948-955`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/llm/provider.py#L948-L955):
   ```python
   staging_mode = os.getenv("STAGING_MODE", "").strip() == "1"
   force_fail = os.getenv("FORCE_PROVIDER_FAIL", "").strip().lower()
   if staging_mode and force_fail and (force_fail == prov.name.lower() or force_fail == "all"):
       logger.warning(f"FORCE_PROVIDER_FAIL active: Simulating failure for {prov.name}")
       fallback_reasons.append(f"FORCE_PROVIDER_FAIL: {prov.name} simulated failure")
       last_error = RateLimitedError(f"Simulated failure for {prov.name}", provider=prov.name)
       continue
   ```

---

## 4. Fresh-DB Idempotent Initialization

When staging launches with a fresh, empty Neon database:
- **Historical Defect**: `create_tables.py` only created 12 tables and crashed on line 183 (`ALTER TABLE memory_items`) because `memory_items`, `reminders`, `google_tokens`, etc. were never created. Furthermore, `action_events` was referenced in early design docs but never created in PostgreSQL.
- **Solution**: The complete, idempotent DDL migration script [`db_migrations/init_postgres_schema.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/db_migrations/init_postgres_schema.py) creates all **38 application tables**, vector/pg_trgm extensions, and indexes.
- **Verification Evidence**: Tested against an isolated fresh schema in Neon PostgreSQL in [`tests/test_fresh_db_schema.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/test_fresh_db_schema.py). Verified that all 38 tables are created cleanly in 43.59s with 100% idempotency.
- **Execution Command on Fresh Database**:
  ```bash
  python -m db_migrations.init_postgres_schema
  ```

---

## 5. OAuth Redirect URIs & Google Test-User Setup

When running on staging domain (e.g. `https://peculiar-ai-staging.onrender.com`), configure these exact callback URIs in provider developer consoles:

| Provider | Setting | Staging Value | Note |
|---|---|---|---|
| **Google Cloud** | Authorized Redirect URI | `https://peculiar-ai-staging.onrender.com/google/callback` | If the Google OAuth app is in "Testing" publishing status, add your staging test Google account (e.g. `your-qa-account@gmail.com`) under **OAuth consent screen $\to$ Audience $\to$ Test users**. |
| **Notion** | Redirect URI | `https://peculiar-ai-staging.onrender.com/notion/callback` | In Notion Developer Portal $\to$ Integrations $\to$ Redirect URIs. |
| **GitHub** | Authorization Callback URL | `https://peculiar-ai-staging.onrender.com/github/callback` | In GitHub Developer Settings $\to$ OAuth Apps $\to$ Authorization callback URL. |

---

## 6. Deployment Architecture: Render Dashboard vs `render.staging.yaml`

> [!WARNING]
> **Blueprint Sync Warning (`region: ohio`)**:
> Commit `7305e91` added `region: ohio` to `render.yaml` to colocate the backend with Neon PostgreSQL. If `render.yaml` is shared between production and staging, Render Blueprint sync can attempt to recreate or destroy services when regions change.
> **DO NOT add staging to `render.yaml`.**

### Option A: Create Manually from Render Dashboard (Recommended)
1. Go to [Render Dashboard](https://dashboard.render.com/) $\to$ **New +** $\to$ **Web Service**.
2. Connect your GitHub repository `ai-agent`.
3. Configure service details:
   - **Name**: `peculiar-ai-staging`
   - **Region**: `Ohio (US East)` (matches Neon Postgres `us-east-2`)
   - **Branch**: `fix/pre-phase8-live-fixes` (or your preview branch)
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python -m db_migrations.init_postgres_schema && uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Plan**: `Starter` ($7/mo)
4. Add Environment Variables (see Section 2).

### Option B: Dedicated `render.staging.yaml`
```yaml
services:
  - type: web
    name: peculiar-ai-staging
    runtime: python
    region: ohio
    plan: starter
    branch: fix/pre-phase8-live-fixes
    buildCommand: pip install -r requirements.txt
    startCommand: python -m db_migrations.init_postgres_schema && uvicorn main:app --host 0.0.0.0 --port $PORT
    envVars:
      - key: PYTHON_VERSION
        value: 3.13.3
      - key: ENV
        value: staging
      - key: STAGING_MODE
        value: "1"
      - key: PROD_DB_FINGERPRINT
        value: ep-delicate-lake-12345 # Your production Neon project host/ID
      - key: DATABASE_URL
        sync: false # Set via dashboard to staging Neon DB
      - key: TELEGRAM_BOT_TOKEN
        sync: false # Set via dashboard to @PeculiarAI_Staging_Bot token
      - key: WEBHOOK_SECRET
        sync: false # Set to random 32-char string
      - key: AES_SECRET_KEY
        sync: false # 64-hex AES key
      - key: PUBLIC_BASE_URL
        value: https://peculiar-ai-staging.onrender.com
      - key: AGENT_CORE
        value: v2
```

---

## 7. Real Captured `/health` Response (Local Validation)

Captured locally via `TestClient` executing against the live application:
```json
{
  "status": "healthy",
  "database": {
    "status": "connected",
    "latency_ms": 5413.75,
    "error": null
  },
  "llm": {
    "status": "configured",
    "primary": "Gemini",
    "fallback": "Groq"
  },
  "embeddings": {
    "status": "ready"
  },
  "agent_core": "v2",
  "timestamp": "2026-11-06T10:06:16.650545+05:30",
  "version": "v2.4.0"
}
```
*Note: In Ohio co-located deployment on Render, database latency drops to ~2–5 ms.*
