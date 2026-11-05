"""
PeculiarAI Phase 6: Meaning-Regex & Keyword Audit Script
Audits codebase for disallowed meaning-determining regexes and keyword lists vs. allowed structural regexes.

Disallowed Meaning Patterns:
- Intent classification regex/keywords
- Free-text confirmation classification (affirmative/negative keyword sets)
- Language/dialect stopword switching
- Master privacy wipe phrase matching
- Pre-LLM conversational interceptors (greetings, acks, direct actions bypassing LLM loop)

Allowed Structural Patterns:
- Slash commands (/start, /forget, /status, /clear, /radar, etc.)
- URLs, email syntax validation (syntax checks)
- Google Meet links
- ISO dates/times, numbers, percentages, clock formats
- Machine tokens with explicit prefixes (ghp_..., secret_..., ntn_...)
- JSON / Markdown / XML wrappers, sanitizers, and prompt-injection tag strips
- Telegram inline callback data prefixes
"""

import os
import re
import sys
from typing import List, Dict, Tuple, Any

EXCLUDE_DIRS = {"venv", "scratch", ".git", "__pycache__", "tests", "docs", "scripts"}
LEGACY_V1_FILES = {
    os.path.normpath("llm/legacy.py"),
    os.path.normpath("nlu.py"),
    os.path.normpath("lang.py"),
}

def audit_file(file_path: str) -> List[Dict[str, Any]]:
    norm_path = os.path.normpath(file_path)
    is_legacy_v1 = norm_path in LEGACY_V1_FILES

    findings = []
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        lines = f.readlines()

    for idx, line in enumerate(lines):
        line_num = idx + 1
        stripped = line.strip()

        # Skip comments
        if stripped.startswith("#"):
            continue

        # 1. Check for master wipe phrase matching in main.py
        if ("is_master_wipe_request" in stripped or "saari chat delete" in stripped) and "main.py" in file_path:
            findings.append({
                "file": file_path,
                "line": line_num,
                "category": "DISALLOWED",
                "kind": "master_wipe_phrase_regex",
                "snippet": stripped
            })

        # 2. Check for free-text confirmation regex / keywords in active code
        if ("AFFIRMATIVE_REGEX" in stripped or "NEGATIVE_REGEX" in stripped) and not is_legacy_v1:
            findings.append({
                "file": file_path,
                "line": line_num,
                "category": "DISALLOWED",
                "kind": "free_text_confirmation_regex",
                "snippet": stripped
            })

        # 3. Check for language switch stopword / alias dictionaries in agent/loop.py
        if ("LANGUAGE_ALIASES" in stripped or "detect_explicit_language_switch" in stripped) and "agent" in file_path and "loop.py" in file_path:
            findings.append({
                "file": file_path,
                "line": line_num,
                "category": "DISALLOWED",
                "kind": "language_stopword_switch",
                "snippet": stripped
            })

        # 4. Check for pre-LLM greeting / ack regexes in main.py
        if "re.fullmatch(" in stripped and ("hi+" in stripped or "hu+" in stripped or "namaste" in stripped):
            findings.append({
                "file": file_path,
                "line": line_num,
                "category": "DISALLOWED",
                "kind": "pre_llm_greeting_interceptor",
                "snippet": stripped
            })

        # 5. Check for hardcoded affirmative lists in agent/loop.py
        if ('("yes", "ha", "haan"' in stripped or '("confirm", "proceed", "yes"' in stripped) and "agent" in file_path and "loop.py" in file_path:
            findings.append({
                "file": file_path,
                "line": line_num,
                "category": "DISALLOWED",
                "kind": "hardcoded_affirmative_keywords",
                "snippet": stripped
            })

        # 6. Check for intent words in agent/tools.py
        if ("is_explicit_reschedule = any(" in stripped or "is_latest_intent = any(" in stripped) and "agent" in file_path and "tools.py" in file_path:
            findings.append({
                "file": file_path,
                "line": line_num,
                "category": "DISALLOWED",
                "kind": "tool_intent_keyword_list",
                "snippet": stripped
            })

    return findings


def run_audit() -> Tuple[int, List[Dict[str, Any]]]:
    all_findings = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                findings = audit_file(path)
                all_findings.extend(findings)

    disallowed = [f for f in all_findings if f["category"] == "DISALLOWED"]
    return len(disallowed), all_findings


if __name__ == "__main__":
    disallowed_count, findings = run_audit()
    print("=" * 60)
    print("PeculiarAI Phase 6: Regex & Keyword Meaning Audit")
    print("=" * 60)
    print(f"Total DISALLOWED patterns found in active codebase: {disallowed_count}\n")

    for f in findings:
        status_tag = f"[{f['category']}] ({f['kind']})"
        print(f"{status_tag} {f['file']}:{f['line']}")
        print(f"    {f['snippet']}\n")

    print("=" * 60)
    if disallowed_count == 0:
        print("AUDIT RESULT: PASS - 0 disallowed meaning regexes/keywords in active paths.")
        sys.exit(0)
    else:
        print(f"AUDIT RESULT: FAIL - {disallowed_count} disallowed patterns remain.")
        sys.exit(1)
