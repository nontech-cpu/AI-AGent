"""
PeculiarAI Phase 3 Migration: Memory Single Source of Truth
- Dynamic Identity Links (identity_links table replacing hardcoded dictionary)
- Embedding Retry Queue (embedding_retry_queue table for resilient vector generation)
- Consolidation of legacy user_facts into memories table with profile & importance tagging
- Backfill queueing for rows with NULL embeddings

Safe, idempotent, and reversible migration supporting both PostgreSQL (Neon) and SQLite.
Includes dry-run verification mode.
"""
import sys
import os
import argparse
import logging
from typing import Dict, Any, Optional

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import db
from sqlalchemy import text, Engine

logger = logging.getLogger("phase3_migration")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def check_preconditions(target_engine: Optional[Engine] = None) -> Dict[str, Any]:
    """Inspects tables and row counts before applying migration."""
    eng = target_engine or db.engine
    is_pg = eng.dialect.name == "postgresql"
    stats = {}

    with eng.connect() as conn:
        # Check memories
        try:
            row = conn.execute(text("SELECT COUNT(*) FROM memories")).fetchone()
            stats["memories_total"] = row[0] if row else 0
        except Exception:
            stats["memories_total"] = None

        # Check memories with NULL embeddings
        try:
            row = conn.execute(text("SELECT COUNT(*) FROM memories WHERE embedding IS NULL")).fetchone()
            stats["memories_null_emb"] = row[0] if row else 0
        except Exception:
            stats["memories_null_emb"] = None

        # Check user_facts
        try:
            row = conn.execute(text("SELECT COUNT(*) FROM user_facts")).fetchone()
            stats["user_facts_total"] = row[0] if row else 0
        except Exception:
            stats["user_facts_total"] = None

        # Check identity_links
        try:
            row = conn.execute(text("SELECT COUNT(*) FROM identity_links")).fetchone()
            stats["identity_links_total"] = row[0] if row else 0
        except Exception:
            stats["identity_links_total"] = None

        # Check embedding_retry_queue
        try:
            row = conn.execute(text("SELECT COUNT(*) FROM embedding_retry_queue")).fetchone()
            stats["embedding_retry_queue_total"] = row[0] if row else 0
        except Exception:
            stats["embedding_retry_queue_total"] = None

    return stats


def run_migration(target_engine: Optional[Engine] = None, dry_run: bool = False) -> Dict[str, Any]:
    """
    Executes Phase 3 memory migration idempotently.
    If dry_run is True, plans and audits mutations without committing.
    """
    eng = target_engine or db.engine
    is_pg = eng.dialect.name == "postgresql"

    print("=" * 70)
    print(f"Phase 3 Migration: Memory Single Source of Truth ({'DRY RUN' if dry_run else 'APPLY'})")
    print(f"Target Dialect: {eng.dialect.name}")
    print("=" * 70)

    pre_stats = check_preconditions(eng)
    print("Pre-migration table metrics:")
    for k, v in pre_stats.items():
        print(f"  - {k}: {v}")

    results = {
        "dry_run": dry_run,
        "pre_stats": pre_stats,
        "identity_links_created": False,
        "identity_links_seeded": 0,
        "retry_queue_created": False,
        "facts_migrated": 0,
        "null_embeddings_queued": 0
    }

    if dry_run:
        print("\n[DRY RUN] Simulating planned operations:")
        print("  1. CREATE TABLE IF NOT EXISTS identity_links")
        print("  2. SEED default canonical link (1234828592 -> 1758056012)")
        print("  3. CREATE TABLE IF NOT EXISTS embedding_retry_queue")
        print("  4. INSERT user_facts INTO memories (where not already existing)")
        print(f"     Estimated candidates from user_facts: {pre_stats.get('user_facts_total', 0)}")
        print(f"  5. ENQUEUE memories with NULL embedding into embedding_retry_queue")
        print(f"     Candidate NULL embeddings: {pre_stats.get('memories_null_emb', 0)}")
        return results

    with eng.begin() as conn:
        # 1. Identity Links Table
        if is_pg:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS identity_links (
                    id SERIAL PRIMARY KEY,
                    source_chat_id BIGINT UNIQUE NOT NULL,
                    canonical_chat_id BIGINT NOT NULL,
                    label VARCHAR(100),
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_identity_links_source ON identity_links(source_chat_id);
                CREATE INDEX IF NOT EXISTS idx_identity_links_canonical ON identity_links(canonical_chat_id);
            """))
            # Seed default mapping safely
            res = conn.execute(text("""
                INSERT INTO identity_links (source_chat_id, canonical_chat_id, label, created_at)
                VALUES (1234828592, 1758056012, 'Neerav secondary account', CURRENT_TIMESTAMP)
                ON CONFLICT (source_chat_id) DO NOTHING
            """))
            results["identity_links_seeded"] = res.rowcount if res.rowcount is not None else 1
        else:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS identity_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_chat_id INTEGER UNIQUE NOT NULL,
                    canonical_chat_id INTEGER NOT NULL,
                    label TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
            conn.execute(text("""
                INSERT OR IGNORE INTO identity_links (source_chat_id, canonical_chat_id, label, created_at)
                VALUES (1234828592, 1758056012, 'Neerav secondary account', CURRENT_TIMESTAMP)
            """))
            results["identity_links_seeded"] = 1
        results["identity_links_created"] = True
        print("  ✓ Created/verified identity_links table and default mapping")

        # 2. Embedding Retry Queue Table
        if is_pg:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS embedding_retry_queue (
                    id SERIAL PRIMARY KEY,
                    memory_id INTEGER REFERENCES memories(id) ON DELETE CASCADE,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT,
                    status VARCHAR(20) DEFAULT 'pending',
                    next_retry_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMPTZ DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_emb_retry_status ON embedding_retry_queue(status, next_retry_at);
            """))
        else:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS embedding_retry_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    memory_id INTEGER REFERENCES memories(id) ON DELETE CASCADE,
                    attempts INTEGER DEFAULT 0,
                    last_error TEXT,
                    status TEXT DEFAULT 'pending',
                    next_retry_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
            """))
        results["retry_queue_created"] = True
        print("  ✓ Created/verified embedding_retry_queue table")

        # 3. Consolidate legacy user_facts into memories (single source of truth)
        if pre_stats.get("user_facts_total", 0) > 0:
            if is_pg:
                res = conn.execute(text("""
                    INSERT INTO memories (
                        user_id, content, memory_type, structured, status,
                        importance, created_at, updated_at
                    )
                    SELECT
                        uf.user_id,
                        uf.fact,
                        CASE WHEN uf.category = 'personal' THEN 'profile' ELSE 'semantic_fact' END,
                        jsonb_build_object('category', uf.category, 'migrated_from', 'user_facts'),
                        uf.status,
                        CASE WHEN uf.category = 'personal' THEN 5 ELSE 3 END,
                        uf.created_at,
                        uf.created_at
                    FROM user_facts uf
                    WHERE NOT EXISTS (
                        SELECT 1 FROM memories m
                        WHERE m.user_id = uf.user_id AND m.content = uf.fact
                    )
                """))
                results["facts_migrated"] = res.rowcount if res.rowcount is not None else 0
            else:
                # SQLite consolidation
                rows = conn.execute(text("""
                    SELECT uf.user_id, uf.fact, uf.category, uf.status, uf.created_at
                    FROM user_facts uf
                    WHERE NOT EXISTS (
                        SELECT 1 FROM memories m
                        WHERE m.user_id = uf.user_id AND m.content = uf.fact
                    )
                """)).fetchall()
                count = 0
                import json
                for r in rows:
                    uid, fact, cat, st, cat_ts = r[0], r[1], r[2], r[3], r[4]
                    m_type = 'profile' if cat == 'personal' else 'semantic_fact'
                    imp = 5 if cat == 'personal' else 3
                    struct_str = json.dumps({'category': cat, 'migrated_from': 'user_facts'})
                    conn.execute(text("""
                        INSERT INTO memories (
                            user_id, content, memory_type, structured, status,
                            importance, created_at, updated_at
                        ) VALUES (
                            :uid, :fact, :mtype, :struct, :st, :imp, :ts, :ts
                        )
                    """), {
                        "uid": uid, "fact": fact, "mtype": m_type,
                        "struct": struct_str, "st": st or "active",
                        "imp": imp, "ts": cat_ts
                    })
                    count += 1
                results["facts_migrated"] = count
            print(f"  ✓ Migrated {results['facts_migrated']} rows from user_facts into memories")
        else:
            print("  - No user_facts found to migrate")

        # 4. Enqueue active memories with NULL embedding into embedding_retry_queue
        if is_pg:
            res = conn.execute(text("""
                INSERT INTO embedding_retry_queue (memory_id, status, created_at, updated_at)
                SELECT m.id, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                FROM memories m
                WHERE m.embedding IS NULL AND m.status = 'active'
                  AND NOT EXISTS (
                      SELECT 1 FROM embedding_retry_queue q WHERE q.memory_id = m.id
                  )
            """))
            results["null_embeddings_queued"] = res.rowcount if res.rowcount is not None else 0
        else:
            rows = conn.execute(text("""
                SELECT m.id FROM memories m
                WHERE m.embedding IS NULL AND m.status = 'active'
                  AND NOT EXISTS (
                      SELECT 1 FROM embedding_retry_queue q WHERE q.memory_id = m.id
                  )
            """)).fetchall()
            for r in rows:
                conn.execute(text("""
                    INSERT INTO embedding_retry_queue (memory_id, status, created_at, updated_at)
                    VALUES (:mid, 'pending', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """), {"mid": r[0]})
            results["null_embeddings_queued"] = len(rows)

        print(f"  ✓ Enqueued {results['null_embeddings_queued']} NULL-embedding memories into embedding_retry_queue")

    post_stats = check_preconditions(eng)
    print("\nPost-migration table metrics:")
    for k, v in post_stats.items():
        print(f"  - {k}: {v}")
    results["post_stats"] = post_stats
    print("\nPhase 3 Migration completed successfully.")
    return results


def rollback_phase3(target_engine: Optional[Engine] = None):
    """Reversible rollback for Phase 3 tables."""
    eng = target_engine or db.engine
    print(f"Rolling back Phase 3 tables on {eng.dialect.name}...")
    with eng.begin() as conn:
        conn.execute(text("DROP TABLE IF EXISTS embedding_retry_queue"))
        conn.execute(text("DROP TABLE IF EXISTS identity_links"))
    print("Phase 3 tables dropped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 3 Memory Migration")
    parser.add_argument("--dry-run", action="store_true", help="Audit without writing mutations")
    parser.add_argument("--apply", action="store_true", help="Apply migration to database")
    parser.add_argument("--rollback", action="store_true", help="Roll back migration tables")
    args = parser.parse_args()

    if args.rollback:
        rollback_phase3()
    elif args.apply:
        run_migration(dry_run=False)
    else:
        run_migration(dry_run=True)

