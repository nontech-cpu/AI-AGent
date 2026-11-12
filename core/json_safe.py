"""
Shared JSON-safe normalization and serialization helpers.
Eliminates TypeError crashes when Python domain or database objects
(datetime, date, time, Decimal, sets, models) cross tool, connector,
trace, idempotency, or LLM serialization boundaries.
"""
import json
from datetime import datetime, date, time as dtime
from decimal import Decimal
from typing import Any


def _normalize_scalar(obj: Any) -> Any:
    if isinstance(obj, (datetime, date, dtime)):
        return obj.isoformat()
    if isinstance(obj, Decimal):
        if obj.is_finite():
            return int(obj) if obj == obj.to_integral_value() else float(obj)
        return str(obj)
    if hasattr(obj, "isoformat") and callable(obj.isoformat):
        return obj.isoformat()
    if hasattr(obj, "model_dump") and callable(obj.model_dump):
        return to_json_safe(obj.model_dump())
    return str(obj)


def to_json_safe(obj: Any) -> Any:
    """Recursively convert Python objects into JSON-serializable primitives."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): to_json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_json_safe(item) for item in obj]
    return _normalize_scalar(obj)


def _json_default(obj: Any) -> Any:
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [to_json_safe(item) for item in obj]
    return _normalize_scalar(obj)


def safe_json_dumps(obj: Any, **kwargs: Any) -> str:
    """Serialize any payload to JSON safely without failing on non-primitive Python types."""
    if "default" not in kwargs:
        kwargs["default"] = _json_default
    return json.dumps(obj, **kwargs)

