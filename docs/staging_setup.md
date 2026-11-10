# PeculiarAI Staging Environment Setup Guide

This document specifies the exact configuration, environment variables, security guards, and verification procedures for the isolated PeculiarAI Staging Environment.

---

## 1. Architectural Topology & Isolation Guard

```mermaid
flowchart TD
    subgraph Development / Staging
        DevCode["Staging Service (Render Dashboard)"] --> DevDB["Staging Neon PostgreSQL (Project: peculiar-staging)"]
        DevCode --> StagingBot["Telegram Staging Bot (@PeculiarAI_Staging_Bot)"]
    end

    subgraph Production (Protected)
        ProdCode["Production Service (peculiar-ai-backend)"] --> ProdDB["Production Neon PostgreSQL (ep-steep-dawn)"]
        ProdCode --> ProdBot["Telegram Production Bot (@PeculiarAI_Bot)"]
    end
```

### Critical Environment Isolation Guard
The application contains an active safety guard in [`tests/conftest.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/conftest.py) and startup hooks:
- **`PROD_DB_FINGERPRINT`**: `"ep-steep-dawn-b4f6w0un"`
- If `ENV != "production"` and `DATABASE_URL` matches the production fingerprint, the application **refuses to start** and immediately raises `RuntimeError("FATAL: Engine connected to production Neon DB!")`.

---

## 2. Environment Variables Specification

The staging service created in Render must configure the following exact environment variables:

| Variable Name | Staging Value Description | Code Location |
| :--- | :--- | :--- |
| `AGENT_CORE` | `v2` (Unified Agent Loop) | `main.py:2869`, `main.py:3650` |
| `DATABASE_URL` | Dedicated empty staging Neon connection string (`postgresql://...@ep-staging...`) | `db.py:22` |
| `AES_SECRET_KEY` | 32-byte staging secret for Fernet token encryption | `google_auth.py`, `connectors/` |
| `AES_SECRET_KEY_FALLBACK` | (Optional during rotation) Old staging AES secret key | `core/crypto.py` |
| `TELEGRAM_BOT_TOKEN` | Staging bot token issued by @BotFather | `main.py` |
| `WEBHOOK_SECRET` | Secret token verified against `X-Telegram-Bot-Api-Secret-Token` | `main.py` |
| `PUBLIC_BASE_URL` | Staging public URL (e.g. `https://peculiar-ai-staging.onrender.com`) | `google_auth.py` |
| `GEMINI_API_KEY` | Google Generative Language API key | `llm/provider.py:234` |
| `GROQ_API_KEY` | Groq Cloud API key (fallback tier) | `llm/provider.py:380` |
| `FORCE_PROVIDER_FAIL` | Test flag: set to `"gemini"` to force live failover to Groq | `llm/provider.py` |

---

## 3. OAuth Redirect URIs Configuration

Configure the staging redirect URLs in the respective developer consoles:
- **Google Cloud Console**: `https://peculiar-ai-staging.onrender.com/auth/google/callback`
- **Notion Developers**: `https://peculiar-ai-staging.onrender.com/auth/notion/callback`
- **GitHub OAuth App**: `https://peculiar-ai-staging.onrender.com/auth/github/callback`

---

## 4. Zero-Downtime AES Secret Key Rotation Procedure

To rotate `AES_SECRET_KEY` without invalidating existing tokens or causing downtime:
1. Generate a fresh 32-byte key:
   ```bash
   python -c "import secrets; print(secrets.token_urlsafe(32))"
   ```
2. Update environment variables in staging/production:
   - Set `AES_SECRET_KEY_FALLBACK = <OLD_SECRET_KEY>`
   - Set `AES_SECRET_KEY = <NEW_SECRET_KEY>`
3. Restart service. The decryption engine attempts `AES_SECRET_KEY` first. If decryption fails, it falls back to `AES_SECRET_KEY_FALLBACK`, decrypts the ciphertext, re-encrypts with `AES_SECRET_KEY`, and updates the database row automatically.
4. Once all rows are re-encrypted, unset `AES_SECRET_KEY_FALLBACK`.

