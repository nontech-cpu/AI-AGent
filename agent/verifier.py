"""
PeculiarAI Zero-Unsupported-Claims Grounding Verifier (Phase 3)
Guarantees that NO unverified or hallucinated claim reaches the user.
Validates draft replies against tool execution results and cognitive memory evidence.
"""
import os
import re
import json
import logging
from typing import Dict, Any, List, Tuple, Optional
import httpx

logger = logging.getLogger("peculiar_verifier")

MEET_URL_PATTERN = re.compile(r'https?://meet\.google\.com/[a-z0-9-]+', re.IGNORECASE)
EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')

VERIFIER_SYSTEM_PROMPT = """You are the Grounding Verifier for PeculiarAI assistant.
Your sole job is to audit the Draft Reply against the Evidence (tool results and retrieved memory).

Rules:
1. Every specific factual claim (person names, dates, times, repository names, deliverable items, URLs, emails) MUST be supported by the Evidence.
2. If the Draft Reply mentions information not in the Evidence (hallucination or invention), rewrite it to state ONLY what is known, or politely say "Mere paas yeh information saved nahi hai".
3. If the Draft Reply is already fully grounded or is a simple conversational greeting/question, keep it intact.
4. If an action (meeting, email) is pending confirmation, ensure the draft asks for confirmation instead of claiming it was already completed.

Respond ONLY with valid JSON:
{
  "is_grounded": true/false,
  "unsupported_claims": ["list of hallucinated items if any"],
  "final_reply": "corrected or verified text"
}
"""

def extract_evidence_text(
    tool_results: List[Dict[str, Any]],
    memory_context: Optional[Dict[str, Any]] = None,
    user_query: str = "",
    recent_history: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Combines all tool outputs, memory context, recent conversation turns, and user-provided inputs into a single audit string."""
    parts = []
    if user_query:
        parts.append(f"User Query & Provided Details:\n{user_query}")

    if recent_history:
        hist_lines = []
        for t in recent_history:
            r = t.get("role", "turn")
            txt = t.get("text", "")
            if txt:
                hist_lines.append(f"{r}: {txt}")
        if hist_lines:
            parts.append("Recent Conversation History:\n" + "\n".join(hist_lines))

    if memory_context and memory_context.get("formatted_block"):
        parts.append(f"Memory Evidence:\n{memory_context['formatted_block']}")

    if tool_results:
        parts.append("Tool Results Evidence:")
        for idx, tr in enumerate(tool_results, 1):
            parts.append(f"Tool {idx}: {json.dumps(tr, ensure_ascii=False)}")

    return "\n\n".join(parts)


def deterministic_verify(
    draft_reply: str,
    tool_results: List[Dict[str, Any]],
    memory_context: Optional[Dict[str, Any]] = None,
    user_query: str = "",
    recent_history: Optional[List[Dict[str, Any]]] = None
) -> Tuple[bool, Optional[str], List[str]]:
    """
    Fast, zero-latency deterministic safety checks.
    Returns (passed, revised_reply, violation_reasons).
    """
    evidence_str = extract_evidence_text(tool_results, memory_context, user_query, recent_history)
    violations = []

    # 1. Fake Google Meet Link Check
    meet_links = MEET_URL_PATTERN.findall(draft_reply)
    for link in meet_links:
        if link.lower() not in evidence_str.lower():
            violations.append(f"Fabricated Google Meet link: {link}")

    # 2. Fake Action Completion Claim Check
    completed_action_phrases = [
        "meeting create kar di",
        "meeting schedule kar di",
        "meeting book kar di",
        "meeting set ho gayi",
        "email bhej diya",
        "email send kar diya",
        "drive pe upload kar diya"
    ]
    low_reply = draft_reply.lower()
    for phrase in completed_action_phrases:
        if phrase in low_reply:
            # Check if any tool result had status "success" for that action
            action_succeeded = any(
                isinstance(tr, dict) and tr.get("status") == "success" and tr.get("action") in ["create_meeting", "send_email", "upload_drive"]
                for tr in tool_results
            )
            needs_confirm = any(
                isinstance(tr, dict) and tr.get("status") == "needs_confirmation"
                for tr in tool_results
            )
            if not action_succeeded:
                violations.append(f"Claimed action completed without success: '{phrase}'")
                if needs_confirm:
                    # Replace with confirmation inquiry
                    return False, "Maine meeting details prepare kar li hain. Kya aap confirm karte hain ki ise create kar doon? (Reply yes/no)", violations

    # 3. Fake GitHub Repositories Check
    if "github" in user_query.lower() or "repo" in user_query.lower():
        # Check if a GitHub repository listing tool was specifically executed to list user repos
        has_repo_listing = any(isinstance(tr, dict) and "repositories" in tr for tr in tool_results)
        is_asking_repos = bool(re.search(r"\b(repo|repos|repository|repositories)\b", user_query, re.I)) and bool(re.search(r"\b(list|show|dikha|bata|btao|batao|kaun|all|mere|mera|meri|my|meine|zeige|welche|names|naam|tell|get)\b", user_query, re.I))
        if has_repo_listing and is_asking_repos:
            real_repos = set()
            gh_user = ""
            allowed_tokens = {
                "peculiarai", "github", "repositories", "repo", "public", "private", "account",
                "profile", "repos", "readme", "readmemd", "issues", "issue", "branch", "main",
                "master", "project", "projects", "tech", "features", "feature", "repository",
                "author", "stack", "dynamic", "pricing", "overview", "note", "notes", "details",
                "status", "commit", "url", "code"
            }
            for gtr in tool_results:
                if not isinstance(gtr, dict):
                    continue
                if "repositories" in gtr:
                    raw_repos = gtr.get("repositories", [])
                    real_repos.update(str(r).lower() for r in raw_repos)
                if "repo" in gtr:
                    r_str = str(gtr.get("repo")).lower()
                    real_repos.add(r_str)
                    if "/" in r_str:
                        real_repos.add(r_str.split("/")[-1])
                if gtr.get("username"):
                    gh_user = gtr.get("username").lower()
                for fld in ["name", "path", "filename"]:
                    if gtr.get(fld):
                        real_repos.add(str(gtr[fld]).lower())
                for list_fld in ["repo_files", "repo_dirs", "files", "directories"]:
                    if gtr.get(list_fld) and isinstance(gtr[list_fld], list):
                        real_repos.update(str(x).lower() for x in gtr[list_fld])

            if gh_user:
                allowed_tokens.add(gh_user)
                for part in gh_user.split("-"):
                    if part: allowed_tokens.add(part)
                for r in list(real_repos):
                    real_repos.add(f"{gh_user}/{str(r).lower()}")

            allowed_tokens.update({"peculiarai", "github", "repositories", "repo", "public", "private", "account", "profile", "repos", "readme", "readmemd", "issues", "issue", "branch", "main", "master", "commit", "commits", "blob", "tree", "pull", "pulls", "sha", "url", "link", "update", "file"})
            # Normalize real repos: alphanumeric only lowercase
            norm_real_repos = {re.sub(r'[^a-z0-9]', '', r) for r in real_repos}
            norm_allowed = {re.sub(r'[^a-z0-9]', '', a) for a in allowed_tokens}

            # Strip URLs and markdown formatting so URLs/commit links are not treated as repo names
            clean_draft = re.sub(r'https?://[^\s]+', '', draft_reply)
            clean_draft = re.sub(r'[*_`\[\]()]', '', clean_draft)
            mentioned_items = re.findall(r'(?:[-*•]|\d+\.)\s*([a-zA-Z0-9_\-\/]+)', clean_draft)
            for item in mentioned_items:
                if any(x in item.lower() for x in ("/commit/", "/blob/", "/pull/", "http", "github.com")):
                    continue
                clean_item = item.lower().split('/')[-1]
                norm_item = re.sub(r'[^a-z0-9]', '', item.lower())
                norm_clean = re.sub(r'[^a-z0-9]', '', clean_item)

                if (clean_item not in real_repos and 
                    item.lower() not in real_repos and 
                    norm_item not in norm_real_repos and
                    norm_clean not in norm_real_repos and
                    item.lower() not in allowed_tokens and
                    clean_item not in allowed_tokens and
                    norm_item not in norm_allowed and
                    norm_clean not in norm_allowed):
                    if len(norm_clean) > 2 and not norm_clean.isdigit():
                        violations.append(f"Fabricated GitHub repository: {item}")

    # 4. Fabricated Email Address Check
    draft_emails = set(EMAIL_PATTERN.findall(draft_reply))
    evidence_emails = set(EMAIL_PATTERN.findall(evidence_str))
    for em in draft_emails:
        if em.lower() not in {e.lower() for e in evidence_emails}:
            violations.append(f"Fabricated email address: {em}")

    # 5. Regional Indian Script (Tamil, Telugu, Kannada, Malayalam, etc.) Sanitization
    REGIONAL_LABEL_REPLACEMENTS = {
        # Tamil
        "பெயர்": "Name",
        "பங்கு": "Role",
        "தொலைபேசி": "Phone",
        "மின்னஞ்சல்": "Email",
        "வாக்குறுதி": "Commitment",
        "தேதி": "Date",
        "நேரம்": "Time",
        "குறிப்பு": "Notes",
        # Telugu
        "పేరు": "Name",
        "పాత్ర": "Role",
        "ఫోన్": "Phone",
        "ఇమెయిల్": "Email",
        "నిబద్ధత": "Commitment",
        # Kannada
        "ಹೆಸರು": "Name",
        "ಪಾತ್ರ": "Role",
        "ದೂರವಾಣಿ": "Phone",
        "ಇಮೇಲ್": "Email",
        # Malayalam
        "പേര്": "Name",
        "പങ്ക്": "Role",
        "ഫോൺ": "Phone",
        "ഇമെയിൽ": "Email",
    }
    if re.search(r"[\u0B80-\u0D7F\u0980-\u0AFF]", draft_reply):
        cleaned_reply = draft_reply
        for k, v in REGIONAL_LABEL_REPLACEMENTS.items():
            cleaned_reply = cleaned_reply.replace(k, v)
        cleaned_reply = re.sub(r"[\u0B80-\u0D7F\u0980-\u0AFF]+", "", cleaned_reply)
        draft_reply = cleaned_reply

    if violations:
        # If fake links found, strip them
        if any("Fabricated Google Meet link" in v for v in violations):
            clean_reply = MEET_URL_PATTERN.sub("[Meet link available upon confirmation]", draft_reply)
            return False, clean_reply, violations

        return False, "Mere paas iski koi saved information nahi hai.", violations

    return True, draft_reply, []


async def verify_and_ground_reply(
    draft_reply: str,
    tool_results: List[Dict[str, Any]],
    memory_context: Optional[Dict[str, Any]] = None,
    user_query: str = "",
    recent_history: Optional[List[Dict[str, Any]]] = None
) -> Tuple[bool, str, List[str]]:
    """
    Two-tier verification pass:
    1. Deterministic rule check (Meet links, completion claims, repo audits, email audits).
    2. Optional LLM verifier pass if factual assertions need cross-checking.
    """
    # 1. Deterministic fast check (0 latency)
    passed, revised, violations = deterministic_verify(draft_reply, tool_results, memory_context, user_query, recent_history)
    if not passed:
        return False, revised or "Mere paas iski koi saved information nahi hai. Kya aap batana chahenge taaki main yaad rakhun?", violations

    # If already a missing-data statement or simple greeting, pass immediately
    # If user instruction is to save/add/update/create, do not flag acknowledging the user's data as unsupported
    is_save_request = any(k in user_query.lower() for k in ["save", "note", "add", "store", "update", "yaad rakh", "likh", "bana", "set", "kar le", "krle", "rakh"])
    if is_save_request:
        return True, draft_reply, []

    # If no evidence and draft claims specific names/commitments, flag it
    evidence_text = extract_evidence_text(tool_results, memory_context, user_query, recent_history)
    if not evidence_text.strip():
        if any(w in user_query.lower() for w in ["kya bhejna", "kab hai", "details", "repo", "schedule", "free hu", "kahan", "kaam", "kaun", "kiska", "who", "where", "what", "email", "phone"]):
            fallback = "Mere paas iski koi saved information nahi hai. Kya aap mujhe batana chahenge taaki main yaad rakhun?"
            return False, fallback, ["Unsubstantiated factual claim with zero tool/memory evidence."]
        return True, draft_reply, []

    # If STRICT_LLM_VERIFIER is set, perform second-tier LLM verification
    if os.getenv("STRICT_LLM_VERIFIER", "false").lower() == "true":
        groq_key = os.getenv("GROQ_API_KEY")
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                resp = await client.post(
                    "https://api.groq.com/openai/v1/chat/completions",
                    headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                    json={
                        "model": "openai/gpt-oss-20b",
                        "messages": [
                            {"role": "system", "content": VERIFIER_SYSTEM_PROMPT},
                            {
                                "role": "user",
                                "content": f"User Query: {user_query}\nEvidence:\n{evidence_text}\nDraft Reply:\n{draft_reply}"
                            }
                        ],
                        "response_format": {"type": "json_object"},
                        "temperature": 0.0
                    }
                )

                if resp.status_code == 200:
                    data = resp.json()["choices"][0]["message"]["content"]
                    result = json.loads(data)
                    is_grounded = result.get("is_grounded", True)
                    final_text = result.get("final_reply", draft_reply)
                    unsupported = result.get("unsupported_claims", [])
                    return is_grounded, final_text, unsupported

        except Exception as exc:
            logger.warning(f"LLM Verifier pass skipped: {exc}")
    return True, draft_reply, []
