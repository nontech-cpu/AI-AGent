# Phase 1: Time & Date Correctness - Verification Summary

## 1. Executive Summary & Root Cause Resolution
The primary defect causing the bot to reply with **"Dekhiye, maine check kiya hai aur mere paas aaj (24 January) ke liye koi bhi reminder save nahi hai"** when asked for today's reminders was verified and fixed:
1. **Root Cause**:
   - Primary model (`gemini-3-flash-preview`) experienced 429 quota exhaustion.
   - Cognitive loop fell back to Groq (`openai/gpt-oss-120b`).
   - The fallback model defaulted to its static training knowledge cutoff date (**24 January 2025**).
   - In `agent/tools.py:2073`, the system previously accepted model-supplied `due_date` without validation, saving reminders for 2025-01-24 instead of today (`2026-11-05`).
   - On retrieval turns, the model queried reminders, found stored reminders anchored to today (`2026-11-05`), but believed today was `24 January 2025`, falsely asserting that the user had no reminders today and suggesting they check Google Calendar!
2. **Permanent Solution**:
   - Created centralized clock module [`core/timeutil.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/core/timeutil.py).
   - Replaced all raw calls across the codebase to `datetime.now()`, `date.today()`, and `utcnow()`.
   - Implemented strict validation rejecting past dates / cutoff years (`2024`, `2025`) with structured error `invalid_date_or_in_past`.
   - Injected runtime prompt ground truth anchors `{TODAY_ISO}`, `{NOW_USER_STR}`, `{WEEKDAY}` so models in both Tier 1 and Tier 2 are unambiguously grounded in current reality.

---

## 2. Crashes Fixed & Fallback Proof (Directive 1)
1. **Crash 1**: `UnboundLocalError: cannot access local variable 'IST'` in `agent/loop.py:1096`. Redundant inner `from timing import IST` shadowed the outer module-level import. Removed.
2. **Crash 2**: `UnboundLocalError: cannot access local variable 'google_services'` in `agent/tools.py:2230` during timezone resolution in `create_reminder`. Removed shadowed local import.
3. **Fallback Reply Proof**:
   - Verified that unhandled exceptions bubbling up to `main.py:2452-2470` execute the fallback exception handler:
     ```python
     except Exception as e:
         logger.exception(f"Unhandled error in handle_message: {e}")
         return "Haanji, network mein thoda fluctuation ho gaya tha. Ek baar dobara bataenge?"
     ```
   - Regression test in `tests/test_crashes_and_fallback.py` verifies both crash fixes and simulates unhandled exceptions to prove it triggers the network fluctuation string. Output: **3 passed in 1.22s**.

---

## 3. Single Clock Exposer (`core/timeutil.py`) (Directive 2)
- Exposes:
  - `timeutil.now(tz)`
  - `timeutil.today(tz)`
  - `timeutil.utcnow()`
  - `timeutil.resolve_date(val, tz, allow_past=False)`
  - `timeutil.resolve_time(val)`
  - `timeutil.resolve_datetime(date_val, time_val, tz)`
  - `timeutil.set_frozen_clock(clock)` / `timeutil.get_frozen_clock()`
- [`tests/harness/clock.py`](file:///c:/Users/neera/OneDrive/ai%20agent/ai-agent/tests/harness/clock.py): `FrozenClock` hooks only `core.timeutil.set_frozen_clock()`.
- Replaced raw time references across 16 core files. (Intentionally preserved: standard library `time.time()`, `time.perf_counter()` for latency profiling).

---

## 4. Dual Database Verification (Directive 3)
1. **SQLite In-Memory**:
   - `tests/test_phase1_time_and_clash.py`: **4 passed in 0.93s**.
2. **Neon PostgreSQL Isolated Test Schema**:
   - `tests/test_phase1_postgres_schema.py`:
     - Creates isolated schema `test_phase1` using `CREATE SCHEMA IF NOT EXISTS test_phase1;` and table cloning via `CREATE TABLE test_phase1.{tbl} (LIKE public.{tbl} INCLUDING ALL);`.
     - Uses connection and pool-checkout event listeners on SQLAlchemy engine: `SET search_path TO test_phase1, public;` to ensure transaction-pooler resilience with PgBouncer.
     - Automatically tears down the schema after tests: `DROP SCHEMA IF EXISTS test_phase1 CASCADE;`.
     - Output: **3 passed in 56.77s**. (Saved to `docs/verification/phase1/postgres_schema_test.txt`).

---

## 5. Outdated Test Rewrites (Directive 4)
- 14 tests were rewritten to assert behavioral contracts (status codes, JSON payloads, DB row state) instead of exact Hinglish strings.
- Complete before/after documentation saved in `docs/verification/phase1/test_rewrites.md`.

---

## 6. Git Content Secrets Scan (Directive 5)
- Full commit history content scan (`git log -p --all --full-history`) and working tree scan across 7 regex signatures.
- **Result: 0 secrets found**.
- Complete scan report saved in `docs/verification/phase1/secrets_scan.md`.

---

## 7. Real Turn Latency Measurement (Directive 6)
- Live turn latency measured on real query `"bhy mere reminder toh btana aajke"`:
  - Total turn latency: **39.56s** (dominated by ~26s of cross-continent TLS roundtrips from local PC to remote Neon Postgres).
  - Gemini inference speed: **3.2s to 3.4s** per step.
  - In-memory cognitive loop overhead (Scripted FakeLLM): **49.3 ms**.
  - Detailed breakdown saved in `docs/verification/phase1/turn_latency.md`.

---

## 8. Deliverables Index in `docs/verification/phase1/`
1. `pytest_phase1.txt`: Full raw pytest output.
2. `postgres_schema_test.txt`: Raw pytest output of isolated Neon Postgres schema execution.
3. `test_rewrites.md`: Behavioral rewrite documentation for all 14 tests.
4. `secrets_scan.md`: Git history content secrets scan results.
5. `turn_latency.md`: Real turn latency measurements.
6. `phase1_summary.md`: This comprehensive verification summary.

