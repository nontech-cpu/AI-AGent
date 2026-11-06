# Phase 7c Item 9: Render Staging Web Service Deployment Specification

## 1. Overview & Guardrails
- **Objective**: Establish an isolated Staging deployment of PeculiarAI to test `fix/pre-phase8-live-fixes` without modifying the live production backend (`peculiar-ai-backend.onrender.com`) or production Telegram webhook.
- **Strict Isolation Guarantees**:
  1. The production Render service continues running `main` at commit `3c27a2e`.
  2. The production Telegram webhook continues pointing to `peculiar-ai-backend.onrender.com`.
  3. Staging uses an independent test Telegram bot created via BotFather (`@PeculiarAI_Staging_Bot`).
  4. Staging operates with `AGENT_CORE=v2` and runs the full Phase 7c code.

---

## 2. Render Blueprint Specification (`render.yaml`)

Add the following independent service block to `render.yaml` (or create via Render Dashboard):

```yaml
  - type: web
    name: peculiar-ai-staging
    runtime: python
    region: ohio
    branch: fix/pre-phase8-live-fixes
    buildCommand: pip install -r requirements.txt
    startCommand: uvicorn main:app --host 0.0.0.0 --port $PORT
    healthCheckPath: /health
    plan: free
    autoDeploy: false
    envVars:
      - key: PYTHON_VERSION
        value: 3.11.9
      - key: AGENT_CORE
        value: "v2"
      - key: DISABLE_LOCAL_EMBEDDINGS
        value: "1"
      - key: BOT_TOKEN
        sync: false  # Paste STAGING Telegram Bot Token
      - key: TELEGRAM_SECRET
        sync: false  # Generate a random 32-character staging secret
      - key: DATABASE_URL
        sync: false  # Points to staging Postgres database (or separate schema)
      - key: GEMINI_API_KEY
        sync: false
      - key: GROQ_API_KEY
        sync: false
```

---

## 3. Step-by-Step Deployment Instructions

### Step 1: Create a Staging Telegram Bot
1. Open Telegram and message `@BotFather`.
2. Send `/newbot`.
3. Set Name: `PeculiarAI Staging`.
4. Set Username: `PeculiarAI_Staging_bot` (or available variant).
5. Copy the generated Bot Token (e.g. `7890123456:AAH...`).

### Step 2: Create the Render Web Service
1. Log in to [dashboard.render.com](https://dashboard.render.com).
2. Click **New +** -> **Web Service**.
3. Connect your GitHub repository: `neeravmishra18/ai-agent`.
4. Configure service details:
   - **Name**: `peculiar-ai-staging`
   - **Branch**: `fix/pre-phase8-live-fixes`
   - **Region**: `Ohio (US East)` (same as DB)
   - **Runtime**: `Python 3`
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - **Instance Type**: `Free`

### Step 3: Configure Environment Variables
In the Render Service Settings under **Environment**, add:
- `PYTHON_VERSION`: `3.11.9`
- `AGENT_CORE`: `v2`
- `BOT_TOKEN`: `<STAGING_BOT_TOKEN_FROM_BOTFATHER>`
- `TELEGRAM_SECRET`: `<RANDOM_32_CHAR_SECRET>`
- `DATABASE_URL`: `<POSTGRES_DATABASE_URL>`
- `GEMINI_API_KEY`: `<YOUR_GEMINI_API_KEY>`
- `GROQ_API_KEY`: `<YOUR_GROQ_API_KEY>`
- `DISABLE_LOCAL_EMBEDDINGS`: `1`

### Step 4: Deploy & Verify Health
1. Click **Create Web Service** (or **Manual Deploy** -> **Deploy latest commit**).
2. Wait for build to complete.
3. Test health check:
   ```bash
   curl -i https://peculiar-ai-staging.onrender.com/health
   ```
   **Expected Response**:
   ```json
   {
     "status": "ok",
     "agent_core": "v2",
     "database": "connected",
     "version": "phase7c-ready"
   }
   ```

### Step 5: Set Staging Webhook
Configure Telegram webhook specifically for the staging bot:
```bash
curl -X POST "https://api.telegram.org/bot<STAGING_BOT_TOKEN>/setWebhook" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://peculiar-ai-staging.onrender.com/telegram/webhook",
    "secret_token": "<STAGING_TELEGRAM_SECRET>"
  }'
```

---

## 4. Real Latency & "Typing" Chat Action
To ensure real-time responsiveness and eliminate user anxiety during multi-step tool turns:
- An asynchronous task is started when a turn begins.
- If the turn latency exceeds **2.0 seconds**, `sendChatAction: typing` is dispatched to Telegram.
- If the turn finishes within 2.0s, the task is cancelled with zero overhead.
