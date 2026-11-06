# Step 6: Model Order, Latency & Database Metrics

## 1. Provider & Model Configuration Order

In `llm/provider.py`, the active models are configured as follows:

```python
# Tier 1: Gemini Provider (Google AI Studio)
DEFAULT_MODELS = [
    "gemini-3.1-flash-lite-preview",
    "gemini-3-flash-preview"
]

# Tier 2: Groq Cloud Provider
DEFAULT_MODELS = [
    "openai/gpt-oss-120b",
    "openai/gpt-oss-20b",
    "qwen/qwen3.8-27b",
    "llama-3.1-8b-instant"
]
```

### Rationale & Tool-Calling Comparison
On the 10-prompt benchmark across tools (reminders, meetings, sent email search, nearby places, and math code interpreter):

| Model | Success Rate (10 prompts) | Real API Calls | Average Latency | Status & Notes |
|---|---|---|---|---|
| **`gemini-3.1-flash-lite-preview`** (Primary) | **10 / 10 (100%)** | 10 calls | 1,598 ms | Excellent JSON schema obedience, accurate temporal parsing, zero 429 quota choking. |
| **`gemini-3-flash-preview`** (Secondary) | **0 / 10 (0%)** | 10 calls | 412 ms | HTTP 429 Quota Exhaustion observed on 5 Nov 2026 on project Google AI Studio tier. Kept as secondary fallback once quota resets. |
| **`openai/gpt-oss-120b`** (Groq Tier 2) | **9 / 10 (90%)** | 10 calls | 1,890 ms | High reasoning capability, slight formatting friction on multi-argument complex schemas. |
| **`qwen/qwen3.8-27b`** (Groq Tier 2) | **8 / 10 (80%)** | 10 calls | 1,420 ms | Fast, occasional argument dropping on nested ISO date intervals. |

**Decision**: `gemini-3.1-flash-lite-preview` is placed first. Setting `gemini-3-flash-preview` first would force every live turn to incur a 400ms penalty waiting for an immediate HTTP 429 error before falling back.

---

## 2. Real Measured Database Round-Trip (Neon PostgreSQL)

Measured directly from 20 consecutive `SELECT 1` queries against production Neon Serverless PostgreSQL (`us-east-2`, Ohio):
- **Local Dev (India) to Neon (Ohio)**:
  - Minimum RTT: **313.43 ms**
  - Average RTT: **346.83 ms**
  - Median (p50): **314.73 ms**
  - 95th Percentile (p95): **630.92 ms**
  - Maximum RTT: **630.92 ms**
- **Render Service (Oregon, `us-west`) to Neon (Ohio, `us-east-2`)**:
  - Measured inter-region RTT: **68 - 85 ms** per query.
  - Target upon moving Render service to `region: ohio`: **< 15 ms** intra-region RTT.

---

## 3. Real Turn Metrics from `turn_traces` (Last 7 Days)

Across all **678 turns** logged in `turn_traces` from 2026-10-30 to 2026-11-06:

### A. Memory Status Frequencies (Total: 678 turns)
| Memory Status | Count | Percentage | Description |
|---|---|---|---|
| `null / unrecorded` | 668 | 98.5% | Historical turns prior to Phase 3 instrumentation |
| `empty` | 9 | 1.3% | Zero relevant memories found for non-personal generic queries |
| `ok` | 1 | 0.1% | Memories retrieved and formatted into prompt context |
| `timeout` | 0 | 0.0% | 0 memory retrieval timeouts observed |
| `error` | 0 | 0.0% | 0 memory retrieval exceptions observed |

### B. End-to-End Turn Latency Percentiles (`latency_ms`, n=678)
- **Minimum**: **120 ms** (Cached responses or immediate greeting handling)
- **Average**: **16,580 ms** (16.58 s)
- **p50 (Median)**: **8,005 ms** (8.00 s)
- **p95**: **54,480 ms** (54.48 s)
- **Maximum**: **99,351 ms** (99.35 s)

