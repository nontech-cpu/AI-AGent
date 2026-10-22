"""
PeculiarAI — Turn Trace & Observability Engine (Part 5 & Part 6)
Stores structured execution traces in DB with secret redaction and suspect-turn tracking for auto-regression promotion.
"""
import re
import json
import uuid
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy import text
import db
from timing import IST

# Redaction patterns for secrets
SECRET_PATTERNS = [
    (re.compile(r"ghp_[a-zA-Z0-9]{20,}", re.I), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{30,}", re.I), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"(?:secret_|ntn_)[a-zA-Z0-9_]{20,}", re.I), "[REDACTED_NOTION_TOKEN]"),
    (re.compile(r"Bearer\s+[a-zA-Z0-9_\-\.]{15,}", re.I), "Bearer [REDACTED_TOKEN]"),
    (re.compile(r"\b[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}[ -]?[0-9]{4}\b"), "[REDACTED_CARD]"),
    (re.compile(r"\b\d{4,8}\b(?=.*(?:otp|code))", re.I), "[REDACTED_OTP]")
]

def redact_secrets(data: Any) -> Any:
    """Recursively redacts API keys, tokens, OTPs, and card numbers from strings/dicts/lists."""
    if isinstance(data, str):
        cleaned = data
        for pat, repl in SECRET_PATTERNS:
            cleaned = pat.sub(repl, cleaned)
        return cleaned
    elif isinstance(data, dict):
        return {k: redact_secrets(v) for k, v in data.items()}
    elif isinstance(data, list):
        return [redact_secrets(item) for item in data]
    return data

def init_trace_tables():
    """Initializes turn_traces table if not present."""
    try:
        with db.engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS turn_traces (
                    id SERIAL PRIMARY KEY,
                    trace_id VARCHAR(64) UNIQUE NOT NULL,
                    chat_id BIGINT NOT NULL,
                    message TEXT,
                    detected_connector VARCHAR(64),
                    tools_called JSONB,
                    tool_results JSONB,
                    guard_results JSONB,
                    latency_ms INTEGER DEFAULT 0,
                    tokens INTEGER DEFAULT 0,
                    reply_path VARCHAR(64),
                    is_suspect BOOLEAN DEFAULT FALSE,
                    suspect_reason TEXT,
                    created_at TIMESTAMPTZ DEFAULT now()
                )
            """))
    except Exception as exc:
        print("init_trace_tables warning:", exc)

# Initialize on module import
init_trace_tables()

def log_turn_trace(
    chat_id: int,
    message: str,
    tools_called: List[str],
    tool_results: List[Dict[str, Any]],
    guard_results: Dict[str, Any],
    latency_ms: int,
    tokens: int,
    reply_path: str,
    detected_connector: Optional[str] = None,
    is_suspect: bool = False,
    suspect_reason: Optional[str] = None
) -> str:
    """Logs a clean, redacted turn trace to DB."""
    trace_id = f"tr_{uuid.uuid4().hex[:12]}"
    clean_msg = redact_secrets(message)
    clean_tools = redact_secrets(tools_called)
    clean_results = redact_secrets(tool_results)
    clean_guards = redact_secrets(guard_results)

    try:
        with db.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO turn_traces (
                        trace_id, chat_id, message, detected_connector,
                        tools_called, tool_results, guard_results,
                        latency_ms, tokens, reply_path, is_suspect, suspect_reason, created_at
                    ) VALUES (
                        :tid, :cid, :msg, :conn,
                        CAST(:tc AS jsonb), CAST(:tr AS jsonb), CAST(:gr AS jsonb),
                        :lat, :tok, :rp, :susp, :sreason, :cat
                    )
                """),
                {
                    "tid": trace_id,
                    "cid": chat_id,
                    "msg": clean_msg,
                    "conn": detected_connector,
                    "tc": json.dumps(clean_tools),
                    "tr": json.dumps(clean_results),
                    "gr": json.dumps(clean_guards),
                    "lat": latency_ms,
                    "tok": tokens,
                    "rp": reply_path,
                    "susp": is_suspect,
                    "sreason": suspect_reason,
                    "cat": datetime.now(IST)
                }
            )
    except Exception as exc:
        print(f"log_turn_trace error (Trace {trace_id}):", repr(exc))

    return trace_id

def get_last_trace(chat_id: int) -> Optional[Dict[str, Any]]:
    """Retrieves the most recent turn trace for a chat."""
    try:
        with db.engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT trace_id, message, detected_connector, tools_called,
                           tool_results, guard_results, latency_ms, tokens, reply_path, is_suspect, suspect_reason, created_at
                    FROM turn_traces
                    WHERE chat_id = :cid
                    ORDER BY id DESC LIMIT 1
                """),
                {"cid": chat_id}
            ).fetchone()
            if row:
                return {
                    "trace_id": row[0],
                    "message": row[1],
                    "detected_connector": row[2],
                    "tools_called": row[3],
                    "tool_results": row[4],
                    "guard_results": row[5],
                    "latency_ms": row[6],
                    "tokens": row[7],
                    "reply_path": row[8],
                    "is_suspect": row[9],
                    "suspect_reason": row[10],
                    "created_at": str(row[11])
                }
    except Exception as exc:
        print("get_last_trace error:", repr(exc))
    return None
