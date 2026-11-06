# Render Deployment, Keep-Alive, and Infrastructure Resilience (Phase 7)

## 1. Executive Summary & Honest Architecture Limits

### Render Free Tier Reality
- **Sleep Mechanism**: On Render's Free Web Service tier, instances spin down (sleep) after **15 minutes** of inbound HTTP inactivity.
- **Async Execution Freeze**: When Render spins down the service, the Python process freezes. All in-memory background tasks (`asyncio.sleep`, background reminders, polling loops) are suspended.
- **Honest Scheduling Limitation**: An agent running solely on the Render Free Plan **cannot guarantee exact on-time reminders or morning briefings** without inbound traffic or an external trigger. If a reminder is scheduled for 10:00 AM and no web traffic reaches the service between 9:45 AM and 10:15 AM, the reminder will not fire at 10:00 AM.
- **Resolution Strategy**:
  1. **Zero Data Loss (DB Scheduler & Catch-up Pass)**: Reminders are persisted in PostgreSQL with claim locks (`due_at`, `sent_at`, `claimed_by`, `claimed_at`). When the service wakes up from a request or restart, a **Startup Catch-Up Pass** immediately scans for past-due reminders and delivers them with a clear late disclosure notice (`⚠️ [Delivered late — Server wake-up catch-up]`).
  2. **Free Keep-Alive Setup (UptimeRobot / cron-job.org)**: A free HTTP pinger hitting `/health` every 10 minutes prevents Render from entering sleep state during active hours.
  3. **Paid Upgrade Alternative ($7/month)**: Upgrading to Render Starter tier ($7/month) removes spin-down entirely, running the container 24/7 with dedicated CPU and guaranteed on-time reminder execution.

---

## 2. Phase 7 Architecture Components

### A. DB-Driven Distributed Scheduler (`db.py` & `main.py`)
- **Schema Columns**:
  - `due_at`: UTC timestamp when the reminder is scheduled to trigger.
  - `sent_at`: UTC timestamp when successfully delivered to user.
  - `claimed_by`: Worker/process identifier currently processing the item.
  - `claimed_at`: UTC timestamp when the lock was acquired.
- **Concurrency & Multi-Instance Safety**:
  - PostgreSQL: Implemented using `SELECT ... FOR UPDATE OF r SKIP LOCKED` with automatic stale lock reclaiming (`claimed_at < NOW() - 300s`).
  - SQLite (Local / Test): Implemented with atomic transaction blocks ensuring zero race conditions.
- **Wake-Up Catch-Up Pass (`main.py:lifespan`)**:
  - Upon server startup/wake-up, `run_startup_catchup_pass()` runs before opening the HTTP port.
  - Identifies all unclaimed, unsent reminders where `due_at <= NOW()`.
  - Sends immediate notifications via Telegram with delay calculation and explicit late notice if `delay_seconds > 120`.

### B. Fail-Fast Startup Validation (`main.py:validate_startup_environment`)
- Prevents silent failure modes and data corruption in production environments.
- **Strict Production Checks**:
  - `ENV=production` or `RENDER=true`:
    - `DATABASE_URL` must be set and must point to PostgreSQL (`postgres://` or `postgresql://`). Silent fallback to `sqlite:///:memory:` is strictly disabled and throws `RuntimeError`.
    - `TELEGRAM_BOT_TOKEN` must be present.
    - At least one LLM key (`GEMINI_API_KEY` or `GROQ_API_KEY`) must be configured.
- Validated at the start of FastAPI `lifespan` before any route handling begins.

### C. Live Production Health Check (`/health` & `render.yaml`)
- **Endpoint**: `GET /health`
- **Behavior**:
  - Executes live `SELECT 1` ping against Neon PostgreSQL.
  - Measures DB round-trip latency in milliseconds.
  - Checks LLM API key readiness and embedding model status.
  - Returns HTTP `200 OK` with JSON telemetry when healthy.
  - Returns HTTP `503 Service Unavailable` if database connectivity fails.
- **Render Configuration**:
  - Added `healthCheckPath: /health` to `render.yaml` web service definition.

### D. Crash & Restart State Reconstruction (`state/active_context.py` & `main.py`)
- **Active Context**:
  - Persistent table `active_contexts (chat_id, context_data, updated_at)`.
  - Cache-aside pattern: Reads from memory; on container restart/cache miss, transparently rebuilds state from PostgreSQL.
- **Recent Chat Turns**:
  - `get_recent_chat_turns(chat_id, limit=20)` automatically reconstructs conversational history from `chat_logs` on container restart.

### E. Secret Masking & Structured Logging (`connectors/trace.py`)
- Regular expressions identify and mask sensitive tokens before log emission:
  - Telegram bot tokens (`bot\d+:[A-Za-z0-9_-]+`)
  - Notion API keys (`secret_[A-Za-z0-9_-]{10,}`, `ntn_[A-Za-z0-9_-]{10,}`)
  - Groq API keys (`gsk_[A-Za-z0-9_-]{10,}`)
  - Google API keys (`AIza[A-Za-z0-9_-]{30,}`)
  - GitHub Personal Access Tokens (`ghp_[A-Za-z0-9_-]{10,}`, `github_pat_[A-Za-z0-9_-]{10,}`)
  - Credit cards and 6-digit OTP codes.
- Structured JSON logging to stdout via `logging.getLogger("turn_trace")` for seamless Render log viewing and aggregation.

---

## 3. How to Configure Free 24/7 Keep-Alive (Step-by-Step)

If maintaining the free Render tier without paying $7/month:

1. **Get Public URL**:
   - Note your public Render service URL: `https://<your-service-name>.onrender.com`
2. **Setup Free Pinger (e.g. cron-job.org)**:
   - Create a free account on [cron-job.org](https://cron-job.org).
   - Create a new cron job:
     - **Title**: `PeculiarAI Render Keep-Alive`
     - **URL**: `https://<your-service-name>.onrender.com/health`
     - **Execution Schedule**: Every 10 minutes (`*/10 * * * *`).
     - **Request Method**: `GET`
3. **Verify Execution**:
   - Check the execution log on cron-job.org to confirm HTTP 200 responses.
   - Render's 15-minute inactivity counter resets on every ping, preventing sleep.

---

## 4. Paid Upgrade Option (Render Starter)

For mission-critical production with SLA guarantees:
- **Plan**: Render Starter Web Service ($7/month)
- **Benefits**:
  - Container runs 24/7 with zero spin-down.
  - Constant 0.5 CPU / 512 MB dedicated RAM.
  - Reminders, calendar checks, and background sync loops run strictly on-schedule without external ping dependencies.

