import os
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, text

db_url = os.getenv("DATABASE_URL")
engine = create_engine(db_url)

with engine.connect() as conn:
    rows = conn.execute(text("SELECT id, user_id, memory_type, content, status, source_type, created_at FROM memories WHERE user_id = 306 ORDER BY id ASC")).mappings().all()
    for r in rows:
        print(f"[{r['id']}] {r['created_at']} | type={r['memory_type']} | content={r['content']}")
