# Step 7: Deployment State, Branch Topology & Region Runbook

## 1. Active Render Git SHA & Branch Audit

### A. Current Live Deployment on Render
- **Live URL**: `https://peculiar-ai-backend.onrender.com`
- **Active Git SHA**: `3c27a2e86bd78cdca46d767a79eafa4beac44db6` (`3c27a2e`)
  - Commit Message: `fix(temporal): anchor model to real-world date and add is_today metadata to list_reminders`
  - Timestamp: `Thu Nov 5 13:26:43 2026 +0530`
- **Verification Evidence**:
  - `curl -s https://peculiar-ai-backend.onrender.com/health` returns `{"status":"ok"}`.
  - `curl -s https://peculiar-ai-backend.onrender.com/debug/db-latency` returns `{"detail":"Not Found"}` (confirming that Phase 1 commit `a0a3044` has not yet been deployed to Render).

### B. Branch Topology
| Branch | Location | Head Commit | Description & Status |
|---|---|---|---|
| `main` | Local & Remote (`origin/main`) | `3c27a2e` | Live production branch connected to Render `autoDeploy: true`. |
| `fix/phase1-time-correctness` | Local only | `21b6102` | Cumulative feature branch containing Phases 1 through 7. Not pushed to remote. |
| `fix/pre-phase8-live-fixes` | Local only (Current) | Working tree | Contains Phase 7b live grounding fixes, Gmail search typed errors, prompt weekday anchoring, and verification tests. |

---

## 2. Order of Merge & Deployment

To deploy cleanly to production once approved:
1. **Local Verification**: Ensure all Phase 7b tests pass locally against the test harness.
2. **Merge Sequence**:
   - Merge `fix/pre-phase8-live-fixes` into `main` (fast-forward or squash merge).
3. **Deployment Trigger**:
   - Push `main` to `origin/main` (`git push origin main`).
   - Render automatically triggers build via `autoDeploy: true`.
4. **Post-Deployment Verification**:
   - Verify `/health` returns `200 OK`.
   - Verify `/debug/db-latency` returns intra-region latency.
   - Run live smoke tests.

---

## 3. Render Rollback Runbook

If any regression occurs upon deploying to Render:
1. **Instant Webhook / Dashboard Rollback**:
   - Open [Render Dashboard](https://dashboard.render.com).
   - Select service `peculiar-ai-backend`.
   - Click **Deploys** tab in left sidebar.
   - Find commit `3c27a2e` in the deployment list.
   - Click the three dots `...` on the right and select **Rollback to this deploy**.
   - Render instantly switches active traffic to the previous container image without rebuilding.
2. **Git Rollback**:
   - `git revert HEAD`
   - `git push origin main`

---

## 4. Render Region Migration Runbook (`oregon` -> `ohio`)

Render is currently in `oregon` (`us-west`), while Neon PostgreSQL is in `us-east-2` (`ohio`).
`render.yaml` has `region: ohio` in commit `7305e91`.

### Survival of Environment Variables & Telegram Webhook:
1. **Env Vars**: Render maintains environment variables at the service level. When applying `render.yaml` with `region: ohio`, environment variables defined in the dashboard remain attached to the service.
2. **Service Domain & Telegram Webhook**:
   - If the service is updated in-place via Blueprint sync, the URL `https://peculiar-ai-backend.onrender.com` is preserved, meaning the Telegram webhook (`/webhook`) **does not change** and requires zero downtime.
   - If Render requires creating a new service instance (e.g. `peculiar-ai-backend-ohio`), run:
     ```bash
     python set_webhook.py --url https://<new-service-url>.onrender.com
     ```
   - Test with: `curl -s https://api.telegram.org/bot<TOKEN>/getWebhookInfo` to confirm `url` points to the new service with 0 pending updates.

