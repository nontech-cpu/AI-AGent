import db

def migrate():
    print("Running chat_logs channel migration...")
    with db.engine.begin() as conn:
        dialect = db.engine.dialect.name
        if dialect == "postgresql":
            conn.execute(db.text("ALTER TABLE chat_logs ADD COLUMN IF NOT EXISTS channel VARCHAR(32) DEFAULT 'telegram';"))
            conn.execute(db.text("CREATE INDEX IF NOT EXISTS idx_chat_logs_user_channel ON chat_logs (user_id, channel, id DESC);"))
            print("Postgres chat_logs migration completed successfully!")
        else:
            cols = [r[1] for r in conn.execute(db.text("PRAGMA table_info(chat_logs)")).fetchall()]
            if "channel" not in cols:
                conn.execute(db.text("ALTER TABLE chat_logs ADD COLUMN channel TEXT DEFAULT 'telegram';"))
            print("SQLite chat_logs migration completed successfully!")

if __name__ == "__main__":
    migrate()
