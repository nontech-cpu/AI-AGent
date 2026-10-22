"""
PeculiarAI Cognitive Memory Package (Phase 2)
Layered Cognitive Memory Architecture:
- L1 Working Memory (Active turn & state)
- L2 Rolling Summary Memory
- L3 Episodic Memory (Searchable via pgvector + pg_trgm hybrid retrieval)
- L4 Semantic Facts (ChatGPT style user preferences & details)
- L5 Entity Knowledge Graph (People, companies, projects with phonetic typo-tolerant aliases)
"""
from memory.store import (
    save_entity,
    find_entity,
    list_entities,
    save_memory_item,
    search_memories_hybrid,
    get_commitments_for_user,
    save_user_semantic_fact,
    get_user_semantic_facts
)
from memory.retrieval import assemble_memory_context

__all__ = [
    "save_entity",
    "find_entity",
    "list_entities",
    "save_memory_item",
    "search_memories_hybrid",
    "get_commitments_for_user",
    "save_user_semantic_fact",
    "get_user_semantic_facts",
    "assemble_memory_context"
]
