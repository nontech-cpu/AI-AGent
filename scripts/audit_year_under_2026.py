import os
import sys
import json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.getcwd())
import db
from memory.store import resolve_user_id

def audit_year_under_2026():
    chat_id = 1758056012
    uid = resolve_user_id(chat_id)
    print(f"Chat ID: {chat_id} -> User ID: {uid}")

    with db.engine.connect() as conn:
        # Check column names in memory_items
        cols = [c[0] for c in conn.execute(db.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'memory_items'")).fetchall()]
        print("memory_items columns:", cols)

        user_col = "user_id" if "user_id" in cols else "chat_id"
        target_id = uid if user_col == "user_id" else chat_id

        # 1. Query memory_items where due_date < '2026-01-01'
        m_rows = conn.execute(db.text(f"""
            SELECT id, {user_col}, title, category, due_date, due_time, duration_minutes, created_at
            FROM memory_items
            WHERE {user_col} = :tid AND due_date IS NOT NULL AND due_date < '2026-01-01'
            ORDER BY due_date ASC
        """), {"tid": target_id}).fetchall()

        # 2. Query reminders where due_date < '2026-01-01'
        r_rows = conn.execute(db.text(f"""
            SELECT r.id, r.item_id, r.remind_at, r.sent, m.title, m.due_date
            FROM reminders r
            JOIN memory_items m ON r.item_id = m.id
            WHERE m.{user_col} = :tid AND m.due_date < '2026-01-01'
            ORDER BY r.remind_at ASC
        """), {"tid": target_id}).fetchall()

        # 3. Memories table
        mem_rows = conn.execute(db.text("""
            SELECT id, user_id, memory_type, content, structured, created_at
            FROM memories
            WHERE user_id = :uid AND created_at < '2026-01-01'
            ORDER BY id ASC
        """), {"uid": uid}).fetchall()

    print(f"\nAudit results for {user_col} = {target_id}:")
    print(f"memory_items with due_date < 2026: {len(m_rows)}")
    for r in m_rows:
        print(f"  Item ID {r[0]}: title='{r[2]}', category='{r[3]}', due_date={r[4]}, due_time={r[5]}, duration={r[6]}, created_at={r[7]}")

    print(f"\nreminders linked to items with due_date < 2026: {len(r_rows)}")
    for r in r_rows:
        print(f"  Reminder ID {r[0]}: item_id={r[1]}, remind_at={r[2]}, sent={r[3]}, title='{r[4]}', due_date={r[5]}")

    print(f"\nmemories table rows created before 2026: {len(mem_rows)}")
    for r in mem_rows:
        print(f"  Memory ID {r[0]}: type='{r[2]}', content='{r[3]}', created_at={r[5]}")

if __name__ == "__main__":
    audit_year_under_2026()
