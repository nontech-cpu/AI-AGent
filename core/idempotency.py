"""
PeculiarAI — Write Action Idempotency Shield (Layer 3 Glass Tower Standard)
Prevents duplicate writes (reminders, calendar events, emails, contacts) caused by:
- User double-tapping or sending identical commands on Telegram/WhatsApp
- Network drop / Webhook duplicate delivery
- LLM tool-calling loop retry on transient error
Provides a 45-second sliding window deduplication cache per user.
"""
import time
import json
import hashlib
import logging
from typing import Dict, Any, Optional, Tuple
from core.json_safe import safe_json_dumps

logger = logging.getLogger("peculiar_idempotency")

# Cache: {idempotency_key: (timestamp, result_dict)}
_IDEMPOTENCY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_IDEMPOTENCY_TTL_SECONDS = 45.0
_MAX_CACHE_ENTRIES = 500

IDEMPOTENT_WRITE_TOOLS = {
    "create_reminder",
    "create_meeting",
    "send_email",
    "save_contact",
    "save_note_or_fact",
    "update_contact_commitment",
    "request_master_privacy_wipe",
}

def compute_idempotency_key(chat_id: int, tool_name: str, arguments: dict) -> str:
    """Computes a deterministic SHA256 idempotency key for a write action."""
    try:
        # Normalize arguments by sorting keys and converting values to clean json strings
        clean_args = {k: v for k, v in sorted(arguments.items()) if v is not None}
        args_str = safe_json_dumps(clean_args, sort_keys=True, ensure_ascii=False)
    except Exception:
        args_str = str(sorted(arguments.items()))
    payload = f"{chat_id}:{tool_name}:{args_str}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()

def check_idempotency(chat_id: int, tool_name: str, arguments: dict) -> Optional[Dict[str, Any]]:
    """
    Checks if an identical write action succeeded within the sliding window.
    Returns the cached result with 'idempotent_replay': True if found, else None.
    """
    if tool_name not in IDEMPOTENT_WRITE_TOOLS:
        return None

    key = compute_idempotency_key(chat_id, tool_name, arguments)
    entry = _IDEMPOTENCY_CACHE.get(key)
    if entry:
        ts, result = entry
        if (time.time() - ts) < _IDEMPOTENCY_TTL_SECONDS:
            logger.info(f"Idempotency hit: replaying result for {tool_name} (chat_id={chat_id})")
            replay_res = dict(result)
            replay_res["idempotent_replay"] = True
            return replay_res
        else:
            _IDEMPOTENCY_CACHE.pop(key, None)
    return None

def record_idempotency(chat_id: int, tool_name: str, arguments: dict, result: dict):
    """
    Records a successful write action result in the sliding window cache.
    Only successful or pending/confirmation results are recorded (errors are never cached).
    """
    if tool_name not in IDEMPOTENT_WRITE_TOOLS or not isinstance(result, dict):
        return

    # Do not cache error outcomes (allow genuine retries)
    if result.get("status") == "error":
        return

    now = time.time()
    # Housekeeping: prune old entries if cache grows
    if len(_IDEMPOTENCY_CACHE) > _MAX_CACHE_ENTRIES:
        expired_keys = [k for k, (ts, _) in _IDEMPOTENCY_CACHE.items() if (now - ts) > _IDEMPOTENCY_TTL_SECONDS]
        for k in expired_keys:
            _IDEMPOTENCY_CACHE.pop(k, None)

    key = compute_idempotency_key(chat_id, tool_name, arguments)
    _IDEMPOTENCY_CACHE[key] = (now, dict(result))

def clear_idempotency_cache():
    """Testing helper to reset idempotency state."""
    _IDEMPOTENCY_CACHE.clear()
