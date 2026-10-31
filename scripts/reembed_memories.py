"""
PeculiarAI Re-embedding Script:
Migrates all active memory embeddings to multilingual embeddings (sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2).
"""
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

import db
from sqlalchemy import text
from memory.store import embed_text

def reembed_all():
    is_pg = db.engine.dialect.name == "postgresql"
    if not is_pg:
        print("Re-embedding script is for PostgreSQL with pgvector.")
        return

    print("Fetching active memories for re-embedding...")
    with db.engine.connect() as conn:
        rows = conn.execute(text("""
            SELECT id, content FROM memories WHERE status = 'active'
        """)).fetchall()

    print(f"Total active memories to re-embed: {len(rows)}")
    updated = 0
    with db.engine.begin() as conn:
        for r_id, content in rows:
            if not content:
                continue
            emb = embed_text(content, is_query=False)
            if emb:
                conn.execute(
                    text("UPDATE memories SET embedding = CAST(:emb AS vector) WHERE id = :id"),
                    {"emb": str(emb), "id": r_id}
                )
                updated += 1
                if updated % 10 == 0 or updated == len(rows):
                    print(f"  Processed {updated}/{len(rows)} memories...")

    print(f"Successfully re-embedded {updated} memories with multilingual embeddings!")

if __name__ == "__main__":
    reembed_all()
