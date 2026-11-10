import os
import json
from dotenv import load_dotenv
load_dotenv()
from sqlalchemy import create_engine, text

db_url = os.getenv("DATABASE_URL")
engine = create_engine(db_url)

with engine.connect() as conn:
    cols = conn.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'chat_logs'")).fetchall()
    print("chat_logs columns:", [c[0] for c in cols])

    # Query chat_logs using the correct column names
    print("\n=== CHAT LOGS FOR USER 306 / CHAT 1234828592 ===")
    user_col = "user_id" if "user_id" in [c[0] for c in cols] else "telegram_chat_id"
    txt_col = "message" if "message" in [c[0] for c in cols] else "message_text"
    query_str = f"SELECT * FROM chat_logs WHERE created_at >= '2026-11-10 07:30:00' ORDER BY id ASC"
    logs = conn.execute(text(query_str)).mappings().all()
    for l in logs:
        d = dict(l)
        print(f"[{d.get('id')}] User/Chat: {d.get('user_id') or d.get('chat_id')} | Role: {d.get('role')} | Msg: {repr(d.get('message') or d.get('message_text'))}")

    print("\n=== CURRENT MEMORY ITEMS FOR USER 306 & USER 408 ===")
    mems = conn.execute(text("SELECT id, user_id, category, title, amount, due_date, due_time, duration_minutes, recurrence, created_at FROM memory_items WHERE user_id IN (306, 408) ORDER BY id ASC")).mappings().all()
    for m in mems:
        print(dict(m))
