"""
PeculiarAI State Management Package (Phase 5)
Provides durable multi-turn conversation and pending action state.
"""
from state.pending import (
    ensure_pending_table,
    save_pending_action,
    get_pending_action,
    complete_pending_action,
    cancel_pending_action,
    classify_confirmation_intent,
    execute_pending_action,
    PersistentPendingDict,
    get_pending_actions_proxy,
)

__all__ = [
    "ensure_pending_table",
    "save_pending_action",
    "get_pending_action",
    "complete_pending_action",
    "cancel_pending_action",
    "classify_confirmation_intent",
    "execute_pending_action",
    "PersistentPendingDict",
    "get_pending_actions_proxy",
]
