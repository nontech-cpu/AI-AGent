"""
PeculiarAI - Webhook Rate Limiter & Token/Replay Expiry Guard
Thread-safe sliding window rate limiter and update freshness validator for external webhooks.
"""

import time
import threading
from typing import Dict, List, Tuple, Optional
from fastapi import Request, HTTPException


class WebhookRateLimiter:
    """
    In-memory thread-safe sliding window rate limiter.
    Ensures that incoming webhook requests per client IP or chat ID stay within defined thresholds.
    """

    def __init__(self, max_requests: int = 60, window_seconds: int = 60):
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._history: Dict[str, List[float]] = {}
        self._lock = threading.Lock()

    def is_allowed(self, key: str) -> Tuple[bool, int]:
        """
        Checks whether the given key is allowed to make a request.
        Returns (is_allowed, retry_after_seconds).
        """
        now = time.time()
        cutoff = now - self.window_seconds

        with self._lock:
            timestamps = self._history.get(key, [])
            # Prune timestamps older than window
            fresh_timestamps = [t for t in timestamps if t > cutoff]

            if len(fresh_timestamps) >= self.max_requests:
                # Oldest timestamp that will roll off
                oldest = fresh_timestamps[0]
                retry_after = max(1, int(oldest + self.window_seconds - now))
                self._history[key] = fresh_timestamps
                return False, retry_after

            fresh_timestamps.append(now)
            self._history[key] = fresh_timestamps
            return True, 0

    def check_request(self, request: Request, key_override: Optional[str] = None):
        """
        Enforces rate limiting on a FastAPI Request. Raises HTTPException(429) if exceeded.
        """
        client_ip = key_override or (request.client.host if request.client else "unknown_client")
        allowed, retry_after = self.is_allowed(client_ip)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail=f"Rate limit exceeded. Maximum {self.max_requests} requests per {self.window_seconds}s.",
                headers={"Retry-After": str(retry_after)}
            )

    def reset(self):
        """Clears all tracking history (primarily for tests)."""
        with self._lock:
            self._history.clear()


# Default singleton instance: 60 requests per 60s
webhook_limiter = WebhookRateLimiter(max_requests=60, window_seconds=60)


def validate_webhook_freshness(update_dict: dict, max_age_seconds: int = 300) -> bool:
    """
    Validates that a Telegram update was created recently and is not a replayed/stale packet.
    Telegram update message timestamps are unix epoch seconds.
    """
    if not isinstance(update_dict, dict):
        return True

    msg = update_dict.get("message") or update_dict.get("edited_message") or update_dict.get("callback_query", {}).get("message")
    if not isinstance(msg, dict):
        return True

    msg_date = msg.get("date")
    if msg_date is None or not isinstance(msg_date, (int, float)):
        return True

    now = time.time()
    # If the message timestamp is older than max_age_seconds, it is expired/stale
    if (now - msg_date) > max_age_seconds:
        return False

    return True
