"""
PeculiarAI Cognitive Memory Store (Phase 2)
PostgreSQL-backed CRUD operations for Entities, Aliases, and Layered Memories.
Supports Hybrid Vector (pgvector) + Trigram (pg_trgm) retrieval with fastembed embeddings.
Includes transparent fallback and SQLite-compatibility for test suite isolation.
"""
import json
import logging
import difflib
import time
from typing import Optional, List, Dict, Any, Tuple
from sqlalchemy import text
import db

logger = logging.getLogger("peculiar_memory")

_EMBED_MODEL = None
_SQLITE_INIT_DONE = False

def _init_sqlite_if_needed():
    global _SQLITE_INIT_DONE
    if _SQLITE_INIT_DONE:
        return
    if db.engine.dialect.name == "sqlite":
        with db.engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    name TEXT NOT NULL,
                    entity_type TEXT DEFAULT 'person',
                    primary_alias TEXT,
                    attributes TEXT DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS entity_aliases (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_id INTEGER NOT NULL,
                    alias TEXT NOT NULL,
                    alias_clean TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    memory_type TEXT DEFAULT 'episodic',
                    subject_entity_id INTEGER,
                    content TEXT NOT NULL,
                    structured TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'active',
                    source_type TEXT DEFAULT 'chat',
                    embedding TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
    _SQLITE_INIT_DONE = True


def _get_embedding_model():
    """Lazy load FastEmbed embedding model (384-dimension BAAI/bge-small-en-v1.5)."""
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        try:
            from fastembed import TextEmbedding
            _EMBED_MODEL = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        except Exception as exc:
            logger.warning(f"FastEmbed load error (falling back to trigram search): {exc}")
            _EMBED_MODEL = False
    return _EMBED_MODEL if _EMBED_MODEL is not False else None


def embed_text(content: str) -> Optional[List[float]]:
    """Generates 384-dimensional dense vector embedding or returns None on error."""
    model = _get_embedding_model()
    if not model or not content or not content.strip():
        return None
    try:
        embeddings = list(model.embed([content.strip()]))
        if embeddings:
            return embeddings[0].tolist()
    except Exception as exc:
        logger.warning(f"embed_text error: {exc}")
    return None


_RESOLVED_USER_ID_CACHE: Dict[int, int] = {}

def resolve_user_id(chat_id_or_user_id: int) -> int:
    """Resolves telegram_chat_id or direct user_id into the internal users.id primary key."""
    if chat_id_or_user_id in _RESOLVED_USER_ID_CACHE:
        return _RESOLVED_USER_ID_CACHE[chat_id_or_user_id]

    _init_sqlite_if_needed()
    with db.engine.connect() as conn:
        try:
            row = conn.execute(
                text("SELECT id FROM users WHERE telegram_chat_id = :c"),
                {"c": chat_id_or_user_id}
            ).fetchone()
            if row:
                _RESOLVED_USER_ID_CACHE[chat_id_or_user_id] = row[0]
                return row[0]
            row2 = conn.execute(
                text("SELECT id FROM users WHERE id = :uid"),
                {"uid": chat_id_or_user_id}
            ).fetchone()
            if row2:
                _RESOLVED_USER_ID_CACHE[chat_id_or_user_id] = row2[0]
                return row2[0]
        except Exception:
            pass


    # Ensure user exists in users table to satisfy foreign key constraints
    try:
        db.ensure_user(chat_id_or_user_id)
        with db.engine.connect() as conn:
            row3 = conn.execute(
                text("SELECT id FROM users WHERE telegram_chat_id = :c"),
                {"c": chat_id_or_user_id}
            ).fetchone()
            if row3:
                return row3[0]
    except Exception:
        pass

    # If user table not present or empty, fallback to chat_id_or_user_id as user_id
    return chat_id_or_user_id


# ---------------------------------------------------------------------------
# L5: Entity Knowledge Graph & Aliases
# ---------------------------------------------------------------------------

def save_entity(
    user_id: int,
    name: str,
    entity_type: str = "person",
    primary_alias: Optional[str] = None,
    attributes: Optional[Dict[str, Any]] = None
) -> int:
    """
    Inserts or updates an entity (person, company, project) and registers default aliases.
    Returns the entity_id.
    """
    _init_sqlite_if_needed()
    clean_name = name.strip()
    attrs_json = json.dumps(attributes or {})
    p_alias = primary_alias.strip() if primary_alias else None
    is_pg = db.engine.dialect.name == "postgresql"

    with db.engine.connect() as conn:
        row = conn.execute(
            text("SELECT id, attributes FROM entities WHERE user_id = :uid AND lower(name) = lower(:name)"),
            {"uid": user_id, "name": clean_name}
        ).fetchone()

        if row:
            entity_id = row[0]
            raw_attrs = row[1]
            existing_attrs = json.loads(raw_attrs) if isinstance(raw_attrs, str) else (raw_attrs or {})
            if attributes:
                existing_attrs.update(attributes)

            if is_pg:
                conn.execute(
                    text("""
                        UPDATE entities
                        SET attributes = CAST(:attrs AS jsonb),
                            primary_alias = COALESCE(:p_alias, primary_alias),
                            entity_type = :entity_type,
                            updated_at = now()
                        WHERE id = :id
                    """),
                    {
                        "attrs": json.dumps(existing_attrs),
                        "p_alias": p_alias,
                        "entity_type": entity_type,
                        "id": entity_id
                    }
                )
            else:
                conn.execute(
                    text("""
                        UPDATE entities
                        SET attributes = :attrs,
                            primary_alias = COALESCE(:p_alias, primary_alias),
                            entity_type = :entity_type,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE id = :id
                    """),
                    {
                        "attrs": json.dumps(existing_attrs),
                        "p_alias": p_alias,
                        "entity_type": entity_type,
                        "id": entity_id
                    }
                )
        else:
            if is_pg:
                res = conn.execute(
                    text("""
                        INSERT INTO entities (user_id, name, entity_type, primary_alias, attributes)
                        VALUES (:uid, :name, :entity_type, :p_alias, CAST(:attrs AS jsonb))
                        RETURNING id
                    """),
                    {
                        "uid": user_id,
                        "name": clean_name,
                        "entity_type": entity_type,
                        "p_alias": p_alias,
                        "attrs": attrs_json
                    }
                )
                entity_id = res.fetchone()[0]
            else:
                conn.execute(
                    text("""
                        INSERT INTO entities (user_id, name, entity_type, primary_alias, attributes)
                        VALUES (:uid, :name, :entity_type, :p_alias, :attrs)
                    """),
                    {
                        "uid": user_id,
                        "name": clean_name,
                        "entity_type": entity_type,
                        "p_alias": p_alias,
                        "attrs": attrs_json
                    }
                )
                res = conn.execute(text("SELECT last_insert_rowid()"))
                entity_id = res.fetchone()[0]

        conn.commit()

    # Automatically register clean aliases
    add_entity_alias(entity_id, clean_name)
    if p_alias:
        add_entity_alias(entity_id, p_alias)

    parts = clean_name.split()
    if len(parts) > 1 and len(parts[0]) >= 3:
        add_entity_alias(entity_id, parts[0])

    invalidate_entity_cache(user_id)
    return entity_id


def add_entity_alias(entity_id: int, alias: str):
    """Adds a phonetic / typo-tolerant alias to an entity."""
    _init_sqlite_if_needed()
    clean_alias = alias.strip().lower()
    if not clean_alias:
        return

    is_pg = db.engine.dialect.name == "postgresql"
    with db.engine.connect() as conn:
        if is_pg:
            conn.execute(
                text("""
                    INSERT INTO entity_aliases (entity_id, alias, alias_clean)
                    VALUES (:eid, :alias, :clean)
                    ON CONFLICT (entity_id, alias_clean) DO NOTHING
                """),
                {"eid": entity_id, "alias": alias.strip(), "clean": clean_alias}
            )
        else:
            exists = conn.execute(
                text("SELECT id FROM entity_aliases WHERE entity_id = :eid AND alias_clean = :clean"),
                {"eid": entity_id, "clean": clean_alias}
            ).fetchone()
            if not exists:
                conn.execute(
                    text("INSERT INTO entity_aliases (entity_id, alias, alias_clean) VALUES (:eid, :alias, :clean)"),
                    {"eid": entity_id, "alias": alias.strip(), "clean": clean_alias}
                )
        conn.commit()


_USER_ENTITIES_CACHE: Dict[int, Any] = {}

def invalidate_entity_cache(user_id: int):
    _USER_ENTITIES_CACHE.pop(user_id, None)


def find_entity(user_id: int, query: str, threshold: float = 0.22) -> Optional[Dict[str, Any]]:
    """
    Finds an entity by exact match, prefix, trigram similarity, or difflib fallback.
    Cached in-memory per user for 45s for ultra-fast (sub-millisecond) retrieval.
    """
    _init_sqlite_if_needed()
    q_clean = query.strip().lower()
    if not q_clean:
        return None

    now_ts = time.time()
    cached = _USER_ENTITIES_CACHE.get(user_id)
    if not cached or (now_ts - cached["ts"] > 45.0):
        try:
            with db.engine.connect() as conn:
                all_records = conn.execute(
                    text("""
                        SELECT e.id, e.name, e.entity_type, e.primary_alias, e.attributes, a.alias_clean
                        FROM entities e
                        LEFT JOIN entity_aliases a ON a.entity_id = e.id
                        WHERE e.user_id = :uid
                    """),
                    {"uid": user_id}
                ).fetchall()
            _USER_ENTITIES_CACHE[user_id] = {"ts": now_ts, "records": all_records}
        except Exception as exc:
            print("find_entity cache fetch error:", repr(exc))
            all_records = cached.get("records", []) if cached else []
    else:
        all_records = cached["records"]

    if not all_records:
        return None

    # 1. Exact match on name, primary_alias, or alias_clean
    for r in all_records:
        name_l = (r[1] or "").lower()
        p_alias_l = (r[3] or "").lower()
        alias_l = (r[5] or "").lower()
        if q_clean in (name_l, p_alias_l, alias_l):
            raw_attrs = r[4]
            attrs = json.loads(raw_attrs) if isinstance(raw_attrs, str) else (raw_attrs or {})
            return {
                "id": r[0],
                "name": r[1],
                "entity_type": r[2],
                "primary_alias": r[3],
                "attributes": attrs,
                "match_similarity": 1.0
            }

    # 2. Fuzzy / Difflib match on all alias strings
    alias_dict = {}
    for r in all_records:
        if r[5]: alias_dict[r[5].lower()] = r
        if r[1]: alias_dict[r[1].lower()] = r
        if r[3]: alias_dict[r[3].lower()] = r

    close_matches = difflib.get_close_matches(q_clean, alias_dict.keys(), n=1, cutoff=0.68)
    if close_matches:
        best_rec = alias_dict[close_matches[0]]
        raw_attrs = best_rec[4]
        attrs = json.loads(raw_attrs) if isinstance(raw_attrs, str) else (raw_attrs or {})
        return {
            "id": best_rec[0],
            "name": best_rec[1],
            "entity_type": best_rec[2],
            "primary_alias": best_rec[3],
            "attributes": attrs,
            "match_similarity": 0.85
        }

    return None


def list_entities(user_id: int, entity_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """Returns all entities saved for a user."""
    _init_sqlite_if_needed()
    query_str = "SELECT id, name, entity_type, primary_alias, attributes FROM entities WHERE user_id = :uid"
    params = {"uid": user_id}
    if entity_type:
        query_str += " AND entity_type = :etype"
        params["etype"] = entity_type
    query_str += " ORDER BY name ASC"

    with db.engine.connect() as conn:
        rows = conn.execute(text(query_str), params).fetchall()
        entities = []
        for r in rows:
            raw_attrs = r[4]
            attrs = json.loads(raw_attrs) if isinstance(raw_attrs, str) else (raw_attrs or {})
            entities.append({
                "id": r[0],
                "name": r[1],
                "entity_type": r[2],
                "primary_alias": r[3],
                "attributes": attrs
            })
        return entities


# ---------------------------------------------------------------------------
# Unified Cognitive Memories (L1, L2, L3, L4, L5)
# ---------------------------------------------------------------------------

def save_memory_item(
    user_id: int,
    content: str,
    memory_type: str = "episodic",
    subject_entity_id: Optional[int] = None,
    structured: Optional[Dict[str, Any]] = None,
    status: str = "active",
    source_type: str = "chat"
) -> int:
    """
    Stores a memory item with an automatic 384-dim dense embedding.
    """
    _init_sqlite_if_needed()
    clean_content = content.strip()
    struct_json = json.dumps(structured or {})
    emb = embed_text(clean_content)
    is_pg = db.engine.dialect.name == "postgresql"

    with db.engine.connect() as conn:
        if is_pg:
            if emb:
                res = conn.execute(
                    text("""
                        INSERT INTO memories (
                            user_id, memory_type, subject_entity_id, content,
                            structured, status, source_type, embedding
                        ) VALUES (
                            :uid, :mtype, :eid, :content,
                            CAST(:struct AS jsonb), :status, :source, CAST(:emb AS vector)
                        ) RETURNING id
                    """),
                    {
                        "uid": user_id,
                        "mtype": memory_type,
                        "eid": subject_entity_id,
                        "content": clean_content,
                        "struct": struct_json,
                        "status": status,
                        "source": source_type,
                        "emb": str(emb)
                    }
                )
            else:
                res = conn.execute(
                    text("""
                        INSERT INTO memories (
                            user_id, memory_type, subject_entity_id, content,
                            structured, status, source_type
                        ) VALUES (
                            :uid, :mtype, :eid, :content,
                            CAST(:struct AS jsonb), :status, :source
                        ) RETURNING id
                    """),
                    {
                        "uid": user_id,
                        "mtype": memory_type,
                        "eid": subject_entity_id,
                        "content": clean_content,
                        "struct": struct_json,
                        "status": status,
                        "source": source_type
                    }
                )
            mem_id = res.fetchone()[0]
        else:
            conn.execute(
                text("""
                    INSERT INTO memories (
                        user_id, memory_type, subject_entity_id, content,
                        structured, status, source_type, embedding
                    ) VALUES (
                        :uid, :mtype, :eid, :content,
                        :struct, :status, :source, :emb
                    )
                """),
                {
                    "uid": user_id,
                    "mtype": memory_type,
                    "eid": subject_entity_id,
                    "content": clean_content,
                    "struct": struct_json,
                    "status": status,
                    "source": source_type,
                    "emb": json.dumps(emb) if emb else None
                }
            )
            res = conn.execute(text("SELECT last_insert_rowid()"))
            mem_id = res.fetchone()[0]

        conn.commit()
        return mem_id


def search_memories_hybrid(
    user_id: int,
    query: str,
    limit: int = 5,
    memory_type: Optional[str] = None,
    entity_id: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Executes hybrid search. On PostgreSQL: combines dense vector cosine distance + pg_trgm.
    On SQLite: ranks by content similarity using SequenceMatcher.
    """
    _init_sqlite_if_needed()
    q_clean = query.strip()
    if not q_clean:
        return []

    is_pg = db.engine.dialect.name == "postgresql"

    if is_pg:
        q_emb = embed_text(q_clean)
        with db.engine.connect() as conn:
            if q_emb:
                sql = """
                    SELECT m.id, m.memory_type, m.content, m.structured, m.status,
                           m.created_at, e.name AS entity_name,
                           (0.6 * (1.0 - (m.embedding <=> CAST(:q_emb AS vector))) + 0.4 * similarity(m.content, :query)) AS score
                    FROM memories m
                    LEFT JOIN entities e ON e.id = m.subject_entity_id
                    WHERE m.user_id = :uid
                      AND m.status = 'active'
                      AND (CAST(:mtype AS text) IS NULL OR m.memory_type = CAST(:mtype AS text))
                      AND (CAST(:eid AS integer) IS NULL OR m.subject_entity_id = CAST(:eid AS integer))
                    ORDER BY score DESC
                    LIMIT :lim
                """
                params = {
                    "uid": user_id,
                    "q_emb": str(q_emb),
                    "query": q_clean,
                    "mtype": memory_type,
                    "eid": entity_id,
                    "lim": limit
                }
            else:
                sql = """
                    SELECT m.id, m.memory_type, m.content, m.structured, m.status,
                           m.created_at, e.name AS entity_name,
                           similarity(m.content, :query) AS score
                    FROM memories m
                    LEFT JOIN entities e ON e.id = m.subject_entity_id
                    WHERE m.user_id = :uid
                      AND m.status = 'active'
                      AND (CAST(:mtype AS text) IS NULL OR m.memory_type = CAST(:mtype AS text))
                      AND (CAST(:eid AS integer) IS NULL OR m.subject_entity_id = CAST(:eid AS integer))
                    ORDER BY score DESC
                    LIMIT :lim
                """
                params = {
                    "uid": user_id,
                    "query": q_clean,
                    "mtype": memory_type,
                    "eid": entity_id,
                    "lim": limit
                }

            rows = conn.execute(text(sql), params).fetchall()
            return [
                {
                    "id": r[0],
                    "memory_type": r[1],
                    "content": r[2],
                    "structured": r[3] if isinstance(r[3], dict) else {},
                    "status": r[4],
                    "created_at": str(r[5]),
                    "entity_name": r[6],
                    "score": float(r[7]) if r[7] is not None else 0.0
                }
                for r in rows
            ]
    else:
        # SQLite dialect fallback (for tests)
        with db.engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT m.id, m.memory_type, m.content, m.structured, m.status,
                           m.created_at, e.name AS entity_name
                    FROM memories m
                    LEFT JOIN entities e ON e.id = m.subject_entity_id
                    WHERE m.user_id = :uid AND m.status = 'active'
                """),
                {"uid": user_id}
            ).fetchall()

            scored = []
            for r in rows:
                if memory_type and r[1] != memory_type:
                    continue
                content = r[2]
                score = difflib.SequenceMatcher(None, q_clean.lower(), content.lower()).ratio()
                if q_clean.lower() in content.lower():
                    score += 0.5
                raw_struct = r[3]
                struct = json.loads(raw_struct) if isinstance(raw_struct, str) else (raw_struct or {})
                scored.append({
                    "id": r[0],
                    "memory_type": r[1],
                    "content": content,
                    "structured": struct,
                    "status": r[4],
                    "created_at": str(r[5]),
                    "entity_name": r[6],
                    "score": score
                })
            scored.sort(key=lambda x: x["score"], reverse=True)
            return scored[:limit]


def get_commitments_for_user(user_id: int, entity_id: Optional[int] = None) -> List[Dict[str, Any]]:
    """Retrieves all active promises, commitments, and deliverables."""
    _init_sqlite_if_needed()
    is_pg = db.engine.dialect.name == "postgresql"

    with db.engine.connect() as conn:
        if is_pg:
            sql = """
                SELECT m.id, m.content, m.structured, m.created_at, e.name AS entity_name, e.id AS entity_id
                FROM memories m
                LEFT JOIN entities e ON e.id = m.subject_entity_id
                WHERE m.user_id = :uid
                  AND m.memory_type = 'commitment'
                  AND m.status = 'active'
                  AND (CAST(:eid AS integer) IS NULL OR m.subject_entity_id = CAST(:eid AS integer))
                ORDER BY m.created_at DESC
            """
            params = {"uid": user_id, "eid": entity_id}
        else:
            sql = """
                SELECT m.id, m.content, m.structured, m.created_at, e.name AS entity_name, e.id AS entity_id
                FROM memories m
                LEFT JOIN entities e ON e.id = m.subject_entity_id
                WHERE m.user_id = :uid
                  AND m.memory_type = 'commitment'
                  AND m.status = 'active'
                ORDER BY m.created_at DESC
            """
            params = {"uid": user_id}

        rows = conn.execute(text(sql), params).fetchall()
        commitments = []
        for r in rows:
            if entity_id and r[5] != entity_id:
                continue
            raw_struct = r[2]
            struct = json.loads(raw_struct) if isinstance(raw_struct, str) else (raw_struct or {})
            commitments.append({
                "id": r[0],
                "commitment": r[1],
                "structured": struct,
                "created_at": str(r[3]),
                "contact_name": r[4] or "General",
                "entity_id": r[5]
            })
        return commitments


def update_commitment_status(commitment_id: int, new_status: str = "completed"):
    """Marks a commitment as completed or archived."""
    _init_sqlite_if_needed()
    with db.engine.connect() as conn:
        conn.execute(
            text("UPDATE memories SET status = :status WHERE id = :id"),
            {"status": new_status, "id": commitment_id}
        )
        conn.commit()


# ---------------------------------------------------------------------------
# L4: Semantic Facts & Preferences
# ---------------------------------------------------------------------------

def save_user_semantic_fact(user_id: int, fact: str, category: str = "general") -> int:
    """Saves a permanent fact or life detail about the user."""
    return save_memory_item(
        user_id=user_id,
        content=fact,
        memory_type="semantic_fact",
        structured={"category": category},
        status="active"
    )


_FACTS_CACHE: dict = {}

def get_user_semantic_facts(user_id: int, category: Optional[str] = None) -> List[Dict[str, Any]]:
    """Retrieves saved life facts and preferences."""
    import time as _pytime
    now_ts = _pytime.time()
    ck = (user_id, category)
    if ck in _FACTS_CACHE and (now_ts < _FACTS_CACHE[ck]["exp"]):
        return _FACTS_CACHE[ck]["data"]

    _init_sqlite_if_needed()
    with db.engine.connect() as conn:
        rows = conn.execute(
            text("""
                SELECT id, content, structured, created_at
                FROM memories
                WHERE user_id = :uid
                  AND memory_type = 'semantic_fact'
                  AND status = 'active'
                ORDER BY created_at DESC
            """),
            {"uid": user_id}
        ).fetchall()

        facts = []
        for r in rows:
            raw_struct = r[2]
            struct = json.loads(raw_struct) if isinstance(raw_struct, str) else (raw_struct or {})
            if category and struct.get("category") != category:
                continue
            facts.append({
                "id": r[0],
                "fact": r[1],
                "category": struct.get("category", "general"),
                "created_at": str(r[3])
            })
        _FACTS_CACHE[ck] = {"exp": now_ts + 120.0, "data": facts}
        return facts
