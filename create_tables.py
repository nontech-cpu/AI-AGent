from sqlalchemy import text
from db import engine

with engine.begin() as conn:
    conn.execute(text("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_chat_id BIGINT UNIQUE NOT NULL,
            persona TEXT,
            consented_at TIMESTAMPTZ,
            created_at TIMESTAMPTZ DEFAULT now()
        )
    """))
print("users table ready")