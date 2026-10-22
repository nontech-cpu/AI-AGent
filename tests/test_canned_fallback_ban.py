"""
PeculiarAI — Canned Fallback Ban Lint Test
Ensures hardcoded generic phrases ('main sun raha hoon', 'aap bataiye kya help chahiye')
never appear anywhere in the codebase outside authorized initial greeting handlers.
"""
import os
import re
import pytest

BANNED_PHRASES = [
    "main sun raha hoon",
    "aap bataiye kya help chahiye"
]

ALLOWED_FILES = {
    # Only the initial Telegram /start greeting handler may welcome the user
    os.path.normpath("tests/test_canned_fallback_ban.py"),
}

def test_no_canned_fallback_in_agent_code():
    root_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    target_dirs = ["agent", "connectors"]

    offending_occurrences = []

    for tdir in target_dirs:
        full_dir = os.path.join(root_dir, tdir)
        if not os.path.exists(full_dir):
            continue
        for root, _, files in os.walk(full_dir):
            for file in files:
                if not file.endswith(".py"):
                    continue
                file_path = os.path.join(root, file)
                rel_path = os.path.normpath(os.path.relpath(file_path, root_dir))
                if rel_path in ALLOWED_FILES:
                    continue

                with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()

                for phrase in BANNED_PHRASES:
                    if phrase.lower() in content.lower():
                        offending_occurrences.append(f"{rel_path}: contains banned canned phrase '{phrase}'")

    assert len(offending_occurrences) == 0, f"Found banned canned fallback replies in agent/connector code:\n" + "\n".join(offending_occurrences)
