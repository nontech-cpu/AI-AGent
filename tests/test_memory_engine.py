import pytest
from unittest.mock import patch
import os
from memory.store import (
    save_entity,
    find_entity,
    save_memory_item,
    search_memories_hybrid,
    get_commitments_for_user,
    update_commitment_status
)
from memory.retrieval import assemble_memory_context, extract_potential_entity_tokens

TEST_USER_ID = 999988

def test_extract_potential_entity_tokens():
    tokens = extract_potential_entity_tokens("bhai annaya ko kya bhejna tha")
    assert "annaya" in tokens
    assert "bhai" not in tokens
    assert "kya" not in tokens


def test_entity_creation_and_fuzzy_phonetic_lookup():
    # 1. Save entity
    eid = save_entity(
        user_id=TEST_USER_ID,
        name="Vikram Malhotra",
        entity_type="person",
        attributes={"company": "Apex Capital", "role_title": "Partner"}
    )
    assert eid is not None

    # 2. Exact match
    res1 = find_entity(TEST_USER_ID, "Vikram Malhotra")
    assert res1 is not None
    assert res1["name"] == "Vikram Malhotra"
    assert res1["attributes"]["company"] == "Apex Capital"

    # 3. First name alias match
    res2 = find_entity(TEST_USER_ID, "Vikram")
    assert res2 is not None
    assert res2["name"] == "Vikram Malhotra"

    # 4. Fast-typed Hinglish typo match "vikrm" or "vikramm"
    res3 = find_entity(TEST_USER_ID, "vikrm")
    assert res3 is not None
    assert res3["name"] == "Vikram Malhotra"


def test_hybrid_vector_and_trigram_search():
    # Save a specific commitment
    eid = save_entity(
        user_id=TEST_USER_ID,
        name="Pooja Verma",
        attributes={"company": "DesignStudio"}
    )
    mem_id = save_memory_item(
        user_id=TEST_USER_ID,
        content="Pooja ko UI wireframes bhejne hain Thursday tak",
        memory_type="commitment",
        subject_entity_id=eid
    )
    assert mem_id is not None

    # Hybrid search with query 'wireframes'
    matches = search_memories_hybrid(TEST_USER_ID, "wireframes", limit=3)
    assert len(matches) > 0
    top = matches[0]
    assert "wireframes" in top["content"]
    assert top["entity_name"] == "Pooja Verma"


def test_assemble_memory_context_integration():
    with patch("memory.retrieval.resolve_user_id", return_value=TEST_USER_ID):
        ctx = assemble_memory_context(123456, "Pooja ko kya bhejna tha?")
        assert ctx is not None
        assert "Pooja Verma" in ctx["formatted_block"]
        assert "UI wireframes" in ctx["formatted_block"]
