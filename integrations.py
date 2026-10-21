"""
PeculiarAI — Third-Party Integrations Engine (Notion & GitHub)
Handles token validation, workspace searching, and executing actions like:
- Creating notes, meeting summaries, and task pages in Notion.
- Querying issues, creating issues, and checking pull requests in GitHub.
"""
import os
import json
import httpx
from typing import Optional, Dict, Any, Tuple


# ==============================================================================
# NOTION INTEGRATION
# ==============================================================================

async def validate_notion_token(token: str) -> Tuple[bool, Optional[str]]:
    """
    Validates a Notion internal integration token (secret_...) against Notion API.
    Returns (is_valid, bot_name_or_workspace_name).
    """
    clean_token = token.strip()
    if not clean_token.startswith("secret_"):
        return False, "Notion integration token usually starts with 'secret_'"

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                "https://api.notion.com/v1/users/me",
                headers={
                    "Authorization": f"Bearer {clean_token}",
                    "Notion-Version": "2022-06-28"
                }
            )
            if res.status_code == 200:
                data = res.json()
                name = data.get("name") or data.get("bot", {}).get("owner", {}).get("workspace_name") or "Notion Workspace"
                return True, name
            return False, f"Notion API error: {res.status_code}"
    except Exception as exc:
        return False, str(exc)


async def create_notion_page(token: str, title: str, content: str, parent_id: Optional[str] = None) -> Tuple[bool, str, Optional[str]]:
    """
    Creates a new page with content blocks in the user's Notion workspace.
    If parent_id is not provided, finds the first shared page or database in the workspace.
    Returns (success, message, page_url).
    """
    clean_token = token.strip()
    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Step 1: If no parent_id, search for accessible parent pages or databases
        target_parent = None
        target_type = None

        if parent_id:
            target_parent = parent_id.replace("-", "")
            target_type = "page_id"
        else:
            search_res = await client.post(
                "https://api.notion.com/v1/search",
                headers=headers,
                json={"page_size": 5}
            )
            if search_res.status_code != 200:
                return False, f"Could not search Notion workspace (Status {search_res.status_code}). Ensure pages are shared with the integration.", None

            results = search_res.json().get("results", [])
            if not results:
                return False, "Notion me koi shared page nahi mila. Kripya Notion mein apne kisi page ke '...' ya 'Share' menu me jakar integration ko invite/access karein.", None

            first_obj = results[0]
            if first_obj.get("object") == "database":
                target_parent = first_obj.get("id")
                target_type = "database_id"
            else:
                target_parent = first_obj.get("id")
                target_type = "page_id"

        # Step 2: Build child blocks from content
        lines = [line.strip() for line in content.split("\n") if line.strip()]
        children_blocks = []
        for line in lines[:30]:  # Cap blocks
            if line.startswith("- ") or line.startswith("* "):
                children_blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {
                        "rich_text": [{"type": "text", "text": {"content": line[2:].strip()[:1800]}}]
                    }
                })
            else:
                children_blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [{"type": "text", "text": {"content": line[:1800]}}]
                    }
                })

        if not children_blocks:
            children_blocks.append({
                "object": "block",
                "type": "paragraph",
                "paragraph": {
                    "rich_text": [{"type": "text", "text": {"content": content[:1800]}}]
                }
            })

        # Step 3: Payload structure depends on parent type
        clean_title = title.strip() or "PeculiarAI Note"
        if target_type == "database_id":
            payload = {
                "parent": {"database_id": target_parent},
                "properties": {
                    "Name": {
                        "title": [{"text": {"content": clean_title[:200]}}]
                    }
                },
                "children": children_blocks
            }
        else:
            payload = {
                "parent": {"page_id": target_parent},
                "properties": {
                    "title": [{"text": {"content": clean_title[:200]}}]
                },
                "children": children_blocks
            }

        create_res = await client.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
        if create_res.status_code in (200, 201):
            created_data = create_res.json()
            page_url = created_data.get("url")
            return True, f"Page '{clean_title}' successfully created in Notion!", page_url
        else:
            err_detail = create_res.text
            try:
                err_detail = create_res.json().get("message", err_detail)
            except Exception:
                pass
            return False, f"Notion error ({create_res.status_code}): {err_detail}", None


# ==============================================================================
# GITHUB INTEGRATION
# ==============================================================================

async def validate_github_token(token: str) -> Tuple[bool, Optional[str]]:
    """
    Validates a GitHub Personal Access Token (ghp_... or github_pat_...).
    Returns (is_valid, username_or_login).
    """
    clean_token = token.strip()
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            res = await client.get(
                "https://api.github.com/user",
                headers={
                    "Authorization": f"Bearer {clean_token}",
                    "User-Agent": "PeculiarAI",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            if res.status_code == 200:
                user_info = res.json()
                login = user_info.get("login") or user_info.get("name") or "GitHub User"
                return True, login
            return False, f"GitHub authentication failed (Status {res.status_code})"
    except Exception as exc:
        return False, str(exc)


async def list_github_issues(token: str) -> Tuple[bool, list]:
    """Retrieves recent open issues assigned or created by the user."""
    clean_token = token.strip()
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                "https://api.github.com/user/issues?filter=all&state=open&per_page=10",
                headers={
                    "Authorization": f"Bearer {clean_token}",
                    "User-Agent": "PeculiarAI",
                    "Accept": "application/vnd.github.v3+json"
                }
            )
            if res.status_code == 200:
                return True, res.json()
            return False, []
    except Exception:
        return False, []
