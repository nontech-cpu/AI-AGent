"""
PeculiarAI Background Memory Extractor (Phase 2)
Continuous cognitive extraction: analyzes user turns and assistant actions in background
to extract new entities, promises/commitments, facts, and mark completed tasks.
"""
import os
import json
import logging
import asyncio
import httpx
from typing import Optional, Dict, Any, List

import db
from memory.store import (
    save_entity,
    save_memory_item,
    find_entity,
    update_commitment_status
)

logger = logging.getLogger("peculiar_extractor")

EXTRACTION_SYSTEM_PROMPT = """You are a precise cognitive memory extractor for PeculiarAI personal assistant.
Analyze the following conversation turn between User and Assistant.
Extract any NEW facts, commitments (promises/deliverables to someone), entities (people, companies), or completed commitments.

Output ONLY valid JSON with this schema:
{
  "entities": [
    {
      "name": "Full Name or Organization",
      "entity_type": "person" or "organization" or "project",
      "company": null or "Company Name",
      "role_title": null or "Role Title",
      "email": null or "email@example.com",
      "phone": null or "phone number"
    }
  ],
  "commitments": [
    {
      "entity_name": "Name of contact commitment is made to",
      "commitment": "Exact description of promise or deliverable (e.g. 'Ananya ko module bhejna hai Tuesday ko')",
      "due_date": null or "YYYY-MM-DD"
    }
  ],
  "facts": [
    {
      "fact": "Life habit, preference, or fact about user",
      "category": "preference" or "personal" or "work"
    }
  ],
  "completed_commitments": [
    "entity_name or description of commitment fulfilled"
  ]
}

If nothing new was stated or promised, return empty lists.
"""

async def extract_and_persist_turn(chat_id: int, user_text: str, assistant_reply: str):
    """
    Executes in background task. Calls Groq with small JSON extraction prompt
    and persists discoveries into Entities and Memories.
    """
    try:
        from memory.store import resolve_user_id
        user_id = resolve_user_id(chat_id)
        groq_key = os.getenv("GROQ_API_KEY")
        if not groq_key:
            return

        # Skip trivial greetings or confirmations
        clean_user = user_text.strip().lower()
        if clean_user in {"hi", "hello", "hey", "yes", "no", "haan", "ok", "theek hai"}:
            return

        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                json={
                    "model": os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
                    "messages": [
                        {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                        {
                            "role": "user",
                            "content": f"User: {user_text}\nAssistant: {assistant_reply}"
                        }
                    ],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.1
                }
            )

            if resp.status_code != 200:
                logger.warning(f"Memory extraction Groq error: {resp.status_code} {resp.text[:100]}")
                return

            data = resp.json()["choices"][0]["message"]["content"]
            parsed = json.loads(data)

            # 1. Persist Entities
            entity_id_map = {}
            for ent in parsed.get("entities", []):
                name = ent.get("name")
                if not name or len(name) < 2:
                    continue
                attrs = {}
                for k in ["company", "role_title", "email", "phone"]:
                    if ent.get(k):
                        attrs[k] = ent[k]
                eid = save_entity(
                    user_id=user_id,
                    name=name,
                    entity_type=ent.get("entity_type", "person"),
                    attributes=attrs
                )
                entity_id_map[name.lower()] = eid

            # 2. Persist Commitments
            for comm in parsed.get("commitments", []):
                comm_text = comm.get("commitment")
                if not comm_text:
                    continue
                ent_name = comm.get("entity_name")
                eid = None
                if ent_name:
                    eid = entity_id_map.get(ent_name.lower())
                    if not eid:
                        existing = find_entity(user_id, ent_name)
                        eid = existing["id"] if existing else save_entity(user_id, ent_name)

                save_memory_item(
                    user_id=user_id,
                    content=comm_text,
                    memory_type="commitment",
                    subject_entity_id=eid,
                    structured={"due_date": comm.get("due_date")}
                )

            # 3. Persist Facts
            for fact_item in parsed.get("facts", []):
                fact_str = fact_item.get("fact")
                if fact_str:
                    save_memory_item(
                        user_id=user_id,
                        content=fact_str,
                        memory_type="semantic_fact",
                        structured={"category": fact_item.get("category", "general")}
                    )

    except Exception as exc:
        logger.error(f"Error in extract_and_persist_turn: {repr(exc)}")
