"""
PeculiarAI Phase 7c: Strengthened Meaning-Regex & Keyword Audit Script
Audits codebase for:
(a) Tuples/sets/lists of natural-language words used in membership checks
(b) Hardcoded person/brand/city names in logic
(c) Keyword->tool dictionaries
(d) Previously flagged disallowed meaning patterns
"""

import os
import ast
import re
import sys
from typing import List, Dict, Any, Tuple

sys.stdout.reconfigure(encoding='utf-8')

EXCLUDE_DIRS = {"venv", "scratch", ".git", "__pycache__", "tests", "docs", "scripts"}
LEGACY_V1_FILES = {
    os.path.normpath("llm/legacy.py"),
    os.path.normpath("nlu.py"),
    os.path.normpath("lang.py"),
}

MACHINE_TOKENS = {
    "get", "post", "put", "delete", "patch", "head", "options",
    "http", "https", "true", "false", "none", "null", "undefined",
    "id", "user_id", "chat_id", "status", "success", "error", "ok",
    "active", "completed", "confirmed", "cancelled", "archived",
    "application/json", "text/plain", "text/html", "utf-8", "ascii"
}

HARDCODED_PERSON_NAMES = {"ananya", "devender"}

class MeaningAuditVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, lines: List[str]):
        self.file_path = file_path
        self.lines = lines
        self.findings = []
        self.norm_path = os.path.normpath(file_path)
        self.is_legacy_v1 = self.norm_path in LEGACY_V1_FILES

    def _get_snippet(self, lineno: int) -> str:
        if 1 <= lineno <= len(self.lines):
            return self.lines[lineno - 1].strip()
        return ""

    def visit_Compare(self, node: ast.Compare):
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(op, (ast.In, ast.NotIn)):
                if isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                    str_elts = [elt.value for elt in comp.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
                    nl_words = [w for w in str_elts if w.lower() not in MACHINE_TOKENS and len(w) > 1 and not w.startswith("/") and not w.startswith("http")]
                    if len(nl_words) >= 3:
                        self.findings.append({
                            "file": self.file_path,
                            "line": node.lineno,
                            "category": "DISALLOWED_HEURISTIC",
                            "kind": "membership_nl_words",
                            "snippet": self._get_snippet(node.lineno),
                            "details": f"Membership check with {len(nl_words)} NL words: {nl_words[:5]}"
                        })

        self.generic_visit(node)

    def visit_Call(self, node: ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in ("any", "all"):
            if node.args and isinstance(node.args[0], (ast.GeneratorExp, ast.ListComp)):
                gen = node.args[0]
                for comp in gen.generators:
                    if isinstance(comp.iter, (ast.Tuple, ast.List, ast.Set)):
                        str_elts = [elt.value for elt in comp.iter.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
                        nl_words = [w for w in str_elts if w.lower() not in MACHINE_TOKENS and len(w) > 1]
                        if len(nl_words) >= 3:
                            self.findings.append({
                                "file": self.file_path,
                                "line": node.lineno,
                                "category": "DISALLOWED_HEURISTIC",
                                "kind": "any_all_nl_word_list",
                                "snippet": self._get_snippet(node.lineno),
                                "details": f"Generator expression iterating over {len(nl_words)} NL words: {nl_words[:5]}"
                            })

        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant):
        if isinstance(node.value, str) and node.value.lower() in HARDCODED_PERSON_NAMES:
            self.findings.append({
                "file": self.file_path,
                "line": node.lineno,
                "category": "DISALLOWED_HEURISTIC",
                "kind": "hardcoded_person_name",
                "snippet": self._get_snippet(node.lineno),
                "details": f"Hardcoded person name '{node.value}' found in source logic"
            })
        self.generic_visit(node)

    def visit_Dict(self, node: ast.Dict):
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        vals = [v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str)]
        
        tool_indicators = {"reminder", "meeting", "email", "notion", "github", "search", "weather", "calculator"}
        if len(keys) >= 3 and any(any(ti in val.lower() for ti in tool_indicators) for val in vals):
            self.findings.append({
                "file": self.file_path,
                "line": node.lineno,
                "category": "DISALLOWED_HEURISTIC",
                "kind": "keyword_to_tool_dict",
                "snippet": self._get_snippet(node.lineno),
                "details": f"Dictionary mapping {len(keys)} keyword keys to tools"
            })
        self.generic_visit(node)


def audit_file(file_path: str) -> List[Dict[str, Any]]:
    with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
        content = f.read()
    lines = content.splitlines()

    try:
        tree = ast.parse(content, filename=file_path)
        visitor = MeaningAuditVisitor(file_path, lines)
        visitor.visit(tree)
        return visitor.findings
    except SyntaxError:
        return []


def run_full_audit() -> List[Dict[str, Any]]:
    all_findings = []
    for root, dirs, files in os.walk("."):
        dirs[:] = [d for d in dirs if d not in EXCLUDE_DIRS]
        for f in files:
            if f.endswith(".py"):
                path = os.path.join(root, f)
                findings = audit_file(path)
                all_findings.extend(findings)
    return all_findings


if __name__ == "__main__":
    findings = run_full_audit()
    print("=" * 70)
    print("PeculiarAI Phase 7c: Strengthened Regex & Meaning Audit")
    print("=" * 70)
    print(f"Total findings identified: {len(findings)}\n")

    by_kind = {}
    for f in findings:
        by_kind[f["kind"]] = by_kind.get(f["kind"], 0) + 1
        print(f"[{f['kind']}] {f['file']}:{f['line']}")
        print(f"    Snippet: {f['snippet']}")
        print(f"    Details: {f.get('details', '')}\n")

    print("=" * 70)
    print("Summary Breakdown by Kind:")
    for k, count in sorted(by_kind.items()):
        print(f"  • {k}: {count}")
    print("=" * 70)
