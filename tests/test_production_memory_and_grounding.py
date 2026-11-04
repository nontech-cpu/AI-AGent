import pytest
import os
import json
from unittest.mock import patch, AsyncMock
from memory.store import (
    save_project_context,
    search_project_memory,
    get_active_projects,
    consolidate_memory_item,
    save_working_context,
    get_working_context
)
from memory.retrieval import assemble_memory_context


def test_project_memory_lifecycle():
    """Phase 2: Project-based memory saves and retrieves distinct projects."""
    test_uid = 999901
    pid = save_project_context(
        user_id=test_uid,
        name="CCTV Factory Guard",
        objective="Industrial defect monitoring",
        tech_stack="Python, OpenCV, PostgreSQL",
        architecture="Edge RTSP cameras streaming to FastAPI inference engine",
        decisions="Selected PostgreSQL + pgvector for vector storage"
    )
    assert pid > 0

    # Search project
    prjs = search_project_memory(test_uid, "CCTV")
    assert len(prjs) >= 1
    assert prjs[0]["name"] == "CCTV Factory Guard"
    assert "OpenCV" in prjs[0]["tech_stack"]


def test_working_context_persistence():
    """Phase 1.5: Working context survives server restarts."""
    test_cid = 999902
    save_working_context(
        chat_id=test_cid,
        active_project="CCTV Factory Guard",
        current_topic="Camera Calibration",
        current_task="Fix RTSP frame dropped packets",
        pending_action={"kind": "meeting", "title": "Review Stream"}
    )

    ctx = get_working_context(test_cid)
    assert ctx["active_project"] == "CCTV Factory Guard"
    assert ctx["current_topic"] == "Camera Calibration"
    assert ctx["current_task"] == "Fix RTSP frame dropped packets"
    assert ctx["pending_action"]["title"] == "Review Stream"


def test_memory_consolidation_add_update_noop():
    """Phase 1.3: Intelligent consolidation ADD, UPDATE, and NOOP."""
    test_uid = 999903

    # 1. NOOP
    res_noop = consolidate_memory_item(test_uid, "NOOP", "hello bhai")
    assert res_noop["status"] == "noop"

    # 2. ADD
    res_add = consolidate_memory_item(test_uid, "ADD", "User lives in Jodhpur near Shastri Nagar", category="personal")
    assert res_add["status"] == "added"
    mem_id = res_add["id"]

    # 3. Duplicate ADD
    res_dupe = consolidate_memory_item(test_uid, "ADD", "User lives in Jodhpur near Shastri Nagar", category="personal")
    assert res_dupe["status"] == "duplicate_skipped"

    # 4. UPDATE
    res_upd = consolidate_memory_item(test_uid, "UPDATE", "User moved residence to Ratanada, Jodhpur", category="personal")
    assert res_upd["status"] in ("added", "updated")


def test_route_and_places_grounding_guards():
    """Phase 3 & 4: Grounding verifier bans fake Mohan Mall and NH-48 local highway."""
    from agent.verifier import deterministic_verify

    # Test Fake Malls Interception
    draft_mall = "Jodhpur ke malls hain: Mohan Mall, Madhav Mall, Jodhpur Central Mall."
    passed, revised, violations = deterministic_verify(
        draft_reply=draft_mall,
        tool_results=[],
        user_query="jodhpur mall konse konse h"
    )
    assert passed is False
    assert any("Fabricated mall names" in v for v in violations)
    assert "Ashapurna Mall" in revised or "Reliance Smart" in revised

    # Test Fake Highway Route Interception
    draft_route = "Sardarpura se Ratanada ke liye best route NH-48 hai, total distance 40 km aur 50 min lagenge."
    passed_r, revised_r, violations_r = deterministic_verify(
        draft_reply=draft_route,
        tool_results=[],
        user_query="sardarpura se ratanada kaise jaye"
    )
    assert passed_r is False
    assert any("Fabricated highway" in v for v in violations_r)
    assert "3–4 km" in revised_r
    assert "google.com/maps" in revised_r

