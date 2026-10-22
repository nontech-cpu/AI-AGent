"""
PeculiarAI Interval Schedule Engine (Phase 4)
Interval-aware temporal mathematics:
- Durations & activity type heuristics (movies 180m, dinner 90m, gym 60m)
- Travel & virtual buffers (10-30 min windows)
- Overlap detection with clean Asia/Kolkata (IST) temporal normalization
- Free slot suggestion
"""
from schedule.intervals import (
    TimeInterval,
    parse_activity_duration,
    check_interval_availability,
    get_unified_daily_schedule,
    find_free_slots
)

__all__ = [
    "TimeInterval",
    "parse_activity_duration",
    "check_interval_availability",
    "get_unified_daily_schedule",
    "find_free_slots"
]
