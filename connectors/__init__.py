"""
PeculiarAI Connectors Package
Universal Connector Contract, Registry, and Implementations.
"""
from connectors.base import (
    Connector,
    ToolSpec,
    ToolResult,
    HealthResult,
    ErrorType,
)
from connectors.registry import (
    register_connector,
    get_connector,
    get_all_connectors,
    get_connector_by_tool,
    get_available_tools,
)

__all__ = [
    "Connector",
    "ToolSpec",
    "ToolResult",
    "HealthResult",
    "ErrorType",
    "register_connector",
    "get_connector",
    "get_all_connectors",
    "get_connector_by_tool",
    "get_available_tools",
]
