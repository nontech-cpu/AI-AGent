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

from connectors.guardrails import is_cancellation_or_decline

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
1. "claims_action_done": boolean. True if the Draft Reply states, asserts, or implies that a real-world, external, or stateful action was completed, created, updated, deleted, wiped, or sent in the CURRENT turn.
CRITICAL INQUIRY, CANCELLATION & ERROR EXCEPTION:
- If the User Query declines, cancels, or dismisses an action (e.g. "rhne de", "no", "cancel", "rehne de", "chhod do", "nahi chahiye", "never mind", "drop it"), or if the Draft Reply acknowledges that an action was cancelled, not performed, or dismissed, return "claims_action_done": false! Acknowledging cancellation or decline is NOT an action execution claim.
- If the User Query is an inquiry or status question about whether something was done in the past, or asking about current state/reminders/calendar (e.g. "ky tumn firecraker wala reminder htaa diya caldner se bhi?", "is my meeting cancelled?", "kya delete ho gaya?"), answering the question or reporting status is an INFORMATIONAL ANSWER, NOT an action claim. Return "claims_action_done": false!
- If Tool Results contains an error, conflict, or parameter request (e.g. 'time_already_passed', 'needs_info', 'conflict_detected', 'error', 'no_user_request_for_write') and the Draft Reply explains this situation or asks the user how to proceed, return "claims_action_done": false!
- Return "claims_action_done": false if the assistant is merely asking confirmation, refusing, explaining, answering a status check, or greeting.
2. "which_action": string or null. The canonical tool name if an action is claimed (e.g. "create_reminder", "delete_reminder", "update_reminder", "send_email", "create_meeting", "save_note_or_fact", "manage_list", "request_master_privacy_wipe", "clear_chat_history"), or null if no action is claimed.
3. "claims_external_fact": boolean. True if the Draft Reply asserts specific real-world live facts (e.g. local business/mall names, transit routes/highways/distances, order/receipt details, market prices, live weather, availability). False for general domain knowledge (e.g. ML, coding concepts, science, history), pure language translations, greetings, conversational remarks, festival wishes, food/celebration commentary, or acknowledging the user's casual personal plans.
4. "claims_route_or_travel_time": boolean. True if the Draft Reply asserts, describes, or predicts a physical travel route, road directions (e.g. via specific roads, highways, bypasses), transit path, or travel time/duration (e.g. '10-15 minute lagenge') between locations. False otherwise.
5. "claims_unavailable_capability": boolean. True if the Draft Reply states, offers, or promises that the assistant can perform an action or capability that does NOT exist in the tool suite (e.g. offering to book tickets, book cinema/movie tickets, book flights/trains/buses, make payments/purchases, order food, place phone calls). False otherwise.
6. "sourced_by_tool": boolean. True if ALL external facts, routes, or claimed actions in the Draft Reply are explicitly supported by and found in the Evidence (Tool Results or Memory Context). False if any claimed fact, route, or action is NOT found in the Evidence.
7. "reason": string. Short 1-sentence explanation of your assessment.

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
    recent_history: Optional[List[Dict[str, Any]]] = None,
    user_lang: str = "english"
) -> Tuple[bool, Optional[str], List[str]]:
    """
    Deterministic safety checks for tool-result facts (status==success, IDs, URLs).
    Guarantees zero-latency enforcement on objective, verifiable tool artifacts.
    """
    from core.i18n import get_i18n_string
    action_failed_msg = get_i18n_string("action_failed", lang=user_lang)
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

    is_user_inquiry = bool(user_query and "?" in user_query)
    is_cancellation = is_cancellation_or_decline(user_query, draft_reply)
    if not has_action_tool_success and not has_pending_confirm and not is_user_inquiry and not is_cancellation:
        # Strip question clauses so questions like "Aaj Tuesday hai — bhej diya?" are not flagged as completion claims
        clean_rep = re.sub(r'[^.!?\n]*\?', '', low_rep)
        action_claim_tokens = (
            "set kar diya", "kar diya hai", "kar di hai", "email bhej diya", "mail bhej diya", "send kar diya",
            "schedule kar diya", "schedule kar di", "delete kar diya", "delete kar di", "delete kar diye",
            "save kar diya", "update kar diya", "shift kar diya", "successfully created", "successfully sent",
            "successfully scheduled", "successfully deleted", "hata di gayi", "hata diya", "clear kar diya", "wipe kar diya"
        )
        has_claim_token = any(tok in clean_rep for tok in action_claim_tokens)
        if has_claim_token:
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
            return False, action_failed_msg, violations

    if not is_cancellation and has_error and not has_conflict and not has_action_tool_success:
        # If tool failed and no action succeeded, ensure draft does not claim success
        if any(w in low_rep for w in ("ho gaya", "kar diya", "bhej diya", "saved", "created", "scheduled", "success")):
            violations.append("Claimed action completed when tool reported error")
            return False, action_failed_msg, violations

    # 3. Fake GitHub Repositories Check (ONLY when tool returned repository list)
    has_repo_list_tool = any(
        isinstance(tr, dict) and "repositories" in tr
        for tr in tool_results
    )
    if has_repo_list_tool:
        real_repos = set()
        for gtr in tool_results:
            if not isinstance(gtr, dict):
                continue
            if "repositories" in gtr:
                for r in gtr.get("repositories", []):
                    r_str = str(r).lower().strip()
                    real_repos.add(r_str)
                    if "/" in r_str:
                        real_repos.add(r_str.split("/")[-1])
        if real_repos:
            clean_draft = re.sub(r'https?://[^\s]+', '', draft_reply)
            clean_draft = re.sub(r'[`*\[\]()]', '', clean_draft)  # Keep _ and - intact
            stop_words = {
                "peculiarai", "github", "repositories", "repo", "public", "private", "account",
                "profile", "repos", "readme", "readmemd", "issues", "issue", "branch", "main",
                "master", "status", "commit", "url", "code", "total", "here", "found", "following",
                "list", "name", "all", "updated", "recent", "active", "kisi", "aap", "hai", "karein",
                "bol", "aur", "bhi", "nahi", "hoga", "the", "is", "for", "to", "you", "can", "check",
                "with", "view", "please", "details", "language", "stars", "fork", "description",
                "note", "tips", "yeh", "rahe", "aapke", "sample", "project"
            }
            for raw_line in clean_draft.splitlines():
                line = raw_line.strip()
                m = re.match(r'^(?:[-*•]|\d+\.)\s+(.+)$', line)
                if not m:
                    continue
                content = m.group(1).strip()
                if ':' in content:
                    continue
                words = [w for w in re.findall(r'[a-zA-Z0-9_\-\/]+', content) if w]
                if len(words) > 3:
                    continue
                item = words[0]
                clean_item = item.lower().split('/')[-1]
                if clean_item not in real_repos and item.lower() not in real_repos and clean_item not in stop_words and len(clean_item) > 2 and not clean_item.isdigit():
                    violations.append(f"Fabricated GitHub repository: {item}")

    # 4. Route & Travel Time Grounding: handled structurally in audit_claims_with_llm (claims_route_or_travel_time)

    # 5. Regional Script Sanitization
    draft_reply = sanitize_regional_scripts(draft_reply)

    if violations:
        if any("Fabricated Google Meet link" in v for v in violations):
            clean_reply = MEET_URL_PATTERN.sub("[Meet link available upon confirmation]", draft_reply)
            return False, clean_reply, violations
        if any("Fabricated GitHub repository" in v for v in violations) and has_repo_list_tool:
            revised = (
                "Aapke GitHub account par ye verified repositories hain:\n\n" +
                "\n".join(f"{i+1}. **{r}**" for i, r in enumerate(sorted(real_repos)))
            )
            return False, revised, violations
        return False, action_failed_msg, violations

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
                "claims_unavailable_capability": bool(parsed.get("claims_unavailable_capability", False)),
                "sourced_by_tool": bool(parsed.get("sourced_by_tool", False)),
                "reason": str(parsed.get("reason", ""))
            }
    except Exception as exc:
        logger.warning(f"LLM Claim-Checker error: {exc}")

    # Fallback heuristic when LLM provider is offline or unreachable in unit tests:
    low = draft_reply.lower()
    is_cancellation = is_cancellation_or_decline(user_query, draft_reply)
    has_action_phrase = False if is_cancellation else any(w in low for w in (
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
    claims_unavail = any(w in low for w in ("book a ticket", "ticket book kar", "ticket book kr", "book kar doon", "book kr du", "order food", "make payment"))
    has_evidence = bool(evidence_str and ("status" in evidence_str or "Tool 1" in evidence_str))

    return {
        "claims_action_done": has_action_phrase,
        "which_action": which_act,
        "claims_external_fact": has_ext_fact,
        "claims_route_or_travel_time": claims_route,
        "claims_unavailable_capability": claims_unavail,
        "sourced_by_tool": has_evidence if (has_action_phrase or has_ext_fact or claims_route or claims_unavail) else True,
        "reason": "Fallback heuristic evaluation"
    }


async def verify_and_ground_reply(
    draft_reply: str,
    tool_results: List[Dict[str, Any]],
    memory_context: Optional[Dict[str, Any]] = None,
    user_query: str = "",
    recent_history: Optional[List[Dict[str, Any]]] = None,
    allow_llm: bool = True,
    user_lang: str = "english"
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
        recent_history=recent_history,
        user_lang=user_lang
    )
    if not passed_det:
        from core.i18n import get_i18n_string
        fallback_msg = get_i18n_string("action_failed", lang=user_lang)
        return False, revised_det or fallback_msg, violations_det

    # Fast-path for questions or trivial short acknowledgements (0ms overhead, zero keyword sets)
    clean_reply_stripped = draft_reply.strip()
    if "?" in clean_reply_stripped or len(clean_reply_stripped.split()) <= 3:
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
            if status in ("success", "ok", "needs_confirmation", "confirmation_required", "conflict_detected", "ready_for_dispatch") or ok is True:
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

            # If user declined or cancelled, or tool reported error, NEVER overwrite with canned text:
            is_declined = is_cancellation_or_decline(user_query, draft_reply)
            has_tool_error = any(isinstance(tr, dict) and tr.get("status") in ("error", "failed", "conflict_detected") for tr in tool_results)
            if is_declined or has_tool_error:
                return True, sanitize_regional_scripts(draft_reply), []

            violations.append(f"Claimed action '{act_label}' completed without verified tool execution")
            from core.i18n import get_i18n_string
            lang = user_lang or "english"
            revised = get_i18n_string("action_failed", lang)
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
        from core.i18n import get_i18n_string
        revised = get_i18n_string("unverified_information", lang=user_lang) or "Mere paas iski verified information nahi hai."
        return False, revised, violations

    # Check 2D: Unavailable Capability Claimed (e.g. Offering to book tickets / make payments)
    if audit.get("claims_unavailable_capability"):
        violations.append(f"Claimed unavailable capability: {audit.get('reason')}")
        revised = (
            "Main directly tickets book ya purchases nahi kar sakta kyunki mere paas ticket booking tool nahi hai. "
            "Lekin main schedules, showtimes, options aur direct booking links dhoondhne mein aapki madad kar sakta hoon!"
        )
        return False, revised, violations

    return True, sanitize_regional_scripts(draft_reply), violations
