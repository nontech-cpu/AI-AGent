# PeculiarAI Production Deployment Runbook

## 1. Pre-Deployment Safety Protocol
1. **Neon PostgreSQL Snapshot**:
   - Before applying schema migrations, trigger a point-in-time branch/snapshot in the Neon console on project `peculiar-ai` (branch `main`).
2. **Current Production Commit**:
   - Baseline Golden Commit for Instant Rollback: `3c27a2e` (or latest hardening `e878cfe`).
   - Rollback Command:
     ```bash
     git reset --hard e878cfe555e1dbba0b777a949f579133ff08ea7c
     git push origin main --force
     ```

---

## 2. Migration Execution Order
Execute database migrations strictly in this order:
1. `db._init_memory_items_status_column()`: Adds `status VARCHAR(50) DEFAULT 'active'`.
2. `db._init_memory_items_recurrence_column()`: Adds `recurrence VARCHAR(50) DEFAULT 'none'`.
3. `db._init_reminders_scheduler_columns()`: Adds `claimed_by`, `claimed_at`, `due_at`.
4. `ensure_pending_table()`: Creates `pending_actions` table for 2-phase confirmation.
5. `_init_sqlite_if_needed()`: Prepares memory tables.

---

## 3. Post-Deployment Verification & Health Checks
1. **Health Endpoint**:
   ```bash
   curl -s https://peculiar-ai-backend.onrender.com/health | jq .
   ```
   Assert: `"status": "healthy"`, `"database.status": "connected"`, `"agent_core": "v2"`.
2. **Live Smoke Acceptance Turn**:
   - Send `hi` via Telegram -> Assert friendly greeting; assert zero database deletions.
   - Send `merko sugar readings ki pdf bhejo` -> Assert clean PDF delivery without "Aapka request note kar liya gaya hai" hijacking.
   - Send past reminder (e.g. 10am after 10am has passed) -> Assert agent informs past time or schedules for tomorrow; zero past rows created.

---

## 4. First 24-Hour Operations Checklist
- [ ] Monitor Render log stream for any `ProviderTimeoutError` or `RateLimitedError`.
- [ ] Verify `turn_traces` table: latency p50 < 3000ms, `is_suspect` = 0.
- [ ] Confirm no token leaks in logs (privacy scrubbing active).
- [ ] Run daily soft-delete purge check (`db.purge_expired_soft_deletes()`).

---

## 5. Free-Plan Honesty Specification (F5)
- **Render Free Starter Tier Reality**:
  - The free web service spins down (sleeps) after 15 minutes of inactivity.
  - An inbound message triggers a cold start taking 30–50 seconds before responding.
  - While asleep, background periodic tasks (`asyncio` scheduler in the web process) do NOT fire until the process wakes up.
  - **Mitigation on Free Tier**: The scheduler includes a catch-up mechanism (`claim_pending_reminders`) that catches up all due reminders immediately upon wake-up.
- **Paid Background Worker Guarantee**:
  - Deploying a persistent Background Worker ($7/mo) ensures 24/7 continuous uptime without sleep, guaranteeing sub-second reminder trigger precision.

