"""
PeculiarAI — Unified Connector Architecture (Part 1: The One Connector Contract)
Defines the universal contract, error taxonomy, and result models for all external integrations.
"""
from abc import ABC, abstractmethod
from enum import Enum
from typing import Dict, Any, List, Optional, Literal
from pydantic import BaseModel, Field

class ErrorType(str, Enum):
    NOT_CONNECTED = "not_connected"
    AUTH_EXPIRED = "auth_expired"
    PERMISSION_DENIED = "permission_denied"
    RATE_LIMITED = "rate_limited"
    NOT_FOUND = "not_found"
    INVALID_ARGS = "invalid_args"
    API_ERROR = "api_error"
    TIMEOUT = "timeout"
    EMPTY_RESULT = "empty_result"

class ToolSpec(BaseModel):
    name: str
    description: str
    parameters: Dict[str, Any]
    kind: Literal["read", "write", "destructive"] = "read"
    needs_confirmation: bool = False

class ToolResult(BaseModel):
    ok: bool
    data: Any = None
    error: Optional[Dict[str, Any]] = None  # {type: ErrorType, message: str, retryable: bool}
    ids: List[str] = Field(default_factory=list)
    urls: List[str] = Field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return self.model_dump()

class HealthResult(BaseModel):
    ok: bool
    connected: bool
    latency_ms: int = 0
    message: str = ""
    details: Optional[Dict[str, Any]] = None

class Connector(ABC):
    """
    Abstract Universal Connector contract implemented by GitHub, Gmail, Calendar, Drive, Notion, etc.
    """
    name: str
    aliases: List[str]
    display_name: str

    @abstractmethod
    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        """
        Returns connection state:
        - 'connected': fully authenticated with valid credentials.
        - 'not_connected': no tokens or configurations found.
        - 'expired': token exists but is expired / invalid.
        - 'partial': public profile / username configured without full write token.
        """
        pass

    @abstractmethod
    def connect_instructions(self, user_or_chat_id: int) -> str:
        """Returns direct link or actionable guidance to authenticate/connect."""
        pass

    @abstractmethod
    def tools(self) -> List[ToolSpec]:
        """Returns the list of tool definitions this connector exposes."""
        pass

    @abstractmethod
    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        """Executes a tool call through the universal execution contract."""
        pass

    @abstractmethod
    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        """Performs a lightweight real health check."""
        pass

    @abstractmethod
    def mock(self) -> "Connector":
        """Returns a deterministic fake connector backed by fixtures() for unit/scenario testing."""
        pass

    @abstractmethod
    def fixtures(self) -> Dict[str, Any]:
        """Returns static sample fixture data returned by mock()."""
        pass
