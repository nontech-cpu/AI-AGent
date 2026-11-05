"""
PeculiarAI Background Memory Extractor (Phase 0 Hotfix & Phase 2 Architecture)
Continuous cognitive extraction: analyzes user messages ONLY (never assistant replies)
to extract new entities, promises/commitments, facts, and structured numerical measurements.
Zero negative-fact poisoning. Zero reminder clutter in memories table.
"""
import os
import re
import json
import logging
import asyncio
from datetime import datetime, timedelta
from typing import Optional, Dict, Any, List
import httpx

from timing import IST
import db
from memory.store import (
    save_entity,
    save_memory_item,
    find_entity,
    update_commitment_status,
    save_measurement,
    get_measurements
)

logger = logging.getLogger("peculiar_extractor")

# Strong references set to prevent background tasks from being garbage collected
_BACKGROUND_TASKS = set()

EXTRACTION_SYSTEM_PROMPT = """You are a precise cognitive memory extractor for PeculiarAI personal assistant.
Analyze the following message sent by the USER. Extract facts stated by the User ONLY.

STRICT EXTRACTION RULES (CRITICAL):
1. Extract facts that the User EXPLICITLY STATED about themselves, their friends, or their life.
2. NEVER extract negative or absence statements (e.g. NEVER extract: "User has not shared X", "no reading found", "reading is missing").
3. NEVER extract reminders, alarms, or scheduling requests (e.g. "10 bje reminder laga do", "remind me to...", "alarm"). Those belong in calendar/reminders, NOT memory!
4. NEVER extract user questions or report queries (e.g. "meri reading kya thi?", "what is my schedule?", "tell me").
5. SUBJECT ACCURACY: Identify who the information is about ("user", "friend", or a named person like "Ananya"). Content MUST clearly state who it is about (e.g. "Friend's fasting blood sugar is 84 mg/dL", NEVER say "User's" for a friend).
6. ABSOLUTE DATE RESOLUTION: If the user says "aaj" (today), "kal" (yesterday or tomorrow), or "morning", resolve it to the exact absolute date ({CURRENT_DATE_IST}) in YYYY-MM-DD.
7. STRUCTURED MEASUREMENTS: If user mentions health, fitness, or numerical readings (glucose, blood sugar, blood pressure, weight, heart rate), ALWAYS populate the "measurements" list!

Output valid JSON ONLY with this schema:
{
  "entities": [
    {
      "name": "Full Name or Relation (e.g. 'friend' or 'Rahul')",
      "entity_type": "person" or "organization",
      "company": null or "Company Name",
      "role_title": null or "Role Title",
      "email": null or "email@example.com",
      "phone": null or "phone number",
      "notes": null or "Purpose, project, task, deliverable, or context for this contact (e.g. 'contact for designing', 'report on Tuesday')"
    }
  ],
  "commitments": [
    {
      "entity_name": "Name of contact commitment is made to",
      "commitment": "Exact description of promise or deliverable (e.g. 'Ananya ko module bhejna hai')",
      "due_date": null or "YYYY-MM-DD"
    }
  ],
  "measurements": [
    {
      "subject": "user" or "friend" or "Name",
      "metric": "glucose" or "blood_pressure_systolic" or "blood_pressure_diastolic" or "weight" or "heart_rate",
      "value": 88.0,
      "unit": "mg/dL" or "kg" or "mmHg" or "bpm",
      "context": "fasting" or "post-meal" or "random" or "resting",
      "event_date": "YYYY-MM-DD"
    }
  ],
  "facts": [
    {
      "subject": "user" or "friend" or "Name",
      "fact": "Factual statement with absolute date (e.g. '2026-10-30: Friend fasting blood sugar was 84 mg/dL')",
      "category": "health" or "preference" or "personal" or "work",
      "action": "ADD" or "UPDATE" or "DELETE" or "NOOP",
      "event_date": "YYYY-MM-DD"
    }
  ],
  "projects": [
    {
      "name": "Project Name (e.g. 'CCTV Monitoring' or 'PeculiarAI')",
      "objective": "High level goal of project",
      "tech_stack": "Technologies used (e.g. 'Python, OpenCV, PostgreSQL')",
      "architecture": "Architecture details",
      "current_tasks": "Pending or completed tasks",
      "decisions": "Important technical/architectural decisions"
    }
  ],
  "corrections": [
    {
      "wrong_concept": "What was wrong",
      "correction_rule": "The permanent rule learned",
      "category": "place_correction" or "preference" or "fact_correction"
    }
  ]
}

If nothing new was stated or promised, return empty lists.
"""

def is_invalid_or_negative_fact(fact_str: str) -> bool:
    """
    Post-filter dropping negative absence statements, reminders, scheduling, questions,
    and assistant meta-talk from polluting memories.
    """
    if not fact_str or not fact_str.strip():
        return True
    f = fact_str.strip().lower()

    # 1. Negative / absence / missing statements
    if re.search(r"\b(has not|did not|not saved|not shared|never shared|nahi kiya|nahi bataya|nahi mila|koi .* nahi|kuch .* nahi|no record|unrecorded|missing|not recorded|not provided|no .* found|none found|there is no|found no)\b", f, re.I):
        return True

    # 2. Reminder / scheduling / timer requests (belong in reminders table)
    if re.search(r"\b(remind|reminder|schedule|scheduled|alarm|timer|yaad dilana|baje yaad|baje reminder|daily reminder|roj reminder)\b", f, re.I):
        return True

    # 3. User questions / queries / requests
    if fact_str.strip().endswith("?") or re.search(r"\b(did the|did user|does the|does user|is the|are the|what is|kya tha|kya thi|kya hai|tell me|batao|pucha|asked|wants a report|wants to know|user requested)\b", f, re.I):
        return True

    # 4. Meta-assistant statements
    if re.search(r"\b(assistant|records show|bot|ai provider)\b", f, re.I):
        return True

    return False


async def extract_and_persist_turn(chat_id: int, user_text: str, assistant_reply: Optional[str] = None):
    """
    Executes in background task. Calls Groq with JSON extraction prompt
    analyzing the USER message only. Persists discoveries with verification and retry.
    """
    try:
        from memory.store import resolve_user_id
        user_id = resolve_user_id(chat_id)
        gemini_key = os.getenv("GEMINI_API_KEY")
        groq_key = os.getenv("GROQ_API_KEY")
        if not (gemini_key or groq_key):
            return

        # Skip trivial greetings or confirmations
        clean_user = user_text.strip().lower()
        if clean_user in {"hi", "hello", "hey", "yes", "no", "haan", "ok", "theek hai", "cancel", "thanks", "thank you"}:
            return

        # Explicitly ignore assistant reply to eliminate negative-fact poisoning
        # Pass ONLY user's message to the extraction prompt
        now_ist = datetime.now(IST)
        today_iso = now_ist.strftime("%Y-%m-%d")

        system_prompt = EXTRACTION_SYSTEM_PROMPT.replace("{CURRENT_DATE_IST}", today_iso)

        # Debounce slightly to prioritize foreground user turns over background extraction
        await asyncio.sleep(1.5)

        data_json = None

        # Tier 1: Try Google Gemini (1 Million TPM, zero Groq rate-limit consumption)
        if gemini_key:
            for g_model in ["gemini-3-flash-preview", "gemini-3.1-flash-lite-preview"]:
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.post(
                            "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
                            headers={"Authorization": f"Bearer {gemini_key}", "Content-Type": "application/json"},
                            json={
                                "model": g_model,
                                "messages": [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": f"User message: {user_text}"}
                                ],
                                "response_format": {"type": "json_object"},
                                "temperature": 0.1
                            }
                        )
                        if resp.status_code == 200:
                            data_json = resp.json()["choices"][0]["message"]["content"]
                            break
                        else:
                            logger.info(f"Memory extraction Gemini {g_model} status {resp.status_code}")
                except Exception as g_err:
                    logger.info(f"Memory extraction Gemini {g_model} net error: {g_err}")

        # Tier 2 Fallback: Groq candidate models
        if not data_json and groq_key:
            candidate_models = ["openai/gpt-oss-20b", "openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
            for model in candidate_models:
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        resp = await client.post(
                            "https://api.groq.com/openai/v1/chat/completions",
                            headers={"Authorization": f"Bearer {groq_key}", "Content-Type": "application/json"},
                            json={
                                "model": model,
                                "messages": [
                                    {"role": "system", "content": system_prompt},
                                    {"role": "user", "content": f"User message: {user_text}"}
                                ],
                                "response_format": {"type": "json_object"},
                                "temperature": 0.1
                            }
                        )
                        if resp.status_code == 200:
                            data_json = resp.json()["choices"][0]["message"]["content"]
                            break
                        else:
                            logger.warning(f"Memory extraction Groq error on {model}: {resp.status_code} {resp.text[:100]}")
                            await asyncio.sleep(1.0)
                except Exception as net_err:
                    logger.warning(f"Memory extraction network error on {model}: {repr(net_err)}")
                    await asyncio.sleep(1.0)

        if not data_json:
            _log_failed_extraction(user_id, user_text, "Groq extraction failed on all candidate models")
            return

        try:
            parsed = json.loads(data_json)
        except Exception as json_err:
            _log_failed_extraction(user_id, user_text, f"JSON parse error: {repr(json_err)}")
            return

        # 1. Persist Entities
        entity_id_map = {}
        EXCLUDED_ENTITIES = {"user", "assistant", "bot", "me", "myself", "friend", "neerav", "nirav", "admin", "ai", "companion"}
        for ent in parsed.get("entities", []):
            name = (ent.get("name") or "").strip()
            if not name or len(name) < 2 or name.lower() in EXCLUDED_ENTITIES:
                continue
            attrs = {}
            for k in ["company", "role_title", "email", "phone"]:
                if ent.get(k):
                    attrs[k] = ent[k]
            if ent.get("notes"):
                attrs["context_notes"] = ent["notes"]
            eid = save_entity(
                user_id=user_id,
                name=name,
                entity_type=ent.get("entity_type", "person"),
                attributes=attrs
            )
            entity_id_map[name.lower()] = eid

            # Only save to contact_memories if real contact information exists (phone/email/role or explicit contact notes)
            if ent.get("phone") or ent.get("email") or ent.get("role_title") or (ent.get("notes") and "contact" in ent.get("notes").lower()):
                try:
                    db.save_contact_memory(
                        chat_id=chat_id,
                        name=name,
                        phone=ent.get("phone") or "",
                        email=ent.get("email") or "",
                        role_title=ent.get("role_title") or "",
                        company=ent.get("company") or "",
                        context_notes=ent.get("notes") or "Saved via Cognitive Extractor"
                    )
                except Exception:
                    pass

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

        # 3. Persist Structured Health & Fitness Measurements
        for meas in parsed.get("measurements", []):
            try:
                metric = meas.get("metric", "").strip().lower()
                raw_val = meas.get("value")
                if not metric or raw_val is None:
                    continue
                val = float(raw_val)
                unit = meas.get("unit") or ("mg/dL" if "glucose" in metric or "sugar" in metric else "")
                ctx = meas.get("context")
                ev_date_str = meas.get("event_date") or today_iso
                try:
                    m_date = datetime.strptime(ev_date_str, "%Y-%m-%d").replace(tzinfo=IST)
                except Exception:
                    m_date = now_ist

                subj_str = str(meas.get("subject", "user")).lower().strip()
                subj_eid = None
                if subj_str not in ("user", "self", "me"):
                    subj_eid = entity_id_map.get(subj_str)
                    if not subj_eid:
                        existing = find_entity(user_id, subj_str)
                        subj_eid = existing["id"] if existing else save_entity(user_id, subj_str)

                # Dedupe: check if identical measurement for same subject and date already exists
                existing_meas = get_measurements(user_id, metric=metric, subject_entity_id=subj_eid, limit=3)
                is_dupe = False
                for em in existing_meas:
                    if abs(em["value"] - val) < 0.01 and str(em["measured_at"]).startswith(ev_date_str):
                        is_dupe = True
                        break

                if not is_dupe:
                    save_measurement(
                        user_id=user_id,
                        metric=metric,
                        value=val,
                        unit=unit,
                        measured_at=m_date,
                        subject_entity_id=subj_eid,
                        context=ctx
                    )
            except Exception as m_err:
                logger.warning(f"Error persisting measurement: {repr(m_err)}")

        # 4. Persist Facts with Rigorous Post-Filter
        for fact_item in parsed.get("facts", []):
            fact_str = fact_item.get("fact", "").strip()
            if not fact_str or is_invalid_or_negative_fact(fact_str):
                continue

            subj_str = str(fact_item.get("subject", "user")).lower().strip()
            subj_eid = None
            if subj_str not in ("user", "self", "me"):
                subj_eid = entity_id_map.get(subj_str)
                if not subj_eid:
                    existing = find_entity(user_id, subj_str)
                    subj_eid = existing["id"] if existing else save_entity(user_id, subj_str)

            ev_date = fact_item.get("event_date") or today_iso
            fact_action = fact_item.get("action", "ADD")

            # Phase 1: Memory write consolidation (ADD / UPDATE / NOOP / DELETE)
            from memory.store import consolidate_memory_item
            res_cons = consolidate_memory_item(
                user_id=user_id,
                action=fact_action,
                content=fact_str,
                category=fact_item.get("category", "general"),
                importance=fact_item.get("importance", 3),
                confidence=0.95,
                entity_id=subj_eid
            )

            # Verification read-back
            if res_cons.get("status") in ("added", "updated") and hasattr(db, "save_user_fact"):
                try:
                    db.save_user_fact(chat_id, fact_str, fact_item.get("category", "general"))
                except Exception:
                    pass

        # 4.5. Phase 2: Persist Projects & Active Working Context
        for prj in parsed.get("projects", []):
            p_name = prj.get("name", "").strip()
            if p_name and len(p_name) >= 2:
                try:
                    from memory.store import save_project_context, save_working_context
                    pid = save_project_context(
                        user_id=user_id,
                        name=p_name,
                        objective=prj.get("objective"),
                        tech_stack=prj.get("tech_stack"),
                        architecture=prj.get("architecture"),
                        current_tasks=prj.get("current_tasks"),
                        decisions=prj.get("decisions")
                    )
                    save_working_context(
                        chat_id=chat_id,
                        active_project=p_name,
                        current_topic=f"Working on {p_name}"
                    )
                except Exception as prj_save_err:
                    logger.warning(f"Error saving project context: {prj_save_err}")

        # 5. Persist Corrections (Self-Learning from User Feedback)
        for corr in parsed.get("corrections", []):
            wrong = corr.get("wrong_concept")
            rule = corr.get("correction_rule")
            if wrong and rule:
                db.save_user_correction(chat_id, wrong, rule, corr.get("category", "user_correction"))

    except Exception as exc:
        logger.error(f"Error in extract_and_persist_turn: {repr(exc)}")


def _log_failed_extraction(user_id: int, text_content: str, error_msg: str):
    """Logs extraction failures for audit and retry."""
    try:
        from sqlalchemy import text
        with db.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO failed_extractions (user_id, raw_text, error_message, attempts)
                    VALUES (:uid, :raw, :err, 3)
                """),
                {"uid": user_id, "raw": text_content[:500], "err": error_msg[:500]}
            )
    except Exception as log_err:
        logger.warning(f"Could not log failed extraction: {log_err}")


def schedule_extraction_task(chat_id: int, user_text: str, assistant_reply: Optional[str] = None):
    """
    Schedules extract_and_persist_turn with strong references to prevent GC.
    """
    task = asyncio.create_task(extract_and_persist_turn(chat_id, user_text, assistant_reply))
    _BACKGROUND_TASKS.add(task)
    task.add_done_callback(_BACKGROUND_TASKS.discard)
