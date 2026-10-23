"""
PeculiarAI — Third-Party Integrations Engine (Notion & GitHub)
Handles token validation, workspace searching, and executing actions like:
- Creating notes, meeting summaries, and task pages in Notion.
- Querying issues, creating issues, and checking pull requests in GitHub.
"""
import os
import json
import httpx
from typing import Optional, Dict, Any, Tuple, List


# ==============================================================================
# NOTION INTEGRATION
# ==============================================================================

async def validate_notion_token(token: str) -> Tuple[bool, Optional[str]]:
    """
    Validates a Notion internal integration token (secret_... or ntn_...) against Notion API.
    Returns (is_valid, bot_name_or_workspace_name).
    """
    clean_token = token.strip()
    if not (clean_token.startswith("secret_") or clean_token.startswith("ntn_")):
        return False, "Notion integration token usually starts with 'secret_' or 'ntn_'"

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


def parse_content_to_notion_blocks(content: str, default_formatting: Optional[str] = None) -> list:
    """
    Parses unstructured or markdown text into typed Notion block objects:
    - #, ##, ### or 'Heading:' -> heading_1, heading_2, heading_3
    - -, *, •, ◦ -> bulleted_list_item
    - 1., 2. -> numbered_list_item
    - [ ], [x] -> to_do
    - --- -> divider
    - plain lines -> paragraph
    """
    import re
    blocks = []
    lines = [l.strip() for l in content.split("\n") if l.strip()]
    force_bullets = (default_formatting == "bullets")

    for line in lines[:40]:  # Cap blocks
        if line.startswith("# "):
            blocks.append({
                "object": "block",
                "type": "heading_1",
                "heading_1": {"rich_text": [{"type": "text", "text": {"content": line[2:].strip()[:1800]}}]}
            })
        elif line.startswith("## "):
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": line[3:].strip()[:1800]}}]}
            })
        elif line.startswith("### "):
            blocks.append({
                "object": "block",
                "type": "heading_3",
                "heading_3": {"rich_text": [{"type": "text", "text": {"content": line[4:].strip()[:1800]}}]}
            })
        elif re.match(r"^(heading|title)\s*:\s*", line, re.I):
            head_text = re.sub(r"^(heading|title)\s*:\s*", "", line, flags=re.I).strip()
            blocks.append({
                "object": "block",
                "type": "heading_2",
                "heading_2": {"rich_text": [{"type": "text", "text": {"content": head_text[:1800]}}]}
            })
        elif line.startswith(("- ", "* ", "• ", "◦ ")):
            clean_item = re.sub(r"^[-*•◦]\s*", "", line).strip()
            blocks.append({
                "object": "block",
                "type": "bulleted_list_item",
                "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": clean_item[:1800]}}]}
            })
        elif re.match(r"^\d+\.\s+", line):
            clean_num = re.sub(r"^\d+\.\s+", "", line).strip()
            blocks.append({
                "object": "block",
                "type": "numbered_list_item",
                "numbered_list_item": {"rich_text": [{"type": "text", "text": {"content": clean_num[:1800]}}]}
            })
        elif line.startswith(("[ ] ", "[x] ", "[X] ")):
            is_checked = line.startswith(("[x] ", "[X] "))
            clean_todo = line[4:].strip()
            blocks.append({
                "object": "block",
                "type": "to_do",
                "to_do": {"rich_text": [{"type": "text", "text": {"content": clean_todo[:1800]}}], "checked": is_checked}
            })
        elif line in ("---", "***", "___"):
            blocks.append({"object": "block", "type": "divider", "divider": {}})
        else:
            if force_bullets:
                blocks.append({
                    "object": "block",
                    "type": "bulleted_list_item",
                    "bulleted_list_item": {"rich_text": [{"type": "text", "text": {"content": line[:1800]}}]}
                })
            else:
                blocks.append({
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"type": "text", "text": {"content": line[:1800]}}]}
                })

    if not blocks:
        blocks.append({
            "object": "block",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"type": "text", "text": {"content": content[:1800]}}]}
        })

    return blocks


async def create_notion_page(
    token: str,
    title: str,
    content: str,
    parent_id: Optional[str] = None,
    target_section: Optional[str] = None,
    formatting: Optional[str] = None
) -> Tuple[bool, str, Optional[str]]:
    """
    Creates a new page or appends a new section in the user's Notion workspace.
    - If target_section or 'existing notes' is requested, searches and appends blocks directly to that page.
    - If not specified, checks for a default 'Notes' page to append into, or creates a new page.
    - Respects heading and bullet formatting requests.
    Returns (success, message, page_url).
    """
    clean_token = token.strip() if token else ""
    if not clean_token:
        return False, "Notion token is missing. Kripya apna Notion account connect karein.", None

    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }

    clean_title = title.strip() or "PeculiarAI Note"
    parsed_blocks = parse_content_to_notion_blocks(content, default_formatting=formatting)

    async with httpx.AsyncClient(timeout=15.0) as client:
        # Search for accessible pages in the workspace
        search_res = await client.post(
            "https://api.notion.com/v1/search",
            headers=headers,
            json={"filter": {"value": "page", "property": "object"}, "page_size": 20}
        )
        page_results = search_res.json().get("results", []) if search_res.status_code == 200 else []

        target_existing_page = None
        target_name_query = (target_section or "").strip().lower()

        # Check if user explicitly asked for existing section/page
        if target_name_query and target_name_query not in ("default", "new", "none"):
            for r in page_results:
                title_arr = r.get("properties", {}).get("title", {}).get("title", []) or r.get("properties", {}).get("Name", {}).get("title", [])
                p_title = "".join([t.get("plain_text", "") for t in title_arr])
                if target_name_query in p_title.lower() or ("note" in target_name_query and "note" in p_title.lower()):
                    target_existing_page = r
                    break

        # If user did NOT specify where, check if a default Notes page exists to append a new section
        if not target_existing_page and not target_section:
            for r in page_results:
                title_arr = r.get("properties", {}).get("title", {}).get("title", []) or r.get("properties", {}).get("Name", {}).get("title", [])
                p_title = "".join([t.get("plain_text", "") for t in title_arr])
                if any(w in p_title.lower() for w in ("note", "notes", "peculiar", "scratchpad")):
                    target_existing_page = r
                    break

        # If an existing page was found, append a new section with divider + heading + blocks
        if target_existing_page:
            p_id = target_existing_page.get("id")
            title_arr = target_existing_page.get("properties", {}).get("title", {}).get("title", []) or target_existing_page.get("properties", {}).get("Name", {}).get("title", [])
            existing_title = "".join([t.get("plain_text", "") for t in title_arr]) or "Notes"

            append_payload = {
                "children": [
                    {"object": "block", "type": "divider", "divider": {}},
                    {
                        "object": "block",
                        "type": "heading_2",
                        "heading_2": {"rich_text": [{"type": "text", "text": {"content": clean_title[:200]}}]}
                    },
                    *parsed_blocks
                ]
            }
            append_res = await client.patch(f"https://api.notion.com/v1/blocks/{p_id}/children", headers=headers, json=append_payload)
            if append_res.status_code in (200, 201):
                p_url = target_existing_page.get("url") or f"https://notion.so/{p_id.replace('-', '')}"
                return True, f"Notes successfully added under section '{clean_title}' in your Notion page '{existing_title}'!", p_url

        # Otherwise, create a new page in the workspace
        target_parent = None
        target_type = None

        if parent_id:
            target_parent = parent_id.replace("-", "")
            target_type = "page_id"
        else:
            for r in page_results:
                if r.get("parent", {}).get("type") in ("workspace", "page_id"):
                    target_parent = r.get("id")
                    target_type = "page_id"
                    break

            if not target_parent and page_results:
                target_parent = page_results[0].get("id")
                target_type = "page_id"

            if not target_parent:
                # Check accessible databases
                db_res = await client.post(
                    "https://api.notion.com/v1/search",
                    headers=headers,
                    json={"filter": {"value": "database", "property": "object"}, "page_size": 10}
                )
                db_results = db_res.json().get("results", []) if db_res.status_code == 200 else []
                for d in db_results:
                    d_title = "".join([t.get("plain_text", "") for t in d.get("title", [])])
                    if d_title.lower() not in ("people", "members", "users", "directory"):
                        target_parent = d.get("id")
                        target_type = "database_id"
                        break

            if not target_parent:
                return False, "Notion workspace mein koi accessible parent page nahi mila. Kripya Notion page ke Share menu me jakar integration ko invite karein.", None

        if target_type == "database_id":
            payload = {
                "parent": {"database_id": target_parent},
                "properties": {
                    "Name": {
                        "title": [{"text": {"content": clean_title[:200]}}]
                    }
                },
                "children": parsed_blocks
            }
        else:
            payload = {
                "parent": {"page_id": target_parent},
                "properties": {
                    "title": [{"text": {"content": clean_title[:200]}}]
                },
                "children": parsed_blocks
            }

        create_res = await client.post("https://api.notion.com/v1/pages", headers=headers, json=payload)
        if create_res.status_code in (200, 201):
            created_data = create_res.json()
            page_url = created_data.get("url")
            return True, f"New Notion page '{clean_title}' successfully created with formatted notes!", page_url
        else:
            err_detail = create_res.text
            try:
                err_detail = create_res.json().get("message", err_detail)
            except Exception:
                pass
            return False, f"Notion error ({create_res.status_code}): {err_detail}", None


# Alias for clarity
create_or_append_notion_note = create_notion_page


async def list_notion_pages(token: str, query: Optional[str] = None) -> List[Dict[str, Any]]:
    """Lists or searches pages and notes in the user's Notion workspace."""
    clean_token = (token or "").strip()
    if not clean_token:
        clean_token = os.getenv("NOTION_API_KEY", "").strip()
    if not clean_token:
        return []

    headers = {
        "Authorization": f"Bearer {clean_token}",
        "Notion-Version": "2022-06-28",
        "Content-Type": "application/json"
    }
    payload: Dict[str, Any] = {
        "filter": {"value": "page", "property": "object"},
        "page_size": 25
    }
    if query:
        payload["query"] = str(query).strip()

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.post("https://api.notion.com/v1/search", headers=headers, json=payload)
            if res.status_code != 200:
                return []
            data = res.json()
            results = []
            for item in data.get("results", []):
                title_arr = (
                    item.get("properties", {}).get("title", {}).get("title", []) or
                    item.get("properties", {}).get("Name", {}).get("title", [])
                )
                title = "".join([t.get("plain_text", "") for t in title_arr]).strip() or "Untitled"
                page_id = item.get("id", "")
                page_url = item.get("url") or f"https://notion.so/{page_id.replace('-', '')}"
                results.append({
                    "id": page_id,
                    "title": title,
                    "url": page_url,
                    "created_time": item.get("created_time"),
                    "last_edited_time": item.get("last_edited_time")
                })
            return results
    except Exception as exc:
        print("list_notion_pages error:", repr(exc))
        return []



# ==============================================================================
# GITHUB INTEGRATION
# ==============================================================================

def _normalize_github_token(token: Any) -> Optional[str]:
    """Extracts and cleans raw token string whether passed as a string, dict, or env variable."""
    if not token:
        token = os.getenv("GITHUB_TOKEN")
    if isinstance(token, dict):
        token = token.get("api_key") or token.get("token") or token.get("access_token")
    if token and str(token).strip() not in ("", "public_access", "None", "null"):
        return str(token).strip()
    return None


async def validate_github_token(token: Any) -> Tuple[bool, Optional[str]]:
    """
    Validates a GitHub Personal Access Token or OAuth token.
    Returns (is_valid, username_or_login).
    """
    clean_token = _normalize_github_token(token)
    if not clean_token:
        return False, "Missing or empty GitHub token"
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


async def list_github_issues(token: Optional[Any] = None, repo: Optional[str] = None) -> Tuple[bool, list]:
    """Retrieves recent open issues for a specific repo or assigned/created by the user."""
    clean_token = _normalize_github_token(token)

    headers = {
        "User-Agent": "PeculiarAI",
        "Accept": "application/vnd.github.v3+json"
    }
    if clean_token:
        headers["Authorization"] = f"Bearer {clean_token}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            if repo and "/" in repo:
                url = f"https://api.github.com/repos/{repo.strip()}/issues?state=open&per_page=10"
            elif clean_token:
                url = "https://api.github.com/user/issues?filter=all&state=open&per_page=10"
            elif repo:
                url = f"https://api.github.com/repos/{repo.strip()}/issues?state=open&per_page=10"
            else:
                return False, []

            res = await client.get(url, headers=headers)
            if res.status_code == 200:
                data = res.json()
                return True, data if isinstance(data, list) else []
            return False, []
    except Exception:
        return False, []


async def list_github_repos(token: Optional[Any] = None, username: Optional[str] = None) -> Tuple[bool, list, Optional[str]]:
    """
    Retrieves real repositories for an authenticated user or a specific GitHub username.
    Returns (success, repos_list, resolved_username).
    """
    clean_token = _normalize_github_token(token)
    headers = {
        "User-Agent": "PeculiarAI",
        "Accept": "application/vnd.github.v3+json"
    }
    if clean_token:
        headers["Authorization"] = f"Bearer {clean_token}"

    clean_user = username.strip().lstrip("@") if username else None
    if clean_user and "github.com/" in clean_user:
        parts = clean_user.split("github.com/")[-1].strip("/").split("/")
        if parts:
            clean_user = parts[0]

    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            # 1. If specific username requested
            if clean_user:
                url = f"https://api.github.com/users/{clean_user}/repos?sort=updated&per_page=30"
                res = await client.get(url, headers=headers)
                if res.status_code == 200:
                    data = res.json()
                    if isinstance(data, list):
                        return True, data, clean_user
                return False, [], clean_user

            # 2. Otherwise list authenticated user's repos
            if clean_token:
                res = await client.get(
                    "https://api.github.com/user/repos?sort=updated&per_page=30",
                    headers=headers
                )
                if res.status_code == 200:
                    data = res.json()
                    user_res = await client.get("https://api.github.com/user", headers=headers)
                    login = user_res.json().get("login") if user_res.status_code == 200 else "User"
                    return True, data if isinstance(data, list) else [], login

            return False, [], None
    except Exception as exc:
        print("list_github_repos error:", repr(exc))
        return False, [], None


async def get_github_file(
    token: Optional[Any] = None,
    username: Optional[str] = None,
    repo: str = "",
    file_path: str = "README.md"
) -> Tuple[bool, Dict[str, Any]]:
    """
    Inspects or reads a file (like README.md) or lists directory contents from a GitHub repository.
    Decodes content and handles case-insensitivity or missing files cleanly.
    """
    import base64
    clean_token = _normalize_github_token(token)

    headers = {
        "User-Agent": "PeculiarAI",
        "Accept": "application/vnd.github.v3+json"
    }
    if clean_token:
        headers["Authorization"] = f"Bearer {clean_token}"

    clean_repo = repo.strip()
    clean_path = file_path.strip().lstrip("/") if file_path else ""
    clean_user = username.strip().lstrip("@") if username else None

    # Resolve full repo path owner/repo if only repo name was given
    if "/" not in clean_repo:
        if clean_user:
            clean_repo = f"{clean_user}/{clean_repo}"
        elif clean_token:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    user_res = await client.get("https://api.github.com/user", headers=headers)
                    if user_res.status_code == 200:
                        login = user_res.json().get("login")
                        clean_repo = f"{login}/{clean_repo}"
            except Exception:
                pass

    if "/" not in clean_repo:
        return False, {"exists": False, "repo": clean_repo, "error": f"Repository '{clean_repo}' must include owner or user (e.g. 'owner/repo')."}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            url = f"https://api.github.com/repos/{clean_repo}/contents/{clean_path}"
            res = await client.get(url, headers=headers)

            if res.status_code == 200:
                data = res.json()
                if isinstance(data, dict) and data.get("type") == "file":
                    raw_content = data.get("content", "")
                    encoding = data.get("encoding", "")
                    if encoding == "base64":
                        try:
                            decoded = base64.b64decode(raw_content).decode("utf-8", errors="replace")
                        except Exception:
                            decoded = raw_content
                    else:
                        decoded = raw_content

                    return True, {
                        "exists": True,
                        "type": "file",
                        "name": data.get("name"),
                        "path": data.get("path"),
                        "size": data.get("size"),
                        "sha": data.get("sha"),
                        "html_url": data.get("html_url"),
                        "content": decoded[:3000],
                        "truncated": len(decoded) > 3000,
                        "repo": clean_repo
                    }
                elif isinstance(data, list):
                    files = [item.get("name") for item in data if isinstance(item, dict) and item.get("type") == "file"]
                    dirs = [item.get("name") for item in data if isinstance(item, dict) and item.get("type") == "dir"]
                    return True, {
                        "exists": True,
                        "type": "dir",
                        "path": clean_path or "/",
                        "files": files,
                        "directories": dirs,
                        "repo": clean_repo
                    }

            if res.status_code == 404:
                root_res = await client.get(f"https://api.github.com/repos/{clean_repo}/contents/", headers=headers)
                if root_res.status_code == 200:
                    root_items = root_res.json()
                    if isinstance(root_items, list):
                        target_name = clean_path.split("/")[-1].lower() if clean_path else ""
                        matched_item = None
                        for item in root_items:
                            if isinstance(item, dict) and item.get("name", "").lower() == target_name:
                                matched_item = item
                                break

                        if matched_item and matched_item.get("url"):
                            file_res = await client.get(matched_item.get("url"), headers=headers)
                            if file_res.status_code == 200:
                                f_data = file_res.json()
                                raw_c = f_data.get("content", "")
                                try:
                                    dec = base64.b64decode(raw_c).decode("utf-8", errors="replace")
                                except Exception:
                                    dec = raw_c
                                return True, {
                                    "exists": True,
                                    "type": "file",
                                    "name": f_data.get("name"),
                                    "path": f_data.get("path"),
                                    "size": f_data.get("size"),
                                    "sha": f_data.get("sha"),
                                    "html_url": f_data.get("html_url"),
                                    "content": dec[:3000],
                                    "truncated": len(dec) > 3000,
                                    "repo": clean_repo
                                }

                        files_found = [i.get("name") for i in root_items if isinstance(i, dict) and i.get("type") == "file"]
                        dirs_found = [i.get("name") for i in root_items if isinstance(i, dict) and i.get("type") == "dir"]
                        return True, {
                            "exists": False,
                            "filename": clean_path or "README.md",
                            "repo": clean_repo,
                            "message": f"'{clean_path}' file repository '{clean_repo}' me nahi mili.",
                            "repo_files": files_found,
                            "repo_dirs": dirs_found
                        }

            return False, {"exists": False, "repo": clean_repo, "message": f"Could not access repository '{clean_repo}' (Status {res.status_code})."}
    except Exception as exc:
        return False, {"exists": False, "error": str(exc), "repo": clean_repo}


async def update_github_file(
    token: Optional[Any] = None,
    repo: str = "",
    file_path: str = "README.md",
    content: str = "",
    commit_message: str = "Update file via PeculiarAI",
    username: Optional[str] = None
) -> Tuple[bool, str, Optional[str]]:
    """
    Creates or updates a file (like README.md) in a GitHub repository via GitHub REST API.
    Returns (success, message, commit_url).
    """
    import base64
    clean_token = _normalize_github_token(token)

    if not clean_token:
        return False, "GitHub personal access token is required to commit files.", None

    headers = {
        "Authorization": f"Bearer {clean_token}",
        "User-Agent": "PeculiarAI",
        "Accept": "application/vnd.github.v3+json"
    }

    clean_repo = repo.strip()
    clean_path = file_path.strip().lstrip("/") if file_path else "README.md"
    clean_user = username.strip().lstrip("@") if username else None

    async with httpx.AsyncClient(timeout=15.0) as client:
        # 1. Resolve full repo path owner/repo if only repo name was given
        if "/" not in clean_repo:
            if clean_user:
                clean_repo = f"{clean_user}/{clean_repo}"
            else:
                user_res = await client.get("https://api.github.com/user", headers=headers)
                if user_res.status_code == 200:
                    login = user_res.json().get("login")
                    clean_repo = f"{login}/{clean_repo}"
                else:
                    return False, f"Could not determine owner for repository '{clean_repo}'. Please specify as 'owner/repo'.", None

        # 2. Check if file already exists to obtain its sha
        file_url = f"https://api.github.com/repos/{clean_repo}/contents/{clean_path}"
        sha = None
        get_res = await client.get(file_url, headers=headers)
        if get_res.status_code == 200:
            file_info = get_res.json()
            if isinstance(file_info, dict):
                sha = file_info.get("sha")

        # 3. Base64 encode content
        encoded_content = base64.b64encode(content.encode("utf-8")).decode("utf-8")
        payload = {
            "message": commit_message or "Update file via PeculiarAI",
            "content": encoded_content
        }
        if sha:
            payload["sha"] = sha

        put_res = await client.put(file_url, headers=headers, json=payload)
        if put_res.status_code in (200, 201):
            res_data = put_res.json()
            commit_html = res_data.get("commit", {}).get("html_url")
            return True, f"File '{clean_path}' in '{clean_repo}' successfully updated!", commit_html
        else:
            err = put_res.text
            try:
                err = put_res.json().get("message", err)
            except Exception:
                pass
            return False, f"GitHub error ({put_res.status_code}): {err}", None


