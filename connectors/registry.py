"""
PeculiarAI — Unified Connector Registry
Registers all active connectors and dynamically exposes tools based on user auth state and message relevance.
"""
from typing import Dict, List, Optional
from connectors.base import Connector, ToolSpec
from connectors.github import GitHubConnector
from connectors.gmail import GmailConnector
from connectors.calendar import CalendarConnector
from connectors.drive import DriveConnector
from connectors.notion import NotionConnector

_REGISTRY: Dict[str, Connector] = {}

def register_connector(connector: Connector):
    _REGISTRY[connector.name.lower()] = connector

def get_connector(name: str) -> Optional[Connector]:
    return _REGISTRY.get(name.lower())

def get_all_connectors() -> List[Connector]:
    return list(_REGISTRY.values())

def get_connector_by_tool(tool_name: str) -> Optional[Connector]:
    for conn in _REGISTRY.values():
        for t in conn.tools():
            if t.name == tool_name:
                return conn
    return None

def get_available_tools(user_or_chat_id: int, message: str = "") -> List[ToolSpec]:
    """
    Returns:
    1. Tools for all currently connected/partially-connected connectors.
    2. PLUS tools for any connector whose aliases match keywords in the current message
       (allowing the bot to cleanly inform the user to connect without prompt bloat).
    """
    msg_low = message.lower()
    selected_tools: List[ToolSpec] = []
    seen_names = set()

    for conn in _REGISTRY.values():
        state = conn.auth_state(user_or_chat_id)
        is_connected = state in ["connected", "partial"]
        is_mentioned = any(alias in msg_low for alias in conn.aliases)

        if is_connected or is_mentioned:
            for t in conn.tools():
                if t.name not in seen_names:
                    seen_names.add(t.name)
                    selected_tools.append(t)

    return selected_tools

# Initialize default connectors
register_connector(GitHubConnector())
register_connector(GmailConnector())
register_connector(CalendarConnector())
register_connector(DriveConnector())
register_connector(NotionConnector())
