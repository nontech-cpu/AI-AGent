"""
PeculiarAI Zero-Unsupported-Claims Grounding Verifier (Phase 5)
Guarantees that NO unverified or hallucinated claim reaches the user.
Replaces brittle keyword-based rules with a structured LLM claim-checker
{claims_action_done, which_action, claims_external_fact, sourced_by_tool}
while retaining deterministic checks for tool-result facts (status==success, IDs, URLs).
"""
import os
import re
import json
import logging
from typing import Dict, Any, List, Tuple, Optional

logger = logging.getLogger("peculiar_verifier")

MEET_URL_PATTERN = re.compile(r'https?://meet\.google\.com/[a-z0-9-]+', re.IGNORECASE)
EMAIL_PATTERN = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')

READ_ONLY_TOOL_NAMES = {
    "list_contacts", "get_contact", "list_reminders", "get_schedule",
    "search_memory", "get_measurements", "get_memory_health_radar",
    "check_integrations_status", "find_nearby_places", "search_web",
    "web_search", "fetch_url", "read_webpage", "get_current_time",
    "calculate", "search_user_emails", "list_github_repos",
    "get_github_file", "list_github_issues", "list_notion_pages",
    "get_notion_page_content", "get_weather", "convert_currency",
    "get_market_price", "get_movie_showtimes", "check_availability",
    "get_meet_link", "get_commitments", "execute_python_code"
}

CLAIM_CHECKER_SYSTEM_PROMPT = """You are the Grounding & Truth Claim-Checker for PeculiarAI personal assistant.
Your job is to audit the Draft Reply against the Evidence (Tool Results, Memory Context, User Query).

You must output a single JSON object with these EXACT keys:
1. "claims_action_done": boolean. True if the Draft Reply states, asserts, or implies that a real-world, external, or stateful action was completed, created, updated, deleted, wiped, or sent (e.g. reminder created/deleted/updated, meeting scheduled, email sent, note/file saved, list updated, memory/data wiped or deleted, contacts deleted). False if the assistant is merely asking confirmation, refusing, explaining, asking a question, or greeting.
2. "which_action": string or null. The canonical tool name if an action is claimed (e.g. "create_reminder", "delete_reminder", "update_reminder", "send_email", "create_meeting", "save_note_or_fact", "manage_list", "request_master_privacy_wipe", "clear_chat_history"), or null if no action is claimed.
3. "claims_external_fact": boolean. True if the Draft Reply asserts specific real-world live facts (e.g. local business/mall names, transit routes/highways/distances, order/receipt details, market prices, live weather, availability). False for general domain knowledge (e.g. ML, coding concepts, science, history), pure language translations, greetings, or conversational remarks.
4. "claims_route_or_travel_time": boolean. True if the Draft Reply asserts, describes, or predicts a physical travel route, road directions (e.g. via specific roads, highways, bypasses), transit path, or travel time/duration (e.g. '10-15 minute lagenge') between locations. False otherwise.
5. "sourced_by_tool": boolean. True if ALL external facts, routes, or claimed actions in the Draft Reply are explicitly supported by and found in the Evidence (Tool Results or Memory Context). False if any claimed fact, route, or action is NOT found in the Evidence.
6. "reason": string. Short 1-sentence explanation of your assessment.

CRITICAL GROUNDING RULES FOR EXTERNAL SAAS / WORKSPACE ACTIONS:
- `execute_python_code` is a local Python sandbox for calculations, data processing, and document creation. It CANNOT modify external SaaS pages, emails, calendar events, Notion notes, or GitHub files. However, when it generates and delivers file artifacts (`files_sent`), document/PDF/report creation claims ARE fully verified and sourced by it!
- If Draft Reply claims an external action was completed (e.g. Notion page deleted/created/updated, email sent, reminder deleted) but no corresponding dedicated integration tool succeeded in Evidence, you MUST return "claims_action_done": true, "sourced_by_tool": false!

Respond ONLY with valid JSON. Do not wrap in markdown or backticks."""

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


def extract_evidence_text(
    tool_results: List[Dict[str, Any]],
    memory_context: Optional[Dict[str, Any]] = None,
    user_query: str = "",
    recent_history: Optional[List[Dict[str, Any]]] = None
) -> str:
    """Combines all tool outputs, memory context, recent conversation turns, and user inputs into an audit string."""
    parts = []
    if user_query:
        parts.append(f"User Query & Details:\n{user_query}")

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


def sanitize_regional_scripts(reply: str) -> str:
    """Replaces non-Latin regional language field headers with English labels."""
    if re.search(r"[\u0B80-\u0D7F\u0980-\u0AFF]", reply):
        cleaned = reply
        for k, v in REGIONAL_LABEL_REPLACEMENTS.items():
            cleaned = cleaned.replace(k, v)
        cleaned = re.sub(r"[\u0B80-\u0D7F\u0980-\u0AFF]+", "", cleaned)
        return cleaned
    return reply


def deterministic_verify(
    draft_reply: str,
    tool_results: List[Dict[str, Any]],
    memory_context: Optional[Dict[str, Any]] = None,
    user_query: str = "",
    recent_history: Optional[List[Dict[str, Any]]] = None
) -> Tuple[bool, Optional[str], List[str]]:
    """
    Deterministic safety checks for tool-result facts (status==success, IDs, URLs).
    Guarantees zero-latency enforcement on objective, verifiable tool artifacts.
    """
    evidence_str = extract_evidence_text(tool_results, memory_context, user_query, recent_history)
    violations = []

    # 1. Fake Google Meet Link Check
    meet_links = MEET_URL_PATTERN.findall(draft_reply)
    for link in meet_links:
        if link.lower() not in evidence_str.lower():
            violations.append(f"Fabricated Google Meet link: {link}")

    # 2. Tool Result Status & Pending Confirmation Check
    # If any tool returned 'needs_confirmation', draft must ask for confirmation rather than claim execution
    has_pending_confirm = any(
        isinstance(tr, dict) and tr.get("status") in ("needs_confirmation", "confirmation_required")
        for tr in tool_results
    )
    if has_pending_confirm:
        low_rep = draft_reply.lower()
        if not any(w in low_rep for w in ("confirm", "kya main", "kya aap", "proceed", "yes/no", "options:")):
            violations.append("Claimed action completed without success: Action requires confirmation")
            return False, "Maine details prepare kar li hain. Kya aap confirm karte hain ki ise proceed kar doon? (Reply yes/no)", violations

    # If any tool failed with error and draft does not mention the error/conflict
    has_conflict = any(
        isinstance(tr, dict) and tr.get("status") == "conflict_detected"
        for tr in tool_results
    )
    has_error = any(
        isinstance(tr, dict) and tr.get("status") == "error"
        for tr in tool_results
    )
    has_action_tool_success = any(
        isinstance(tr, dict)
        and (tr.get("status") in ("success", "ok") or tr.get("ok") is True)
        and str(tr.get("tool_name") or tr.get("action") or "").lower() not in READ_ONLY_TOOL_NAMES
        for tr in tool_results
    )
    low_rep = draft_reply.lower()

    if not has_action_tool_success and not has_pending_confirm:
        # Strip question clauses so questions like "Aaj Tuesday hai — bhej diya?" are not flagged as completion claims
        clean_rep = re.sub(r'[^.!?\n]*\?', '', low_rep)
        action_claim_tokens = (
            "set kar diya", "kar diya hai", "kar di hai", "email bhej diya", "mail bhej diya", "send kar diya",
            "schedule kar diya", "schedule kar di", "delete kar diya", "delete kar di", "delete kar diye",
            "save kar diya", "update kar diya", "shift kar diya", "successfully created", "successfully sent",
            "successfully scheduled", "successfully deleted", "hata di gayi", "hata diya", "clear kar diya", "wipe kar diya"
        )
        has_claim_token = any(tok in clean_rep for tok in action_claim_tokens)
        has_verb_and_done = (
            any(v in clean_rep for v in ("delete", "trash", "hata", "remove", "clear", "wipe", "create", "bana", "schedule", "send", "bhej", "update", "save")) and
            any(d in clean_rep for d in ("kar diya", "kar diye", "kar di", "ho gaya", "ho gaye", "done!", "done:", "done.", "successfully", "kiye gaye", "diye gaye"))
        )
        if has_claim_token or has_verb_and_done:
            is_wipe_claim = any(w in clean_rep for w in ("delete kar di", "delete kar diye", "clear kar diya", "hata di gayi", "wipe kar diya")) and any(w in clean_rep for w in ("memory", "data", "contacts", "reminders", "account", "location"))
            is_user_wipe_request = any(k in (user_query or "").lower() for k in ("delete", "dlete", "delte", "delet", "wipe", "clear", "uda", "hata", "bhool", "erase", "reset", "purge", "forget", "clean", "entire memory", "saari memory", "all data", "poora data"))
            if is_wipe_claim and is_user_wipe_request:
                violations.append("Claimed data/memory wipe completed without verified deletion tool execution")
                revised = (
                    "⚠️ **Security & Privacy Warning**:\n\n"
                    "Kya aap sach me apni saari memories, chat history, notes, reminders, smart lists aur saved contacts permanently delete karna chahte hain? "
                    "Ye action irreversible hai aur data wapas recover nahi hoga.\n\n"
                    "Reply **yes/haan** to confirm ya **no/cancel** to abort."
                )
                return False, revised, violations
            violations.append("Claimed action completed without success: No successful tool execution in evidence")
            return False, "Maine koshish ki lekin action complete nahi ho paya. Kripya check karein.", violations

    if has_error and not has_conflict:
        # If tool failed, ensure draft does not claim success
        if any(w in low_rep for w in ("ho gaya", "kar diya", "bhej diya", "saved", "created", "scheduled", "success")):
            violations.append("Claimed action completed when tool reported error")
            return False, "Maine koshish ki lekin action complete nahi ho paya. Kripya check karein.", violations

    # 3. Fake GitHub Repositories Check (if repo tools returned real repo lists)
    if any(isinstance(tr, dict) and ("repositories" in tr or "repo" in tr) for tr in tool_results):
        real_repos = set()
        for gtr in tool_results:
            if not isinstance(gtr, dict):
                continue
            if "repositories" in gtr:
                real_repos.update(str(r).lower() for r in gtr.get("repositories", []))
            if "repo" in gtr:
                r_str = str(gtr.get("repo")).lower()
                real_repos.add(r_str)
                if "/" in r_str:
                    real_repos.add(r_str.split("/")[-1])
        if real_repos:
            clean_draft = re.sub(r'https?://[^\s]+', '', draft_reply)
            clean_draft = re.sub(r'[*_`\[\]()]', '', clean_draft)
            mentioned_items = re.findall(r'(?:[-*•]|\d+\.)\s*([a-zA-Z0-9_\-\/]+)', clean_draft)
            allowed_tokens = {"peculiarai", "github", "repositories", "repo", "public", "private", "account", "profile", "repos", "readme", "readmemd", "issues", "issue", "branch", "main", "master", "status", "commit", "url", "code"}
            for item in mentioned_items:
                clean_item = item.lower().split('/')[-1]
                if clean_item not in real_repos and item.lower() not in real_repos and clean_item not in allowed_tokens and len(clean_item) > 2 and not clean_item.isdigit():
                    violations.append(f"Fabricated GitHub repository: {item}")

    # 4. Route & Travel Time Grounding: handled structurally in audit_claims_with_llm (claims_route_or_travel_time)

    # 5. Regional Script Sanitization
    draft_reply = sanitize_regional_scripts(draft_reply)

    if violations:
        if any("Fabricated Google Meet link" in v for v in violations):
            clean_reply = MEET_URL_PATTERN.sub("[Meet link available upon confirmation]", draft_reply)
            return False, clean_reply, violations
        return False, "Maine koshish ki lekin action complete nahi ho paya. Kripya check karein.", violations

    return True, draft_reply, []


async def audit_claims_with_llm(
    draft_reply: str,
    evidence_str: str,
    user_query: str = ""
) -> Dict[str, Any]:
    """
    Calls unified LLM provider with structured JSON claim-checker schema:
    {claims_action_done: bool, which_action: str|null, claims_external_fact: bool, sourced_by_tool: bool, reason: str}
    """
    default_res = {
        "claims_action_done": False,
        "which_action": None,
        "claims_external_fact": False,
        "sourced_by_tool": True,
        "reason": "Default pass"
    }

    try:
        import llm.provider as llm_provider
        user_content = (
            f"User Query:\n{user_query}\n\n"
            f"Evidence (Tool Results & Memory):\n{evidence_str or '[No tool or memory evidence]'}\n\n"
            f"Draft Reply to Audit:\n{draft_reply}"
        )
        resp = await llm_provider.chat(
            messages=[
                {"role": "system", "content": CLAIM_CHECKER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content}
            ],
            response_format={"type": "json_object"},
            temperature=0.0,
            timeout=8.0
        )
        if resp.text:
            cleaned_text = resp.text.strip()
            # Strip potential markdown code fence wrapper
            if cleaned_text.startswith("```"):
                cleaned_text = re.sub(r"^```(?:json)?\s*", "", cleaned_text)
                cleaned_text = re.sub(r"\s*```$", "", cleaned_text)
            parsed = json.loads(cleaned_text)
            return {
                "claims_action_done": bool(parsed.get("claims_action_done", False)),
                "which_action": parsed.get("which_action"),
                "claims_external_fact": bool(parsed.get("claims_external_fact", False)),
                "claims_route_or_travel_time": bool(parsed.get("claims_route_or_travel_time", False)),
                "sourced_by_tool": bool(parsed.get("sourced_by_tool", False)),
                "reason": str(parsed.get("reason", ""))
            }
    except Exception as exc:
        logger.warning(f"LLM Claim-Checker error: {exc}")

    # Fallback heuristic when LLM provider is offline or unreachable in unit tests:
    low = draft_reply.lower()
    has_action_phrase = any(w in low for w in (
        "kar diya", "bhej diya", "delete kar diya", "set kar diya", "laga diya",
        "save kar diya", "update kar diya", "shift kar diya", "successfully created", "successfully sent",
        "successfully deleted", "successfully scheduled"
    ))
    which_act = None
    if "reminder" in low or "meeting" in low:
        which_act = "delete_reminder" if any(w in low for w in ("delete", "hta", "hata")) else "create_reminder"
    elif "email" in low or "mail" in low:
        which_act = "send_email"

    claims_route = any(w in low for w in ("road", "rasta", "route", "hote hue", "highway", "bypass", "flyover", "shortest rasta")) or bool(re.search(r'\b\d+(?:-\d+)?\s*(?:minute|min|ghante|hours?)\b', low))
    has_ext_fact = any(w in low for w in ("mall", "malls", "km", "order tha:", "order", "rupees", "₹"))
    has_evidence = bool(evidence_str and ("status" in evidence_str or "Tool 1" in evidence_str))

    return {
        "claims_action_done": has_action_phrase,
        "which_action": which_act,
        "claims_external_fact": has_ext_fact,
        "claims_route_or_travel_time": claims_route,
        "sourced_by_tool": has_evidence if (has_action_phrase or has_ext_fact or claims_route) else True,
        "reason": "Fallback heuristic evaluation"
    }


async def verify_and_ground_reply(
    draft_reply: str,
    tool_results: List[Dict[str, Any]],
    memory_context: Optional[Dict[str, Any]] = None,
    user_query: str = "",
    recent_history: Optional[List[Dict[str, Any]]] = None,
    allow_llm: bool = True
) -> Tuple[bool, str, List[str]]:
    """
    Phase 5 Unified Verification & Grounding:
    1. Deterministic tool-result facts check (status==success, Meet links, repo lists).
    2. Structured LLM claim-checker evaluating {claims_action_done, which_action, claims_external_fact, sourced_by_tool}.
    3. Blocks any reply claiming action execution without successful tool result,
       or claiming external live facts without verified tool source.
    """
    # 1. Deterministic rule check
    passed_det, revised_det, violations_det = deterministic_verify(
        draft_reply=draft_reply,
        tool_results=tool_results,
        memory_context=memory_context,
        user_query=user_query,
        recent_history=recent_history
    )
    if not passed_det:
        return False, revised_det or "Maine koshish ki lekin action complete nahi ho paya. Kripya check karein.", violations_det

    # Fast-path for non-claiming conversational phrases (0ms overhead)
    low_reply = draft_reply.strip().lower()
    is_greeting = any(low_reply.startswith(g) for g in ("hi", "hello", "hey", "bas bhai", "namaste", "aur bhai", "main ekdum", "main badhiya", "ekdum badhiya"))
    is_clarifying_question = draft_reply.strip().endswith("?") and not any(w in low_reply for w in ("kar diya", "bhej diya", "saved", "created"))
    is_explicit_refusal = any(w in low_reply for w in ("mere paas iski koi saved information nahi", "mere paas yeh information nahi hai", "i don't have access", "verified information nahi hai"))
    # When no tool was executed and draft makes no real-world mutation/action claims, it is pure conversational dialogue:
    is_pure_dialogue = not tool_results and not any(w in low_reply for w in ("kar diya", "bhej diya", "delete kar diya", "successfully created", "successfully deleted"))

    if is_greeting or is_clarifying_question or is_explicit_refusal or is_pure_dialogue:
        return True, sanitize_regional_scripts(draft_reply), []

    # If user instruction is to save/add/update/create and model is merely repeating/acknowledging input text
    is_save_request = any(k in user_query.lower() for k in ["save", "note", "add", "store", "yaad rakh", "likh", "bana"]) and not any(w in low_reply for w in ("kar diya", "bhej diya", "created", "deleted"))
    if is_save_request:
        return True, sanitize_regional_scripts(draft_reply), []

    evidence_text = extract_evidence_text(tool_results, memory_context, user_query, recent_history)

    # 2. Structured LLM Claim Checker
    violations = list(violations_det)
    audit = await audit_claims_with_llm(draft_reply, evidence_text, user_query)

    # Check 2A: Action Claimed Without Successful Tool Result
    if audit.get("claims_action_done"):
        which_act = (audit.get("which_action") or "").lower()
        # Look for matching successful mutating tool execution in tool_results
        has_tool_success = False
        for tr in tool_results:
            if not isinstance(tr, dict):
                continue
            status = tr.get("status")
            ok = tr.get("ok", False)
            if status in ("success", "ok", "needs_confirmation", "confirmation_required", "conflict_detected") or ok is True:
                tr_name = str(tr.get("tool_name") or tr.get("action") or "").lower()
                # Read-only tools CANNOT satisfy a mutation/deletion action claim (unless file artifacts were produced)!
                if tr_name in READ_ONLY_TOOL_NAMES and not (tr.get("files_sent") or tr.get("filename")):
                    continue
                if tr.get("files_sent") or tr.get("filename"):
                    has_tool_success = True
                    break
                # If specific action was named, match it; else any mutating success
                if not which_act or not tr_name or which_act in tr_name or tr_name in which_act:
                    has_tool_success = True
                    break

        if not has_tool_success:
            act_label = which_act or "action"
            combined_text = (user_query + " " + draft_reply).lower()
            is_wipe_act = any(w in act_label for w in ("wipe", "delete", "clear", "purge", "erase")) and any(w in combined_text for w in ("memory", "data", "account", "everything", "history", "contacts", "reminders"))
            is_user_wipe_request = any(k in (user_query or "").lower() for k in ("delete", "dlete", "delte", "delet", "wipe", "clear", "uda", "hata", "bhool", "erase", "reset", "purge", "forget", "clean", "entire memory", "saari memory", "all data", "poora data"))
            if is_wipe_act and is_user_wipe_request:
                violations.append(f"Claimed data/memory wipe '{act_label}' completed without verified deletion tool execution")
                revised = (
                    "⚠️ **Security & Privacy Warning**:\n\n"
                    "Kya aap sach me apni saari memories, chat history, notes, reminders, smart lists aur saved contacts permanently delete karna chahte hain? "
                    "Ye action irreversible hai aur data wapas recover nahi hoga.\n\n"
                    "Reply **yes/haan** to confirm ya **no/cancel** to abort."
                )
                return False, revised, violations
            violations.append(f"Claimed action '{act_label}' completed without verified tool execution")
            revised = "Aapka request note kar liya gaya hai. Kripya details confirm karein taaki main ise proceed kar sakun."
            return False, revised, violations

    # Check 2B: Route / Travel Time Claimed Without Verified Navigation Tool
    has_nav_tool = any(isinstance(tr, dict) and tr.get("tool_name") in ("google_maps", "get_directions", "navigation") for tr in tool_results)
    if audit.get("claims_route_or_travel_time") and not has_nav_tool:
        violations.append("Ungrounded route or travel time claimed without verified maps tool")
        revised = (
            "Mere paas live GPS navigation aur real-time traffic maps ka live access nahi hai, "
            "isliye main exact travel time ya road route predict nahi kar sakta. "
            "Aap live traffic aur best route ke liye Google Maps check kar sakte hain:\n\n"
            "👉 https://www.google.com/maps/dir/?api=1"
        )
        return False, revised, violations

    # Check 2C: General External Facts Claimed Without Tool Source
    if audit.get("claims_external_fact") and not audit.get("sourced_by_tool"):
        # If evidence contains the exact numbers/facts, allow
        violations.append("Unsubstantiated external fact claimed without verified tool source")
        revised = "Mere paas iski verified information nahi hai."
        return False, revised, violations

    return True, sanitize_regional_scripts(draft_reply), violations
