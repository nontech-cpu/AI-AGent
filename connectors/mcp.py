"""
PeculiarAI — Model Context Protocol (MCP) Dynamic Connector
Exposes tools from user-configured remote MCP servers to the agent reasoning loop.
"""
from typing import Dict, Any, List, Optional, Literal
from connectors.base import Connector, ToolSpec, ToolResult, HealthResult, ErrorType
import db
from services.mcp_client import MCPClient

class MCPConnector(Connector):
    name = "mcp"
    aliases = ["mcp", "protocol", "server", "custom tool", "plugin", "remote tool"]
    display_name = "Model Context Protocol (MCP)"

    def __init__(self, is_mock: bool = False, forced_state: Optional[str] = None):
        self.is_mock = is_mock
        self.forced_state = forced_state

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        if self.forced_state:
            return self.forced_state
        if self.is_mock:
            return "connected"
        servers = db.get_user_mcp_servers(user_or_chat_id)
        return "connected" if servers else "not_connected"

    def connect_instructions(self, user_or_chat_id: int) -> str:
        return (
            "Apna custom MCP server connect karne ke liye REST endpoint use karein:\n"
            "POST /api/connectors/mcp with body: {\"server_name\": \"my_server\", \"server_url\": \"https://...\", \"auth_token\": \"...\"}"
        )

    def tools(self) -> List[ToolSpec]:
        """Returns standard MCP management tool specs plus dynamically known tools."""
        return [
            ToolSpec(
                name="list_mcp_servers",
                description="List all active user-connected Model Context Protocol (MCP) servers.",
                parameters={},
                kind="read"
            ),
            ToolSpec(
                name="call_mcp_tool",
                description="Invoke a tool on a connected remote Model Context Protocol (MCP) server.",
                parameters={
                    "server_name": {"type": "string", "description": "The name of the connected MCP server."},
                    "tool_name": {"type": "string", "description": "The name of the tool to execute on the MCP server."},
                    "arguments": {"type": "object", "description": "JSON arguments matching the tool's schema."}
                },
                kind="write"
            )
        ]

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        """Executes an MCP operation."""
        if self.forced_state == "not_connected":
            return ToolResult(
                ok=False,
                error={"type": ErrorType.NOT_CONNECTED.value, "message": "MCP server is not connected."}
            )
        if self.forced_state == "expired":
            return ToolResult(
                ok=False,
                error={"type": ErrorType.AUTH_EXPIRED.value, "message": "MCP server authorization expired."}
            )

        if self.is_mock:
            return ToolResult(ok=True, data={"mock_result": "MCP action executed successfully"})

        if tool_name == "list_mcp_servers":
            servers = db.get_user_mcp_servers(user_or_chat_id)
            return ToolResult(ok=True, data={"servers": servers, "count": len(servers)})

        if tool_name == "call_mcp_tool":
            server_name = args.get("server_name", "").strip().lower()
            remote_tool = args.get("tool_name", "").strip()
            tool_args = args.get("arguments", {})

            servers = db.get_user_mcp_servers(user_or_chat_id)
            target = next((s for s in servers if s.get("server_name", "").lower() == server_name), None)
            if not target:
                return ToolResult(
                    ok=False,
                    error={"type": ErrorType.NOT_FOUND, "message": f"MCP server '{server_name}' is not registered."}
                )

            client = MCPClient(target["server_url"], auth_token=target.get("auth_token"))
            res = await client.call_tool(remote_tool, tool_args)
            if res.get("ok"):
                return ToolResult(ok=True, data=res.get("data"))
            else:
                return ToolResult(
                    ok=False,
                    error={"type": ErrorType.API_ERROR, "message": str(res.get("error", "Remote MCP invocation failed"))}
                )

        return ToolResult(
            ok=False,
            error={"type": ErrorType.NOT_FOUND, "message": f"Unknown MCP tool '{tool_name}'"}
        )

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        if self.is_mock:
            return HealthResult(ok=True, connected=True, latency_ms=5, message="Mock MCP Connector healthy")
        servers = db.get_user_mcp_servers(user_or_chat_id)
        if not servers:
            return HealthResult(ok=True, connected=False, latency_ms=0, message="No MCP servers configured.")
        return HealthResult(ok=True, connected=True, latency_ms=10, message=f"{len(servers)} active MCP servers registered.")

    def mock(self) -> "Connector":
        return MCPConnector(is_mock=True)

    def fixtures(self) -> Dict[str, Any]:
        return {
            "servers": [
                {"server_name": "weather_mcp", "server_url": "https://mcp.weather.internal", "is_active": True}
            ]
        }
