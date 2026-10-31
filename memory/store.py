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
import re
from datetime import datetime
from typing import Optional, List, Dict, Any, Tuple, Union
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
                    superseded_by INTEGER,
                    valid_from TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    valid_to TIMESTAMP,
                    importance INTEGER DEFAULT 3,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    last_accessed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS conversation_summaries (
                    user_id INTEGER PRIMARY KEY,
                    summary TEXT NOT NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS measurements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    subject_entity_id INTEGER,
                    metric TEXT NOT NULL,
                    value REAL NOT NULL,
                    unit TEXT,
                    measured_at TIMESTAMP NOT NULL,
                    context TEXT,
                    source_memory_id INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS commitments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    subject_entity_id INTEGER,
                    title TEXT NOT NULL,
                    description TEXT,
                    target_date TEXT,
                    condition_type TEXT NOT NULL DEFAULT 'date',
                    condition_details TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    nudge_count INTEGER NOT NULL DEFAULT 0,
                    max_nudges INTEGER NOT NULL DEFAULT 3,
                    last_nudged_at TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS failed_extractions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    raw_text TEXT NOT NULL,
                    error_message TEXT NOT NULL,
                    attempts INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.commit()
    _SQLITE_INIT_DONE = True


def _get_embedding_model():
    """Lazy load FastEmbed embedding model (384-dimension multilingual MiniLM-L12-v2)."""
    global _EMBED_MODEL
    if _EMBED_MODEL is None:
        try:
            from fastembed import TextEmbedding
            _EMBED_MODEL = TextEmbedding(model_name="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
        except Exception as exc:
            logger.warning(f"FastEmbed load error (falling back to trigram search): {exc}")
            _EMBED_MODEL = False
    return _EMBED_MODEL if _EMBED_MODEL is not False else None


def embed_text(content: str, is_query: bool = False) -> Optional[List[float]]:
    """Generates 384-dimensional dense vector embedding or returns None on error."""
    model = _get_embedding_model()
    if not model or not content or not content.strip():
        return None
    try:
        prefix = "query: " if is_query else "passage: "
        embeddings = list(model.embed([f"{prefix}{content.strip()}"]))
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
    source_type: str = "chat",
    superseded_by: Optional[int] = None,
    valid_from: Optional[Any] = None,
    valid_to: Optional[Any] = None,
    importance: int = 3
) -> int:
    """
    Stores a memory item with an automatic 384-dim dense embedding and versioning metadata.
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
                            structured, status, source_type, embedding,
                            superseded_by, valid_from, valid_to, importance
                        ) VALUES (
                            :uid, :mtype, :eid, :content,
                            CAST(:struct AS jsonb), :status, :source, CAST(:emb AS vector),
                            :sup_by, COALESCE(:vfrom, CURRENT_TIMESTAMP), :vto, :imp
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
                        "emb": str(emb),
                        "sup_by": superseded_by,
                        "vfrom": valid_from,
                        "vto": valid_to,
                        "imp": importance
                    }
                )
            else:
                res = conn.execute(
                    text("""
                        INSERT INTO memories (
                            user_id, memory_type, subject_entity_id, content,
                            structured, status, source_type,
                            superseded_by, valid_from, valid_to, importance
                        ) VALUES (
                            :uid, :mtype, :eid, :content,
                            CAST(:struct AS jsonb), :status, :source,
                            :sup_by, COALESCE(:vfrom, CURRENT_TIMESTAMP), :vto, :imp
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
                        "sup_by": superseded_by,
                        "vfrom": valid_from,
                        "vto": valid_to,
                        "imp": importance
                    }
                )
            mem_id = res.fetchone()[0]
        else:
            conn.execute(
                text("""
                    INSERT INTO memories (
                        user_id, memory_type, subject_entity_id, content,
                        structured, status, source_type, embedding,
                        superseded_by, valid_from, valid_to, importance
                    ) VALUES (
                        :uid, :mtype, :eid, :content,
                        :struct, :status, :source, :emb,
                        :sup_by, COALESCE(:vfrom, CURRENT_TIMESTAMP), :vto, :imp
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
                    "emb": json.dumps(emb) if emb else None,
                    "sup_by": superseded_by,
                    "vfrom": valid_from,
                    "vto": valid_to,
                    "imp": importance
                }
            )
            res = conn.execute(text("SELECT last_insert_rowid()"))
            mem_id = res.fetchone()[0]

        conn.commit()
        return mem_id


def save_measurement(
    user_id: int,
    metric: str,
    value: float,
    unit: Optional[str],
    measured_at: datetime,
    subject_entity_id: Optional[int] = None,
    context: Optional[str] = None,
    source_memory_id: Optional[int] = None
) -> int:
    """Stores a structured numerical health or physical measurement."""
    _init_sqlite_if_needed()
    is_pg = db.engine.dialect.name == "postgresql"
    with db.engine.begin() as conn:
        if is_pg:
            res = conn.execute(
                text("""
                    INSERT INTO measurements (
                        user_id, subject_entity_id, metric, value, unit, measured_at, context, source_memory_id
                    ) VALUES (
                        :uid, :eid, :metric, :val, :unit, :mat, :ctx, :smid
                    ) RETURNING id
                """),
                {
                    "uid": user_id, "eid": subject_entity_id, "metric": metric.lower().strip(),
                    "val": float(value), "unit": unit.strip() if unit else None,
                    "mat": measured_at, "ctx": context, "smid": source_memory_id
                }
            )
            return res.fetchone()[0]
        else:
            if hasattr(measured_at, "strftime"):
                mat_str = measured_at.strftime("%Y-%m-%d %H:%M:%S")
            else:
                mat_str = str(measured_at).replace("T", " ")[:19]

            conn.execute(
                text("""
                    INSERT INTO measurements (
                        user_id, subject_entity_id, metric, value, unit, measured_at, context, source_memory_id
                    ) VALUES (
                        :uid, :eid, :metric, :val, :unit, :mat, :ctx, :smid
                    )
                """),
                {
                    "uid": user_id, "eid": subject_entity_id, "metric": metric.lower().strip(),
                    "val": float(value), "unit": unit.strip() if unit else None,
                    "mat": mat_str,
                    "ctx": context, "smid": source_memory_id
                }
            )
            res = conn.execute(text("SELECT last_insert_rowid()"))
            return res.fetchone()[0]


def get_measurements(
    user_id: int,
    metric: Optional[str] = None,
    subject_entity_id: Optional[int] = None,
    date_from: Optional[Any] = None,
    date_to: Optional[Any] = None,
    limit: int = 10
) -> List[Dict[str, Any]]:
    """Retrieves structured measurements via exact SQL lookup."""
    _init_sqlite_if_needed()
    where_clauses = ["m.user_id = :uid"]
    params: Dict[str, Any] = {"uid": user_id, "lim": limit}
    if metric:
        where_clauses.append("LOWER(m.metric) = :metric")
        params["metric"] = metric.lower().strip()
    if subject_entity_id is not None:
        where_clauses.append("m.subject_entity_id = :eid")
        params["eid"] = subject_entity_id
    if date_from:
        d_val = str(date_from).replace("T", " ")
        if len(d_val) == 10:
            d_val = f"{d_val} 00:00:00"
        where_clauses.append("m.measured_at >= :dfrom")
        params["dfrom"] = d_val
    if date_to:
        d_val = str(date_to).replace("T", " ")
        if len(d_val) == 10:
            d_val = f"{d_val} 23:59:59"
        where_clauses.append("m.measured_at <= :dto")
        params["dto"] = d_val

    sql = f"""
        SELECT m.id, m.metric, m.value, m.unit, m.measured_at, m.context, e.name as subject_name
        FROM measurements m
        LEFT JOIN entities e ON e.id = m.subject_entity_id
        WHERE {" AND ".join(where_clauses)}
        ORDER BY m.measured_at DESC
        LIMIT :lim
    """
    with db.engine.connect() as conn:
        rows = conn.execute(text(sql), params).fetchall()
        return [
            {
                "id": r[0],
                "metric": r[1],
                "value": float(r[2]) if r[2] is not None else 0.0,
                "unit": r[3],
                "measured_at": str(r[4]),
                "context": r[5],
                "subject": r[6] or "user"
            }
            for r in rows
        ]


def search_memories_hybrid(
    user_id: int,
    query: str,
    limit: int = 5,
    memory_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    min_score: float = 0.015
) -> List[Dict[str, Any]]:
    """
    Executes hybrid search via Reciprocal Rank Fusion (RRF, k=60):
    dense vector cosine ops + full-text search (tsvector) + trigram similarity (pg_trgm) + recency boost.
    On SQLite: ranks by content similarity using SequenceMatcher + recency boost.
    """
    _init_sqlite_if_needed()
    q_clean = query.strip()
    if not q_clean:
        return []

    is_pg = db.engine.dialect.name == "postgresql"

    if is_pg:
        q_emb = embed_text(q_clean, is_query=True)
        with db.engine.connect() as conn:
            if q_emb:
                sql = """
                    WITH vector_ranked AS (
                        SELECT m.id,
                               ROW_NUMBER() OVER (ORDER BY m.embedding <=> CAST(:q_emb AS vector) ASC) as v_rank
                        FROM memories m
                        WHERE m.user_id = :uid AND m.status = 'active' AND m.valid_to IS NULL AND m.valid_to IS NULL
                          AND (CAST(:mtype AS text) IS NULL OR m.memory_type = CAST(:mtype AS text))
                          AND (CAST(:eid AS integer) IS NULL OR m.subject_entity_id = CAST(:eid AS integer))
                        LIMIT 25
                    ),
                    fts_ranked AS (
                        SELECT m.id,
                               ROW_NUMBER() OVER (ORDER BY ts_rank_cd(m.tsv, plainto_tsquery('english', :query)) DESC) as f_rank
                        FROM memories m
                        WHERE m.user_id = :uid AND m.status = 'active' AND m.valid_to IS NULL AND m.valid_to IS NULL
                          AND m.tsv @@ plainto_tsquery('english', :query)
                          AND (CAST(:mtype AS text) IS NULL OR m.memory_type = CAST(:mtype AS text))
                          AND (CAST(:eid AS integer) IS NULL OR m.subject_entity_id = CAST(:eid AS integer))
                        LIMIT 25
                    ),
                    trgm_ranked AS (
                        SELECT m.id,
                               ROW_NUMBER() OVER (ORDER BY similarity(m.content, :query) DESC) as t_rank
                        FROM memories m
                        WHERE m.user_id = :uid AND m.status = 'active' AND m.valid_to IS NULL AND m.valid_to IS NULL
                          AND similarity(m.content, :query) > 0.08
                          AND (CAST(:mtype AS text) IS NULL OR m.memory_type = CAST(:mtype AS text))
                          AND (CAST(:eid AS integer) IS NULL OR m.subject_entity_id = CAST(:eid AS integer))
                        LIMIT 25
                    ),
                    candidates AS (
                        SELECT id FROM vector_ranked
                        UNION
                        SELECT id FROM fts_ranked
                        UNION
                        SELECT id FROM trgm_ranked
                    )
                    SELECT m.id, m.memory_type, m.content, m.structured, m.status,
                           m.created_at, e.name AS entity_name,
                           (
                               COALESCE(1.0 / (60.0 + vr.v_rank), 0.0) +
                               COALESCE(1.0 / (60.0 + fr.f_rank), 0.0) +
                               COALESCE(1.0 / (60.0 + tr.t_rank), 0.0) +
                               (0.015 / (1.0 + EXTRACT(EPOCH FROM (NOW() - m.created_at)) / 86400.0))
                           ) AS score
                    FROM candidates c
                    JOIN memories m ON m.id = c.id
                    LEFT JOIN entities e ON e.id = m.subject_entity_id
                    LEFT JOIN vector_ranked vr ON vr.id = c.id
                    LEFT JOIN fts_ranked fr ON fr.id = c.id
                    LEFT JOIN trgm_ranked tr ON tr.id = c.id
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
                           (similarity(m.content, :query) + (0.015 / (1.0 + EXTRACT(EPOCH FROM (NOW() - m.created_at)) / 86400.0))) AS score
                    FROM memories m
                    LEFT JOIN entities e ON e.id = m.subject_entity_id
                    WHERE m.user_id = :uid
                      AND m.status = 'active'
                      AND m.valid_to IS NULL
                      AND similarity(m.content, :query) > 0.08
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
                if (float(r[7]) if r[7] is not None else 0.0) >= min_score
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
                    WHERE m.user_id = :uid AND m.status = 'active' AND m.valid_to IS NULL
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
            scored = [s for s in scored if s["score"] >= min_score]
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


# ---------------------------------------------------------------------------
# Phase 2: Memory Consolidation, Versioning & User Controls
# ---------------------------------------------------------------------------

def consolidate_and_save_fact(
    user_id: int,
    content: str,
    subject_entity_id: Optional[int] = None,
    memory_type: str = "semantic_fact",
    structured: Optional[Dict[str, Any]] = None,
    importance: int = 3
) -> Tuple[str, Optional[int]]:
    """
    Consolidates new candidate fact against existing memories for the same user and subject:
    - NOOP: If an identical or highly similar fact (>0.88 similarity / exact content) is already active.
    - UPDATE: If the new fact supersedes an older fact (similarity > 0.52).
              Marks old fact valid_to = now, superseded_by = new_id.
    - ADD: If genuinely new knowledge.
    Returns (action, memory_id).
    """
    _init_sqlite_if_needed()
    clean_content = content.strip()
    is_pg = db.engine.dialect.name == "postgresql"
    emb = embed_text(clean_content, is_query=False)

    # Check for active existing memories for this user & subject
    with db.engine.connect() as conn:
        if is_pg and emb:
            existing_rows = conn.execute(text("""
                SELECT id, content, (1.0 - (embedding <=> CAST(:emb AS vector))) as sim
                FROM memories
                WHERE user_id = :uid AND status = 'active' AND valid_to IS NULL
                  AND (CAST(:eid AS integer) IS NULL OR subject_entity_id = CAST(:eid AS integer))
                ORDER BY sim DESC
                LIMIT 5
            """), {"uid": user_id, "eid": subject_entity_id, "emb": str(emb)}).fetchall()
        else:
            rows = conn.execute(text("""
                SELECT id, content FROM memories
                WHERE user_id = :uid AND status = 'active' AND valid_to IS NULL
            """), {"uid": user_id}).fetchall()
            existing_rows = []
            for r in rows:
                sim = difflib.SequenceMatcher(None, clean_content.lower(), r[1].lower()).ratio()
                words_new = set(re.findall(r'\w+', clean_content.lower()))
                words_old = set(re.findall(r'\w+', r[1].lower()))
                overlap = len(words_new & words_old) / max(len(words_new | words_old), 1)
                effective_sim = max(sim, overlap)
                existing_rows.append((r[0], r[1], effective_sim))
            existing_rows.sort(key=lambda x: x[2], reverse=True)

    # 1. NOOP: Exact match or similarity >= 0.95
    if existing_rows:
        top_id, top_content, top_sim = existing_rows[0]
        if top_sim >= 0.95 or clean_content.lower() == top_content.lower():
            return ("NOOP", top_id)

        # 2. UPDATE: If similarity is moderately high (>= 0.45)
        if top_sim >= 0.45:
            new_id = save_memory_item(
                user_id=user_id,
                content=clean_content,
                memory_type=memory_type,
                subject_entity_id=subject_entity_id,
                structured=structured,
                importance=importance
            )
            with db.engine.begin() as w_conn:
                w_conn.execute(text("""
                    UPDATE memories
                    SET valid_to = CURRENT_TIMESTAMP, superseded_by = :nid, status = 'superseded'
                    WHERE id = :oid
                """), {"nid": new_id, "oid": top_id})
            return ("UPDATE", new_id)

    # 3. ADD: Genuinely new fact
    new_id = save_memory_item(
        user_id=user_id,
        content=clean_content,
        memory_type=memory_type,
        subject_entity_id=subject_entity_id,
        structured=structured,
        importance=importance
    )
    return ("ADD", new_id)


def get_active_memories(user_id: int) -> List[Dict[str, Any]]:
    """Lists all active memories (valid_to IS NULL) grouped by subject with IDs."""
    _init_sqlite_if_needed()
    with db.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT m.id, m.content, m.memory_type, m.importance, m.created_at, e.name AS subject_name
            FROM memories m
            LEFT JOIN entities e ON e.id = m.subject_entity_id
            WHERE m.user_id = :uid AND m.status = 'active' AND m.valid_to IS NULL
            ORDER BY m.subject_entity_id NULLS FIRST, m.importance DESC, m.created_at DESC
        """), {"uid": user_id}).fetchall()
        return [
            {
                "id": r[0],
                "content": r[1],
                "memory_type": r[2],
                "importance": r[3] if len(r) > 3 and r[3] is not None else 3,
                "created_at": str(r[4])[:10],
                "subject": r[5] or "User"
            }
            for r in rows
        ]


def forget_memory(user_id: int, id_or_keyword: str) -> bool:
    """Soft-deletes active memories matching ID or keyword (sets valid_to = CURRENT_TIMESTAMP and status = 'archived')."""
    _init_sqlite_if_needed()
    with db.engine.begin() as conn:
        clean = id_or_keyword.strip()
        if clean.isdigit():
            res = conn.execute(text("""
                UPDATE memories
                SET valid_to = CURRENT_TIMESTAMP, status = 'archived'
                WHERE user_id = :uid AND id = :mid AND valid_to IS NULL
            """), {"uid": user_id, "mid": int(clean)})
            return res.rowcount > 0
        else:
            res = conn.execute(text("""
                UPDATE memories
                SET valid_to = CURRENT_TIMESTAMP, status = 'archived'
                WHERE user_id = :uid AND valid_to IS NULL AND LOWER(content) LIKE :kw
            """), {"uid": user_id, "kw": f"%{clean.lower()}%"})
            return res.rowcount > 0


def get_core_profile(user_id: int) -> str:
    """Loads ~300 tokens of always-true core user profile directly (NO retrieval needed)."""
    _init_sqlite_if_needed()
    with db.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT content FROM memories
            WHERE user_id = :uid AND status = 'active' AND valid_to IS NULL
              AND (importance >= 4 OR memory_type IN ('preference', 'profile'))
            ORDER BY importance DESC, created_at DESC
            LIMIT 6
        """), {"uid": user_id}).fetchall()
        facts = [r[0] for r in rows]
        if not facts:
            return ""
        return "\n".join([f"• {f}" for f in facts])


def get_conversation_summary(user_id: int) -> Optional[str]:
    """Retrieves cached running summary of earlier conversation turns."""
    _init_sqlite_if_needed()
    with db.engine.connect() as conn:
        row = conn.execute(text("""
            SELECT summary FROM conversation_summaries WHERE user_id = :uid
        """), {"uid": user_id}).fetchone()
        return row[0] if row else None


def save_conversation_summary(user_id: int, summary: str):
    """Saves or updates running conversation summary."""
    _init_sqlite_if_needed()
    is_pg = db.engine.dialect.name == "postgresql"
    with db.engine.begin() as conn:
        if is_pg:
            conn.execute(text("""
                INSERT INTO conversation_summaries (user_id, summary, updated_at)
                VALUES (:uid, :summary, CURRENT_TIMESTAMP)
                ON CONFLICT (user_id) DO UPDATE SET summary = :summary, updated_at = CURRENT_TIMESTAMP
            """), {"uid": user_id, "summary": summary.strip()})
        else:
            conn.execute(text("""
                INSERT OR REPLACE INTO conversation_summaries (user_id, summary, updated_at)
                VALUES (:uid, :summary, CURRENT_TIMESTAMP)
            """), {"uid": user_id, "summary": summary.strip()})


# ==============================================================================
# Phase 3: Commitments State Machine & Proactive Nudge Storage
# ==============================================================================

def create_commitment(
    user_id: int,
    title: str,
    description: Optional[str] = None,
    target_date: Optional[Union[datetime, str]] = None,
    condition_type: str = "date",
    condition_details: Optional[Dict[str, Any]] = None,
    subject_entity_id: Optional[int] = None
) -> int:
    """Creates a commitment entry with pending status."""
    _init_sqlite_if_needed()
    is_pg = db.engine.dialect.name == "postgresql"
    cond_details_json = json.dumps(condition_details or {}, ensure_ascii=False)

    with db.engine.begin() as conn:
        if is_pg:
            res = conn.execute(
                text("""
                    INSERT INTO commitments (
                        user_id, subject_entity_id, title, description, target_date,
                        condition_type, condition_details, status, nudge_count, max_nudges
                    ) VALUES (
                        :uid, :eid, :title, :desc, :tdate,
                        :ctype, CAST(:cdetails AS JSONB), 'pending', 0, 3
                    ) RETURNING id
                """),
                {
                    "uid": user_id, "eid": subject_entity_id, "title": title.strip(),
                    "desc": description.strip() if description else None,
                    "tdate": target_date, "ctype": condition_type,
                    "cdetails": cond_details_json
                }
            )
            return res.fetchone()[0]
        else:
            tdate_str = target_date.strftime("%Y-%m-%d %H:%M:%S") if hasattr(target_date, "strftime") else (str(target_date) if target_date else None)
            res = conn.execute(
                text("""
                    INSERT INTO commitments (
                        user_id, subject_entity_id, title, description, target_date,
                        condition_type, condition_details, status, nudge_count, max_nudges
                    ) VALUES (
                        :uid, :eid, :title, :desc, :tdate,
                        :ctype, :cdetails, 'pending', 0, 3
                    )
                """),
                {
                    "uid": user_id, "eid": subject_entity_id, "title": title.strip(),
                    "desc": description.strip() if description else None,
                    "tdate": tdate_str, "ctype": condition_type,
                    "cdetails": cond_details_json
                }
            )
            r = conn.execute(text("SELECT last_insert_rowid()")).fetchone()
            return r[0]


def get_commitments(user_id: int, status: Optional[str] = None) -> List[Dict[str, Any]]:
    """Fetches commitments for a given user, optionally filtered by status."""
    _init_sqlite_if_needed()
    query = "SELECT id, user_id, subject_entity_id, title, description, target_date, condition_type, condition_details, status, nudge_count, max_nudges, last_nudged_at, created_at FROM commitments WHERE user_id = :uid"
    params: Dict[str, Any] = {"uid": user_id}
    if status:
        query += " AND status = :st"
        params["st"] = status
    query += " ORDER BY id DESC"

    with db.engine.connect() as conn:
        rows = conn.execute(text(query), params).fetchall()
        result = []
        for r in rows:
            cd = r[7]
            if isinstance(cd, str):
                try:
                    cd = json.loads(cd)
                except Exception:
                    cd = {}
            result.append({
                "id": r[0],
                "user_id": r[1],
                "subject_entity_id": r[2],
                "title": r[3],
                "description": r[4],
                "target_date": r[5].isoformat() if hasattr(r[5], "isoformat") else str(r[5]) if r[5] else None,
                "condition_type": r[6],
                "condition_details": cd or {},
                "status": r[8],
                "nudge_count": r[9],
                "max_nudges": r[10],
                "last_nudged_at": r[11].isoformat() if hasattr(r[11], "isoformat") else str(r[11]) if r[11] else None,
                "created_at": r[12].isoformat() if hasattr(r[12], "isoformat") else str(r[12])
            })
        return result


def update_commitment_status(
    commitment_id: int,
    user_id: int,
    status: Optional[str] = None,
    increment_nudge: bool = False
) -> bool:
    """Updates status or increments nudge_count for a commitment."""
    _init_sqlite_if_needed()
    with db.engine.begin() as conn:
        updates = ["updated_at = CURRENT_TIMESTAMP"]
        params: Dict[str, Any] = {"cid": commitment_id, "uid": user_id}

        if status:
            updates.append("status = :status")
            params["status"] = status

        if increment_nudge:
            updates.append("nudge_count = nudge_count + 1")
            updates.append("last_nudged_at = CURRENT_TIMESTAMP")

        set_clause = ", ".join(updates)
        res = conn.execute(
            text(f"UPDATE commitments SET {set_clause} WHERE id = :cid AND user_id = :uid"),
            params
        )
        return res.rowcount > 0


def get_commitments_due_for_nudge(now_dt: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Returns all pending commitments ready to be nudged (nudge_count < max_nudges)."""
    _init_sqlite_if_needed()
    now_dt = now_dt or datetime.now()
    is_pg = db.engine.dialect.name == "postgresql"

    with db.engine.connect() as conn:
        if is_pg:
            rows = conn.execute(text("""
                SELECT c.id, c.user_id, u.telegram_chat_id, c.title, c.description, c.nudge_count, c.max_nudges, c.target_date, c.condition_type, c.condition_details
                FROM commitments c
                JOIN users u ON u.id = c.user_id
                WHERE c.status = 'pending'
                  AND c.nudge_count < c.max_nudges
                  AND (c.target_date IS NULL OR c.target_date <= :now OR c.condition_type != 'date')
                ORDER BY c.id ASC
            """), {"now": now_dt}).fetchall()
        else:
            now_str = now_dt.strftime("%Y-%m-%d %H:%M:%S")
            rows = conn.execute(text("""
                SELECT c.id, c.user_id, u.telegram_chat_id, c.title, c.description, c.nudge_count, c.max_nudges, c.target_date, c.condition_type, c.condition_details
                FROM commitments c
                JOIN users u ON u.id = c.user_id
                WHERE c.status = 'pending'
                  AND c.nudge_count < c.max_nudges
                  AND (c.target_date IS NULL OR c.target_date <= :now OR c.condition_type != 'date')
                ORDER BY c.id ASC
            """), {"now": now_str}).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r[0],
                "user_id": r[1],
                "chat_id": r[2],
                "title": r[3],
                "description": r[4],
                "nudge_count": r[5],
                "max_nudges": r[6],
                "target_date": str(r[7]) if r[7] else None,
                "condition_type": r[8],
                "condition_details": json.loads(r[9]) if isinstance(r[9], str) else (r[9] or {})
            })
        return results

