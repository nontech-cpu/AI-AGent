"""
PeculiarAI — GitHub Connector (Universal Connector Contract)
Wraps GitHub API integrations with deterministic mock support and standard error taxonomy.
"""
import os
import json
import time
import httpx
from typing import Dict, Any, List, Optional, Literal

import db
import integrations
from connectors.base import Connector, ToolSpec, ToolResult, HealthResult, ErrorType

GITHUB_FIXTURES = {
    "username": "nontech-cpu",
    "repositories": [
        {"name": "PeculliarAI", "description": "Personal AI Assistant and Second Brain", "stars": 12, "url": "https://github.com/nontech-cpu/PeculliarAI"},
        {"name": "Agentic_AI-Chatbot", "description": "Multi-agent chatbot system", "stars": 5, "url": "https://github.com/nontech-cpu/Agentic_AI-Chatbot"},
        {"name": "v_imp", "description": "Important reference scripts", "stars": 2, "url": "https://github.com/nontech-cpu/v_imp"},
        {"name": "spamdetection", "description": "NLP spam detection classifier", "stars": 8, "url": "https://github.com/nontech-cpu/spamdetection"}
    ],
    "issues": [
        {"id": "iss_101", "number": 1, "title": "Fix Groq 8k token limit", "state": "open", "url": "https://github.com/nontech-cpu/PeculliarAI/issues/1"},
        {"id": "iss_102", "number": 2, "title": "Add universal connector test matrix", "state": "open", "url": "https://github.com/nontech-cpu/PeculliarAI/issues/2"}
    ]
}

class GitHubConnector(Connector):
    name = "github"
    aliases = ["github", "gh", "git", "repo", "repos", "repository", "repositories", "issue", "issues", "pr", "commit"]
    display_name = "GitHub"

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        row = db.get_user_integration(user_or_chat_id, "github")
        if not row:
            return "not_connected"
        token = row.get("api_key")
        config = {}
        try:
            config = json.loads(row.get("config_data") or "{}")
        except Exception:
            pass
        if token and token != "public_access" and token.startswith(("ghp_", "github_pat_")):
            return "connected"
        if config.get("username"):
            return "partial"
        return "not_connected"

    def connect_instructions(self, user_or_chat_id: int) -> str:
        return "Apna GitHub account connect karne ke liye profile link (jaise `https://github.com/username`) ya personal access token bhej dijiye."

    def tools(self) -> List[ToolSpec]:
        return [
            ToolSpec(
                name="list_github_repos",
                description="List public or authenticated user's GitHub repositories with names, URLs, and update details.",
                parameters={
                    "type": "object",
                    "properties": {
                        "username": {
                            "type": "string",
                            "description": "Optional GitHub username or profile URL (e.g. 'nontech-cpu'). If omitted, saved user context is used."
                        }
                    }
                },
                kind="read"
            ),
            ToolSpec(
                name="list_github_issues",
                description="List open issues for a specific GitHub repository.",
                parameters={
                    "type": "object",
                    "properties": {
                        "repo": {
                            "type": "string",
                            "description": "Repository name in 'owner/repo' or 'repo' format."
                        }
                    },
                    "required": ["repo"]
                },
                kind="read"
            )
        ]

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        state = self.auth_state(user_or_chat_id)
        token_row = db.get_user_integration(user_or_chat_id, "github")
        token = token_row.get("api_key") if token_row else None
        saved_username = None
        if token_row:
            try:
                cfg = json.loads(token_row.get("config_data") or "{}")
                saved_username = cfg.get("username")
            except Exception:
                pass

        if tool_name == "list_github_repos":
            req_username = args.get("username") or saved_username
            if not req_username and not token:
                return ToolResult(
                    ok=False,
                    error={
                        "type": ErrorType.NOT_CONNECTED.value,
                        "message": "GitHub is not connected. Provide a username or token to view repositories.",
                        "retryable": False
                    }
                )

            sys_token = token if (token and token != "public_access") else os.getenv("GITHUB_TOKEN")
            ok, repos_data, resolved_user = await integrations.list_github_repos(token=sys_token, username=req_username)
            if not ok and req_username:
                ok, repos_data, resolved_user = await integrations.list_github_repos(token=None, username=req_username)

            if not ok:
                return ToolResult(
                    ok=False,
                    error={
                        "type": ErrorType.API_ERROR.value,
                        "message": "Could not fetch repositories from GitHub.",
                        "retryable": True
                    }
                )

            if not repos_data:
                return ToolResult(
                    ok=True,
                    data={"username": resolved_user, "repositories": [], "count": 0},
                    error={"type": ErrorType.EMPTY_RESULT.value, "message": "No repositories found.", "retryable": False}
                )

            names = [r["name"] for r in repos_data if isinstance(r, dict)]
            urls = [r.get("html_url", "") for r in repos_data if isinstance(r, dict) and r.get("html_url")]
            return ToolResult(
                ok=True,
                data={"username": resolved_user or req_username, "repositories": names, "count": len(names)},
                urls=urls[:15]
            )

        elif tool_name == "list_github_issues":
            repo_arg = args.get("repo", "")
            if not repo_arg:
                return ToolResult(
                    ok=False,
                    error={
                        "type": ErrorType.INVALID_ARGS.value,
                        "message": "Missing 'repo' argument.",
                        "retryable": False
                    }
                )

            repo_full = repo_arg
            if "/" not in repo_arg and saved_username:
                repo_full = f"{saved_username}/{repo_arg}"

            sys_token = token if (token and token != "public_access") else os.getenv("GITHUB_TOKEN")
            ok, issues_data = await integrations.list_github_issues(token=sys_token, repo=repo_full)
            if not ok:
                return ToolResult(
                    ok=False,
                    error={
                        "type": ErrorType.API_ERROR.value,
                        "message": f"Could not fetch issues for '{repo_full}'.",
                        "retryable": True
                    }
                )

            if not issues_data:
                return ToolResult(
                    ok=True,
                    data={"repo": repo_full, "issues": [], "count": 0},
                    error={"type": ErrorType.EMPTY_RESULT.value, "message": f"No open issues found for '{repo_full}'.", "retryable": False}
                )

            issue_list = [
                {
                    "number": iss.get("number"),
                    "title": iss.get("title"),
                    "url": iss.get("html_url")
                }
                for iss in issues_data if isinstance(iss, dict)
            ]
            ids = [str(iss.get("number")) for iss in issue_list if iss.get("number")]
            urls = [iss.get("url") for iss in issue_list if iss.get("url")]
            return ToolResult(
                ok=True,
                data={"repo": repo_full, "issues": issue_list, "count": len(issue_list)},
                ids=ids,
                urls=urls
            )

        return ToolResult(
            ok=False,
            error={
                "type": ErrorType.INVALID_ARGS.value,
                "message": f"Unknown tool '{tool_name}' for GitHub connector.",
                "retryable": False
            }
        )

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                res = await client.get("https://api.github.com/zen", headers={"User-Agent": "PeculiarAI"})
                latency = int((time.time() - t0) * 1000)
                state = self.auth_state(user_or_chat_id)
                return HealthResult(
                    ok=(res.status_code == 200),
                    connected=(state in ["connected", "partial"]),
                    latency_ms=latency,
                    message="GitHub API reachable.",
                    details={"auth_state": state, "zen": res.text.strip()}
                )
        except Exception as exc:
            return HealthResult(
                ok=False,
                connected=False,
                latency_ms=int((time.time() - t0) * 1000),
                message=f"GitHub health check failed: {str(exc)}"
            )

    def fixtures(self) -> Dict[str, Any]:
        return GITHUB_FIXTURES

    def mock(self) -> "Connector":
        return MockGitHubConnector()


class MockGitHubConnector(GitHubConnector):
    """Deterministic Mock for Auto-Generated Testing."""
    def __init__(self, forced_state: Literal["connected", "not_connected", "expired", "partial"] = "connected"):
        self._forced_state = forced_state

    def auth_state(self, user_or_chat_id: int) -> Literal["connected", "not_connected", "expired", "partial"]:
        return self._forced_state

    async def execute(self, user_or_chat_id: int, tool_name: str, args: Dict[str, Any]) -> ToolResult:
        if self._forced_state == "not_connected":
            return ToolResult(
                ok=False,
                error={
                    "type": ErrorType.NOT_CONNECTED.value,
                    "message": "GitHub is not connected.",
                    "retryable": False
                }
            )
        if self._forced_state == "expired":
            return ToolResult(
                ok=False,
                error={
                    "type": ErrorType.AUTH_EXPIRED.value,
                    "message": "GitHub token has expired.",
                    "retryable": False
                }
            )

        if tool_name == "list_github_repos":
            names = [r["name"] for r in GITHUB_FIXTURES["repositories"]]
            urls = [r["url"] for r in GITHUB_FIXTURES["repositories"]]
            return ToolResult(
                ok=True,
                data={"username": GITHUB_FIXTURES["username"], "repositories": names, "count": len(names)},
                urls=urls
            )

        elif tool_name == "list_github_issues":
            issues = GITHUB_FIXTURES["issues"]
            return ToolResult(
                ok=True,
                data={"repo": args.get("repo", "nontech-cpu/PeculliarAI"), "issues": issues, "count": len(issues)},
                ids=[str(i["number"]) for i in issues],
                urls=[i["url"] for i in issues]
            )

        return ToolResult(ok=False, error={"type": ErrorType.INVALID_ARGS.value, "message": "Unknown mock tool"})

    async def health_check(self, user_or_chat_id: int) -> HealthResult:
        return HealthResult(ok=True, connected=(self._forced_state in ["connected", "partial"]), latency_ms=5, message="Mock GitHub OK")
