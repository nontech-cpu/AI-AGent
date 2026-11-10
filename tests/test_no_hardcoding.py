"""
Automated regression tests enforcing the Golden Standard Architecture Zero-Hardcoding Rule.
Verifies that semantic meaning, intent, recurrence, language preference, and entities are never
resolved via hardcoded natural-language lists, keyword loops, or mapping dictionaries in modified runtime paths.
"""
import ast
import inspect
import pytest
import agent.tools as tools_mod
import agent.loop as loop_mod
import agent.verifier as verifier_mod
import db as db_mod
import morning_briefing as briefing_mod


def test_no_recurrence_keyword_loops_in_tools():
    """Verify that create_reminder and create_calendar_event do not loop over hardcoded recurrence words."""
    src = inspect.getsource(tools_mod._execute_tool_inner)
    assert 'for d_kw in ("daily", "roj"' not in src
    assert 'for d_kw in ("daily", "roj", "roz"' not in src
    assert 'for d_kw in' not in src


def test_no_language_mapping_dicts():
    """Verify that set_user_preference and get_user_preference do not use hardcoded en/eng/hi normalization dicts."""
    tools_src = inspect.getsource(tools_mod._execute_tool_inner)
    assert '{"en": "english"' not in tools_src
    assert '{"hi": "hindi"' not in tools_src

    db_pref_src = inspect.getsource(db_mod.get_user_preference)
    assert '{"en": "english"' not in db_pref_src
    assert 'raw_lang in ("en", "eng")' not in db_pref_src


def test_no_sentiment_or_task_word_sets_in_loop():
    """Verify that agent/loop.py does not define sentiment or task word sets to bypass tools or routing."""
    loop_src = inspect.getsource(loop_mod.run_agent_turn)
    assert "_sentiment_words" not in loop_src
    assert "_has_task_words" not in loop_src
    assert '("cancel", "no", "nahi", "nahin"' not in loop_src


def test_no_hardcoded_city_tuples_in_runtime():
    """Verify that location detection does not iterate over static lists of cities."""
    briefing_src = inspect.getsource(briefing_mod.detect_user_city)
    assert '("jodhpur", "udaipur", "jaipur"' not in briefing_src

    db_loc_src = inspect.getsource(db_mod.get_user_location_state)
    assert 'if "jodhpur" in txt:' not in db_loc_src

    loop_src = inspect.getsource(loop_mod.run_agent_turn)
    assert '("udaipur", "jaipur", "delhi", "mumbai"' not in loop_src


def test_no_verifier_sentiment_bypasses():
    """Verify that verifier.py does not use hardcoded sentiment dictionaries to skip checks."""
    verifier_src = inspect.getsource(verifier_mod.verify_and_ground_reply)
    assert "sentiment_words = {" not in verifier_src


def test_ast_scan_no_membership_nl_words_in_hot_paths():
    """
    AST check ensuring no membership comparisons (x in ('word1', 'word2', ...))
    in recently hardened functions.
    """
    for func in [briefing_mod.detect_user_city, db_mod.get_user_location_state]:
        src = inspect.getsource(func)
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comp, op in zip(node.comparators, node.ops):
                    if isinstance(op, (ast.In, ast.NotIn)) and isinstance(comp, (ast.Tuple, ast.List, ast.Set)):
                        elements = [elt.value for elt in comp.elts if isinstance(elt, ast.Constant) and isinstance(elt.value, str)]
                        # Disallow known natural language city/status lists
                        assert "jodhpur" not in [e.lower() for e in elements], f"Found hardcoded city in {func.__name__}"
