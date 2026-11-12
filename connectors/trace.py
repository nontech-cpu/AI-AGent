"""
PeculiarAI — Turn Trace & Observability Engine (Part 5 & Part 6)
Stores structured execution traces in DB with secret redaction and suspect-turn tracking for auto-regression promotion.
"""
import os
import re
import json
import uuid
import logging
from datetime import datetime
from typing import Dict, Any, List, Optional
from sqlalchemy import text
import db
from timing import IST
import core.timeutil as timeutil
from core.json_safe import safe_json_dumps

# Redaction patterns for secrets
SECRET_PATTERNS = [
    (re.compile(r"ghp_[a-zA-Z0-9]{15,}", re.I), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"github_pat_[a-zA-Z0-9_]{20,}", re.I), "[REDACTED_GH_TOKEN]"),
    (re.compile(r"(?:secret_|ntn_)[a-zA-Z0-9_]{10,}", re.I), "[REDACTED_NOTION_TOKEN]"),
    (re.compile(r"gsk_[a-zA-Z0-9_]{20,}", re.I), "[REDACTED_GROQ_KEY]"),
    (re.compile(r"AIza[0-9A-Za-z-_]{30,}", re.I), "[REDACTED_GOOGLE_KEY]"),
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
    """Initializes turn_traces table if not present and adds observability columns."""
    try:
        from sqlalchemy import inspect
        is_pg = db.engine.dialect.name == "postgresql"
        with db.engine.begin() as conn:
            if is_pg:
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
                cols = [
                    "provider TEXT",
                    "model TEXT",
                    "fallback_used BOOLEAN DEFAULT FALSE",
                    "fallback_reason TEXT",
                    "llm_calls INTEGER DEFAULT 0",
                    "prompt_tokens INTEGER DEFAULT 0",
                    "completion_tokens INTEGER DEFAULT 0",
                    "error_type TEXT",
                    "memory_status TEXT"
                ]
                for c in cols:
                    try:
                        conn.execute(text(f"ALTER TABLE turn_traces ADD COLUMN IF NOT EXISTS {c}"))
                    except Exception:
                        pass
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS turn_traces (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trace_id VARCHAR(64) UNIQUE NOT NULL,
                        chat_id INTEGER NOT NULL,
                        message TEXT,
                        detected_connector VARCHAR(64),
                        tools_called TEXT,
                        tool_results TEXT,
                        guard_results TEXT,
                        latency_ms INTEGER DEFAULT 0,
                        tokens INTEGER DEFAULT 0,
                        reply_path VARCHAR(64),
                        is_suspect BOOLEAN DEFAULT 0,
                        suspect_reason TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                try:
                    insp = inspect(conn)
                    existing = [col["name"] for col in insp.get_columns("turn_traces")]
                    cols_to_add = [
                        ("provider", "TEXT"),
                        ("model", "TEXT"),
                        ("fallback_used", "BOOLEAN DEFAULT 0"),
                        ("fallback_reason", "TEXT"),
                        ("llm_calls", "INTEGER DEFAULT 0"),
                        ("prompt_tokens", "INTEGER DEFAULT 0"),
                        ("completion_tokens", "INTEGER DEFAULT 0"),
                        ("error_type", "TEXT"),
                        ("memory_status", "TEXT")
                    ]
                    for col_name, col_type in cols_to_add:
                        if col_name not in existing:
                            conn.execute(text(f"ALTER TABLE turn_traces ADD COLUMN {col_name} {col_type}"))
                except Exception:
                    pass
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
    suspect_reason: Optional[str] = None,
    provider: Optional[str] = None,
    model: Optional[str] = None,
    fallback_used: bool = False,
    fallback_reason: Optional[str] = None,
    llm_calls: int = 1,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    error_type: Optional[str] = None,
    memory_status: str = "ok"
) -> str:
    """Logs a clean, redacted turn trace to DB with comprehensive telemetry."""
    trace_id = f"tr_{uuid.uuid4().hex[:12]}"
    clean_msg = redact_secrets(message)
    clean_tools = redact_secrets(tools_called)
    clean_results = redact_secrets(tool_results)
    clean_guards = redact_secrets(guard_results)

    try:
        is_pg = db.engine.dialect.name == "postgresql"
        with db.engine.begin() as conn:
            if is_pg:
                conn.execute(
                    text("""
                        INSERT INTO turn_traces (
                            trace_id, chat_id, message, detected_connector,
                            tools_called, tool_results, guard_results,
                            latency_ms, tokens, reply_path, is_suspect, suspect_reason, created_at,
                            provider, model, fallback_used, fallback_reason,
                            llm_calls, prompt_tokens, completion_tokens, error_type, memory_status
                        ) VALUES (
                            :tid, :cid, :msg, :conn,
                            CAST(:tc AS jsonb), CAST(:tr AS jsonb), CAST(:gr AS jsonb),
                            :lat, :tok, :rp, :susp, :sreason, :cat,
                            :prov, :mod, :fb_used, :fb_reason,
                            :llm_c, :p_tok, :c_tok, :err_t, :mem_s
                        )
                    """),
                    {
                        "tid": trace_id,
                        "cid": chat_id,
                        "msg": clean_msg,
                        "conn": detected_connector,
                        "tc": safe_json_dumps(clean_tools),
                        "tr": safe_json_dumps(clean_results),
                        "gr": safe_json_dumps(clean_guards),
                        "lat": latency_ms,
                        "tok": tokens,
                        "rp": reply_path,
                        "susp": is_suspect,
                        "sreason": suspect_reason,
                        "cat": timeutil.now(IST),
                        "prov": provider,
                        "mod": model,
                        "fb_used": fallback_used,
                        "fb_reason": fallback_reason,
                        "llm_c": llm_calls,
                        "p_tok": prompt_tokens,
                        "c_tok": completion_tokens,
                        "err_t": error_type,
                        "mem_s": memory_status
                    }
                )
            else:
                conn.execute(
                    text("""
                        INSERT INTO turn_traces (
                            trace_id, chat_id, message, detected_connector,
                            tools_called, tool_results, guard_results,
                            latency_ms, tokens, reply_path, is_suspect, suspect_reason, created_at,
                            provider, model, fallback_used, fallback_reason,
                            llm_calls, prompt_tokens, completion_tokens, error_type, memory_status
                        ) VALUES (
                            :tid, :cid, :msg, :conn,
                            :tc, :tr, :gr,
                            :lat, :tok, :rp, :susp, :sreason, :cat,
                            :prov, :mod, :fb_used, :fb_reason,
                            :llm_c, :p_tok, :c_tok, :err_t, :mem_s
                        )
                    """),
                    {
                        "tid": trace_id,
                        "cid": chat_id,
                        "msg": clean_msg,
                        "conn": detected_connector,
                        "tc": safe_json_dumps(clean_tools),
                        "tr": safe_json_dumps(clean_results),
                        "gr": safe_json_dumps(clean_guards),
                        "lat": latency_ms,
                        "tok": tokens,
                        "rp": reply_path,
                        "susp": is_suspect,
                        "sreason": suspect_reason,
                        "cat": timeutil.now(IST).strftime("%Y-%m-%d %H:%M:%S"),
                        "prov": provider,
                        "mod": model,
                        "fb_used": 1 if fallback_used else 0,
                        "fb_reason": fallback_reason,
                        "llm_c": llm_calls,
                        "p_tok": prompt_tokens,
                        "c_tok": completion_tokens,
                        "err_t": error_type,
                        "mem_s": memory_status
                    }
                )
    except Exception as exc:
        dead_letter_path = os.getenv("DEAD_LETTER_TRACE_PATH", "logs/dead_letter_traces.jsonl")
        logger = logging.getLogger("trace_health")
        logger.warning(f"HEALTH WARNING: Failed to insert turn_trace {trace_id} into DB: {repr(exc)}. Writing to dead-letter file.")
        try:
            os.makedirs(os.path.dirname(os.path.abspath(dead_letter_path)), exist_ok=True)
            dead_letter_entry = {
                "trace_id": trace_id,
                "chat_id": chat_id,
                "timestamp": timeutil.now(IST).isoformat(),
                "error": repr(exc),
                "message": clean_msg,
                "reply_path": reply_path,
                "tools_called": clean_tools,
                "tool_results": clean_results,
                "provider": provider,
                "model": model,
                "llm_calls": llm_calls,
                "error_type": error_type,
            }
            with open(dead_letter_path, "a", encoding="utf-8") as f_dl:
                f_dl.write(safe_json_dumps(dead_letter_entry) + "\n")
        except Exception as dl_err:
            logger.error(f"Failed writing to dead-letter file: {dl_err}")

    # Structured console log line for Render log aggregation without secrets
    try:
        turn_logger = logging.getLogger("turn_trace")
        structured_payload = {
            "event": "turn_completed",
            "trace_id": trace_id,
            "user_id": chat_id,
            "tools": clean_tools,
            "provider": provider,
            "model": model,
            "fallback_used": fallback_used,
            "fallback_reason": fallback_reason,
            "latency_ms": latency_ms,
            "tokens": tokens,
            "error_type": error_type,
            "memory_status": memory_status
        }
        turn_logger.info(safe_json_dumps(structured_payload))
    except Exception:
        pass

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
