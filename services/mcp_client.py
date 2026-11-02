"""
PeculiarAI Model Context Protocol (MCP) Client
Supports discovery and execution of remote MCP tools via standard JSON-RPC 2.0.
"""
import os
import json
import logging
from typing import Dict, Any, List, Optional
import httpx

logger = logging.getLogger("peculiar_mcp")

class MCPClient:
    """Async MCP client interacting with remote Model Context Protocol servers."""

    def __init__(self, server_url: str, auth_token: Optional[str] = None, timeout: float = 15.0):
        self.server_url = server_url.rstrip("/")
        self.auth_token = auth_token
        self.timeout = timeout

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json"
        }
        if self.auth_token:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers

    async def list_tools(self) -> List[Dict[str, Any]]:
        """Queries the MCP server for available tools via tools/list."""
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/list",
            "params": {}
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.server_url, json=payload, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    result = data.get("result", {})
                    tools = result.get("tools", [])
                    return tools
                else:
                    logger.warning(f"MCP list_tools returned status {resp.status_code}: {resp.text[:200]}")
        except Exception as exc:
            logger.error(f"MCP list_tools error connecting to {self.server_url}: {exc}")

        return []

    async def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """Invokes an MCP tool via tools/call."""
        payload = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": name,
                "arguments": arguments
            }
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.server_url, json=payload, headers=self._headers())
                if resp.status_code == 200:
                    data = resp.json()
                    if "error" in data:
                        return {"ok": False, "error": data["error"]}
                    return {"ok": True, "data": data.get("result", {})}
                else:
                    return {
                        "ok": False,
                        "error": f"MCP server error ({resp.status_code}): {resp.text[:300]}"
                    }
        except Exception as exc:
            logger.error(f"MCP call_tool error for {name}: {exc}")
            return {"ok": False, "error": str(exc)}


async def discover_and_cache_mcp_tools(chat_id: int, server_name: str, server_url: str, auth_token: Optional[str] = None) -> List[Dict[str, Any]]:
    """Discovers tools from an MCP server and updates user_mcp_servers cache."""
    import db
    client = MCPClient(server_url, auth_token=auth_token)
    tools = await client.list_tools()
    db.save_user_mcp_server(
        chat_id=chat_id,
        server_name=server_name,
        server_url=server_url,
        auth_token=auth_token,
        tools_cache=tools
    )
    return tools
