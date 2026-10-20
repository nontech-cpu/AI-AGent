import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
engine = create_engine(os.getenv("DATABASE_URL"), pool_pre_ping=True)


def give_consent(chat_id):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (telegram_chat_id, consented_at) "
                "VALUES (:c, now()) "
                "ON CONFLICT (telegram_chat_id) "
                "DO UPDATE SET consented_at = now()"
            ),
            {"c": chat_id},
        )


def has_consent(chat_id):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT consented_at FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        ).first()
    return bool(row and row[0])


def set_persona(chat_id, persona):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET persona = :p WHERE telegram_chat_id = :c"),
            {"p": persona, "c": chat_id},
        )


def get_persona(chat_id):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT persona FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        ).first()
    return row[0] if row else None


def set_categories(chat_id, cats):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET categories = :cats WHERE telegram_chat_id = :c"),
            {"cats": cats, "c": chat_id},
        )


def get_categories(chat_id):
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT categories FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        ).first()
    return list(row[0]) if row and row[0] else []


def save_memory(chat_id, category, title, amount, due_date, due_time):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO memory_items "
                "(user_id, category, title, amount, due_date, due_time) "
                "SELECT id, :cat, :t, :a, :d, :tm FROM users "
                "WHERE telegram_chat_id = :c RETURNING id"
            ),
            {"cat": category, "t": title, "a": amount, "d": due_date,
             "tm": due_time, "c": chat_id},
        ).first()
    return row[0] if row else None


def list_memories(chat_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.category, m.title, m.amount, m.due_date, m.due_time "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c "
                "ORDER BY m.due_date NULLS LAST, m.due_time NULLS LAST, m.id DESC "
                "LIMIT 20"
            ),
            {"c": chat_id},
        ).fetchall()
    return rows


def items_for_date(chat_id, d):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.category, m.title, m.amount, m.due_time "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.due_date = :d "
                "ORDER BY m.due_time NULLS FIRST, m.id"
            ),
            {"c": chat_id, "d": d},
        ).fetchall()
    return rows


def delete_user(chat_id):
    # memory_items aur reminders apne aap delete honge (ON DELETE CASCADE)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        )


def export_data(chat_id):
    with engine.connect() as conn:
        u = conn.execute(
            text(
                "SELECT persona, categories, consented_at FROM users "
                "WHERE telegram_chat_id = :c"
            ),
            {"c": chat_id},
        ).first()
        rows = conn.execute(
            text(
                "SELECT m.category, m.title, m.amount, m.due_date, m.due_time, m.created_at "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c ORDER BY m.id"
            ),
            {"c": chat_id},
        ).fetchall()
    if not u:
        return None
    return {
        "persona": u[0],
        "categories": list(u[1] or []),
        "consented_at": u[2],
        "memories": [
            {"category": r[0], "title": r[1], "amount": r[2],
             "due_date": r[3], "due_time": r[4], "saved_at": r[5]}
            for r in rows
        ],
    }


# ---------- Reminders ----------

def add_reminders(item_id, reminders):
    with engine.begin() as conn:
        for remind_at, label in reminders:
            conn.execute(
                text(
                    "INSERT INTO reminders (item_id, remind_at, label) "
                    "VALUES (:i, :a, :l)"
                ),
                {"i": item_id, "a": remind_at, "l": label},
            )


def get_pending_reminders(now):
    # Jin reminders ka time aa gaya hai, aur jo abhi bheje nahi gaye
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT r.id, u.telegram_chat_id, m.category, m.title, m.amount, "
                "m.due_date, m.due_time, r.label "
                "FROM reminders r "
                "JOIN memory_items m ON m.id = r.item_id "
                "JOIN users u ON u.id = m.user_id "
                "WHERE NOT r.sent AND r.remind_at <= :now "
                "AND m.category = ANY(u.categories) "
                "ORDER BY r.remind_at"
            ),
            {"now": now},
        ).fetchall()


def get_upcoming_reminders(chat_id, now):
    # Sirf aane wale reminders (abhi ke baad ke), purane nahi
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT r.remind_at, r.label, m.category, m.title, m.amount, m.due_time "
                "FROM reminders r "
                "JOIN memory_items m ON m.id = r.item_id "
                "JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND NOT r.sent AND r.remind_at > :now "
                "AND m.category = ANY(u.categories) "
                "ORDER BY r.remind_at LIMIT 30"
            ),
            {"c": chat_id, "now": now},
        ).fetchall()


def mark_sent(reminder_id):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE reminders SET sent = true WHERE id = :i"),
            {"i": reminder_id},
        )

# ---------- Email linking ----------

def create_email_link(chat_id, email, code):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO email_links (user_id, email, code, verified) "
                "SELECT id, :e, :code, false FROM users WHERE telegram_chat_id = :c "
                "ON CONFLICT (user_id) DO UPDATE SET email = EXCLUDED.email, "
                "code = EXCLUDED.code, verified = false, created_at = now()"
            ),
            {"e": email, "code": code, "c": chat_id},
        )


def get_link_by_email(email):
    # returns (chat_id, code, verified) ya None
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT u.telegram_chat_id, l.code, l.verified "
                "FROM email_links l JOIN users u ON u.id = l.user_id "
                "WHERE l.email = :e"
            ),
            {"e": email},
        ).first()
    return tuple(row) if row else None


def get_linked_email(chat_id):
    # returns (email, verified) ya None
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT l.email, l.verified FROM email_links l "
                "JOIN users u ON u.id = l.user_id WHERE u.telegram_chat_id = :c"
            ),
            {"c": chat_id},
        ).first()
    return tuple(row) if row else None


def verify_email_link(email):
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE email_links SET verified = true WHERE email = :e"),
            {"e": email},
        )


def remove_email_link(chat_id):
    with engine.begin() as conn:
        conn.execute(
            text(
                "DELETE FROM email_links WHERE user_id = "
                "(SELECT id FROM users WHERE telegram_chat_id = :c)"
            ),
            {"c": chat_id},
        )

# ---------- Conversation state (WhatsApp-style flow) ----------

def ensure_user(chat_id):
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO users (telegram_chat_id) VALUES (:c) "
                "ON CONFLICT (telegram_chat_id) DO NOTHING"
            ),
            {"c": chat_id},
        )


def get_state(chat_id):
    # None = user hi nahi hai. Warna (state, state_data)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT state, state_data FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        ).first()
    return (row[0], row[1]) if row else None


def set_state(chat_id, state, data=None):
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE users SET state = :s, state_data = :d "
                "WHERE telegram_chat_id = :c"
            ),
            {"s": state, "d": data, "c": chat_id},
        )


def items_between(chat_id, start, end):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT m.due_date, m.due_time, m.category, m.title, m.amount "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.due_date BETWEEN :s AND :e "
                "ORDER BY m.due_date, m.due_time NULLS FIRST, m.id"
            ),
            {"c": chat_id, "s": start, "e": end},
        ).fetchall()


def save_google_token(user_id: int, email: str, refresh_token_enc: str, scopes: str):
    """Insert or update the encrypted Google refresh token."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO google_tokens
                    (user_id, email, refresh_token_enc, scopes,
                     created_at, updated_at)
                VALUES
                    (:user_id, :email, :refresh_token_enc, :scopes,
                     NOW(), NOW())
                ON CONFLICT (user_id) DO UPDATE SET
                    email = EXCLUDED.email,
                    refresh_token_enc = EXCLUDED.refresh_token_enc,
                    scopes = EXCLUDED.scopes,
                    updated_at = NOW()
            """),
            {
                "user_id": user_id,
                "email": email,
                "refresh_token_enc": refresh_token_enc,
                "scopes": scopes,
            },
        )


def get_google_token(user_id: int):
    """Return the saved Google token row, or None."""
    with engine.connect() as conn:
        result = conn.execute(
            text("""
                SELECT user_id, email, refresh_token_enc, scopes
                FROM google_tokens
                WHERE user_id = :user_id
            """),
            {"user_id": user_id},
        ).mappings().first()

        return dict(result) if result else None


def delete_google_token(user_id: int):
    """Disconnect Google by deleting its stored token."""
    with engine.begin() as conn:
        conn.execute(
            text("""
                DELETE FROM google_tokens
                WHERE user_id = :user_id
            """),
            {"user_id": user_id},
        )

# Add these two functions at the END of your EXISTING db.py.
# Keep your existing Google-token functions and all other code unchanged.

def find_meetings_before(chat_id, due_date, before_time):
    """Return only this user's saved appointment meetings before the requested time."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT m.id, m.title, m.due_date, m.due_time "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.category = 'appointments' "
                "AND m.due_date = :d AND m.due_time IS NOT NULL AND m.due_time < :t "
                "AND (m.title ILIKE '%meeting%' OR m.title ILIKE '%metting%' "
                "OR m.title ILIKE '%meetting%' OR m.title ILIKE '%meetng%' "
                "OR m.title ILIKE '%call%' OR m.title ILIKE '%appointment%') "
                "ORDER BY m.due_time, m.id"
            ),
            {"c": chat_id, "d": due_date, "t": before_time},
        ).fetchall()


def delete_memory_items(chat_id, item_ids):
    """Delete only previewed items owned by this chat, plus their reminder rows."""
    ids = [int(value) for value in item_ids if isinstance(value, int) or (isinstance(value, str) and value.isdigit())]
    if not ids:
        return 0

    with engine.begin() as conn:
        owned = conn.execute(
            text(
                "SELECT m.id FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.id = ANY(:ids)"
            ),
            {"c": chat_id, "ids": ids},
        ).fetchall()
        safe_ids = [int(row[0]) for row in owned]
        if not safe_ids:
            return 0

        conn.execute(
            text("DELETE FROM reminders WHERE item_id = ANY(:ids)"),
            {"ids": safe_ids},
        )
        result = conn.execute(
            text("DELETE FROM memory_items WHERE id = ANY(:ids)"),
            {"ids": safe_ids},
        )
        return result.rowcount or 0

def find_meetings_at_times(chat_id, due_date, times, source_text=None):
    """Return this user's meeting reminders at exact local clock times on a date."""
    time_labels = [f"{int(hour):02d}:{int(minute):02d}" for hour, minute in times]
    if not time_labels:
        return []
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT m.id, m.title, m.due_date, m.due_time "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.category = 'appointments' "
                "AND m.due_date = :d AND m.due_time IS NOT NULL "
                "AND to_char(m.due_time, 'HH24:MI') = ANY(:times) "
                "AND (m.title ILIKE '%meeting%' OR m.title ILIKE '%metting%' "
                "OR m.title ILIKE '%meetting%' OR m.title ILIKE '%meetng%' "
                "OR m.title ILIKE '%call%' OR m.title ILIKE '%appointment%') "
                "ORDER BY m.due_time, m.id"
            ),
            {"c": chat_id, "d": due_date, "times": time_labels},
        ).fetchall()


def find_recent_meetings(chat_id, limit=3):
    """Return this user's most recently created or upcoming meeting reminders."""
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT m.id, m.title, m.due_date, m.due_time "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.category = 'appointments' "
                "ORDER BY m.id DESC LIMIT :lim"
            ),
            {"c": chat_id, "lim": limit},
        ).fetchall()
