import os
import json
import logging
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

load_dotenv()
raw_db_url = os.getenv("DATABASE_URL") or "sqlite:///:memory:"
if raw_db_url.startswith("postgres://"):
    raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)

if raw_db_url.startswith("sqlite"):
    from sqlalchemy.pool import StaticPool
    engine = create_engine(
        raw_db_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
else:
    engine = create_engine(raw_db_url, pool_pre_ping=True)


def give_consent(chat_id):
    now_expr = "NOW()" if engine.dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    with engine.begin() as conn:
        conn.execute(
            text(f"""
                INSERT INTO users (telegram_chat_id, consented_at)
                VALUES (:c, {now_expr})
                ON CONFLICT (telegram_chat_id)
                DO UPDATE SET consented_at = {now_expr}
            """),
            {"c": chat_id},
        )


def has_consent(chat_id):
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT consented_at FROM users WHERE telegram_chat_id = :c"),
                {"c": chat_id},
            ).first()
        return bool(row and row[0])
    except Exception:
        return False


def set_persona(chat_id, persona):
    chat_id = canonical_chat_id(chat_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET persona = :p WHERE telegram_chat_id = :c"),
            {"p": persona, "c": chat_id},
        )


def get_persona(chat_id):
    chat_id = canonical_chat_id(chat_id)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT persona FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        ).first()
    return row[0] if row else None


def set_categories(chat_id, cats):
    chat_id = canonical_chat_id(chat_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE users SET categories = :cats WHERE telegram_chat_id = :c"),
            {"cats": cats, "c": chat_id},
        )


def get_categories(chat_id):
    chat_id = canonical_chat_id(chat_id)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT categories FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        ).first()
    return list(row[0]) if row and row[0] else []


def save_memory(chat_id, category, title, amount, due_date, due_time):
    chat_id = canonical_chat_id(chat_id)
    tm = due_time
    d = due_date
    if engine.dialect.name == "sqlite":
        if hasattr(due_time, "strftime"):
            tm = due_time.strftime("%H:%M:%S")
        if hasattr(due_date, "isoformat"):
            d = due_date.isoformat()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO memory_items "
                "(user_id, category, title, amount, due_date, due_time) "
                "SELECT id, :cat, :t, :a, :d, :tm FROM users "
                "WHERE telegram_chat_id = :c RETURNING id"
            ),
            {"cat": category, "t": title, "a": amount, "d": d,
             "tm": tm, "c": chat_id},
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
    chat_id = canonical_chat_id(chat_id)
    d_val = d.isoformat() if hasattr(d, "isoformat") and engine.dialect.name == "sqlite" else d
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.category, m.title, m.amount, m.due_time, m.id "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.due_date = :d "
                "ORDER BY m.due_time NULLS FIRST, m.id"
            ),
            {"c": chat_id, "d": d_val},
        ).fetchall()
    return rows


def update_memory_time(chat_id, item_id, new_due_time):
    chat_id = canonical_chat_id(chat_id)
    tm = new_due_time
    if engine.dialect.name == "sqlite" and hasattr(new_due_time, "strftime"):
        tm = new_due_time.strftime("%H:%M:%S")
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE memory_items SET due_time = :tm "
                "WHERE id = :id AND user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c)"
            ),
            {"tm": tm, "id": item_id, "c": chat_id}
        )



def delete_user(chat_id):
    # memory_items aur reminders apne aap delete honge (ON DELETE CASCADE)
    with engine.begin() as conn:
        conn.execute(
            text("DELETE FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        )


def wipe_all_user_memories(chat_id: int):
    chat_id = canonical_chat_id(chat_id)
    """
    Master Privacy Wipe: Clears all user_facts, memory_items, reminders,
    chat_logs, contact_memories, smart_lists, board_cards, user_rules,
    memories, entities, entity_aliases, user_integrations, and pending_actions.
    Each table is deleted in its own isolated transaction so that a failure or
    missing table/column never rolls back deletions from other tables.
    """
    uid = None
    with engine.connect() as conn:
        u_row = conn.execute(
            text("SELECT id FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id}
        ).first()
        if u_row:
            uid = int(u_row[0])

    if not uid:
        return False

    deletions = [
        ("DELETE FROM user_facts WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM reminders WHERE item_id IN (SELECT id FROM memory_items WHERE user_id = :u)", {"u": uid}),
        ("DELETE FROM memory_items WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM chat_logs WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM contact_memories WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM smart_list_items WHERE list_id IN (SELECT id FROM smart_lists WHERE user_id = :u)", {"u": uid}),
        ("DELETE FROM smart_lists WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM board_cards WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM user_rules WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM memories WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM entity_aliases WHERE entity_id IN (SELECT id FROM entities WHERE user_id = :u)", {"u": uid}),
        ("DELETE FROM entities WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM user_integrations WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM google_tokens WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM email_links WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM user_device_sessions WHERE user_id = :u", {"u": uid}),
        ("DELETE FROM pending_actions WHERE chat_id = :c", {"c": chat_id}),
        ("DELETE FROM processed_user_emails WHERE chat_id = :c", {"c": chat_id}),
        ("DELETE FROM oauth_states WHERE chat_id = :c", {"c": chat_id}),
        ("UPDATE users SET state = NULL, state_data = NULL WHERE id = :u", {"u": uid}),
    ]

    for sql, params in deletions:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql), params)
        except Exception as e:
            logger.warning(f"wipe_all_user_memories non-fatal error executing '{sql}': {e}")

    try:
        from state.active_context import clear_active_context
        clear_active_context(chat_id)
    except Exception:
        pass

    try:
        from agent.prompt import _USER_META_CACHE
        _USER_META_CACHE.pop(chat_id, None)
    except Exception:
        pass

    try:
        import sqlite3
        v_db = os.getenv("PECULIARAI_WEB_VERIFY_DB", "peculiarai_web_verify.sqlite3")
        if os.path.exists(v_db):
            with sqlite3.connect(v_db) as v_conn:
                v_conn.execute("DELETE FROM web_verifications WHERE telegram_chat_id = ?", (str(chat_id),))
                v_conn.commit()
    except Exception:
        pass

    return True


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
                "WHERE (r.sent IS NOT TRUE) "
                "AND r.remind_at <= :now "
                "ORDER BY r.remind_at"
            ),
            {"now": now},
        ).fetchall()


def get_upcoming_reminders(chat_id, now):
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT r.remind_at, r.label, m.category, m.title, m.amount, m.due_time "
                "FROM reminders r "
                "JOIN memory_items m ON m.id = r.item_id "
                "JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id IN (:c, 1758056012, 1234828592) "
                "AND (r.sent IS NOT TRUE) "
                "AND r.remind_at > :now "
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

# ---------- Canonical User Identity Mapping ----------
CANONICAL_CHAT_ID_MAP = {
    1234828592: 1758056012,
}

def canonical_chat_id(chat_id):
    try:
        cid = int(chat_id)
        return CANONICAL_CHAT_ID_MAP.get(cid, cid)
    except Exception:
        return chat_id


def ensure_user(chat_id):
    chat_id = canonical_chat_id(chat_id)
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO users (telegram_chat_id) VALUES (:c) "
                    "ON CONFLICT (telegram_chat_id) DO NOTHING"
                ),
                {"c": chat_id},
            )
    except Exception:
        pass


def get_state(chat_id):
    # None = user hi nahi hai. Warna (state, state_data)
    with engine.connect() as conn:
        row = conn.execute(
            text("SELECT state, state_data FROM users WHERE telegram_chat_id = :c"),
            {"c": chat_id},
        ).first()
    if not row:
        return None
    st, data = row[0], row[1]
    if isinstance(data, str) and data.strip().startswith(("{", "[")):
        try:
            data = json.loads(data)
        except Exception:
            pass
    return (st, data)


def set_state(chat_id, state, data=None):
    serialized_data = data
    if isinstance(data, (dict, list)):
        serialized_data = json.dumps(data)
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE users SET state = :s, state_data = :d "
                "WHERE telegram_chat_id = :c"
            ),
            {"s": state, "d": serialized_data, "c": chat_id},
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
    now_expr = "NOW()" if engine.dialect.name == "postgresql" else "CURRENT_TIMESTAMP"
    with engine.begin() as conn:
        conn.execute(
            text(f"""
                INSERT INTO google_tokens
                    (user_id, email, refresh_token_enc, scopes,
                     created_at, updated_at)
                VALUES
                    (:user_id, :email, :refresh_token_enc, :scopes,
                     {now_expr}, {now_expr})
                ON CONFLICT (user_id) DO UPDATE SET
                    email = EXCLUDED.email,
                    refresh_token_enc = EXCLUDED.refresh_token_enc,
                    scopes = EXCLUDED.scopes,
                    updated_at = {now_expr}
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


# ---------- Phase 2: Dynamic Task Boards ----------

def get_board_cards(chat_id):
    """Fetch user's Kanban board cards organized by status."""
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT b.id, b.title, b.description, b.status, b.priority, b.due_date, b.created_at "
                "FROM board_cards b JOIN users u ON u.id = b.user_id "
                "WHERE u.telegram_chat_id = :c ORDER BY b.id DESC"
            ),
            {"c": chat_id},
        ).fetchall()

    board = {"queue": [], "this_week": [], "today": [], "done": []}
    for r in rows:
        st = r[3] if r[3] in board else "queue"
        board[st].append({
            "id": r[0],
            "title": r[1],
            "description": r[2] or "",
            "status": r[3],
            "priority": r[4],
            "due_date": str(r[5]) if r[5] else None,
            "created_at": r[6].isoformat() if hasattr(r[6], "isoformat") else str(r[6])
        })
    return board


def create_board_card(chat_id, title, description="", status="queue", priority="medium", due_date=None):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO board_cards (user_id, title, description, status, priority, due_date) "
                "SELECT id, :t, :desc, :st, :pri, :due FROM users WHERE telegram_chat_id = :c RETURNING id"
            ),
            {"t": title, "desc": description, "st": status, "pri": priority, "due": due_date, "c": chat_id},
        ).first()
    return row[0] if row else None


def move_board_card(chat_id, card_id, new_status):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "UPDATE board_cards SET status = :st, updated_at = now() "
                "WHERE id = :cid AND user_id = (SELECT id FROM users WHERE telegram_chat_id = :c)"
            ),
            {"st": new_status, "cid": card_id, "c": chat_id},
        )
    return (result.rowcount or 0) > 0


def delete_board_card(chat_id, card_id):
    with engine.begin() as conn:
        result = conn.execute(
            text(
                "DELETE FROM board_cards WHERE id = :cid AND user_id = (SELECT id FROM users WHERE telegram_chat_id = :c)"
            ),
            {"cid": card_id, "c": chat_id},
        )
    return (result.rowcount or 0) > 0


# ---------- Phase 2: Persistent Smart Lists ----------

def get_smart_lists(chat_id):
    chat_id = canonical_chat_id(chat_id)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT l.id, l.name, l.category, l.created_at, "
                "       it.id as item_id, it.title as item_title, it.quantity, it.checked "
                "FROM smart_lists l "
                "JOIN users u ON u.id = l.user_id "
                "LEFT JOIN smart_list_items it ON it.list_id = l.id "
                "WHERE u.telegram_chat_id = :c "
                "ORDER BY l.id ASC, it.id ASC"
            ),
            {"c": chat_id},
        ).fetchall()

        lists_dict = {}
        for r in rows:
            lid = r[0]
            if lid not in lists_dict:
                lists_dict[lid] = {
                    "id": lid,
                    "name": r[1],
                    "category": r[2],
                    "items": []
                }
            if r[4] is not None:  # item_id exists
                lists_dict[lid]["items"].append({
                    "id": r[4],
                    "title": r[5],
                    "quantity": r[6],
                    "checked": bool(r[7])
                })
        return list(lists_dict.values())



def create_smart_list(chat_id, name, category="general"):
    chat_id = canonical_chat_id(chat_id)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO smart_lists (user_id, name, category) "
                "SELECT id, :n, :cat FROM users WHERE telegram_chat_id = :c RETURNING id"
            ),
            {"n": name.strip(), "cat": category, "c": chat_id},
        ).first()
    return row[0] if row else None


def create_smart_list_with_items(chat_id, name, category="general", items=None):
    chat_id = canonical_chat_id(chat_id)
    """Creates a smart list and its items in a single atomic transaction for high speed."""
    items = items or []
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO smart_lists (user_id, name, category) "
                "SELECT id, :n, :cat FROM users WHERE telegram_chat_id = :c RETURNING id"
            ),
            {"n": name.strip(), "cat": category, "c": chat_id},
        ).first()
        if not row:
            return None, []
        list_id = row[0]
        created = []
        for it in items:
            it_clean = it.strip() if isinstance(it, str) else str(it)
            if not it_clean:
                continue
            item_row = conn.execute(
                text(
                    "INSERT INTO smart_list_items (list_id, title, quantity, checked) "
                    "VALUES (:lid, :t, NULL, false) RETURNING id"
                ),
                {"lid": list_id, "t": it_clean},
            ).first()
            if item_row:
                created.append({"id": item_row[0], "title": it_clean, "checked": False})
        return list_id, created


def add_smart_list_item(chat_id, list_id, title, quantity=None):
    chat_id = canonical_chat_id(chat_id)

    with engine.begin() as conn:
        owner = conn.execute(
            text(
                "SELECT l.id FROM smart_lists l JOIN users u ON u.id = l.user_id "
                "WHERE l.id = :lid AND u.telegram_chat_id = :c"
            ),
            {"lid": list_id, "c": chat_id},
        ).first()
        if not owner:
            return None
        row = conn.execute(
            text(
                "INSERT INTO smart_list_items (list_id, title, quantity, checked) "
                "VALUES (:lid, :t, :q, false) RETURNING id"
            ),
            {"lid": list_id, "t": title.strip(), "q": quantity},
        ).first()
    return row[0] if row else None


def add_smart_list_items_batch(chat_id, list_id, items):
    chat_id = canonical_chat_id(chat_id)
    if not items:
        return []
    created = []
    with engine.begin() as conn:
        owner = conn.execute(
            text(
                "SELECT l.id FROM smart_lists l JOIN users u ON u.id = l.user_id "
                "WHERE l.id = :lid AND u.telegram_chat_id = :c"
            ),
            {"lid": list_id, "c": chat_id},
        ).first()
        if not owner:
            return []
        for it in items:
            it_clean = it.strip() if isinstance(it, str) else str(it)
            if not it_clean:
                continue
            row = conn.execute(
                text(
                    "INSERT INTO smart_list_items (list_id, title, quantity, checked) "
                    "VALUES (:lid, :t, NULL, false) RETURNING id"
                ),
                {"lid": list_id, "t": it_clean},
            ).first()
            if row:
                created.append({"id": row[0], "title": it_clean, "checked": False})
    return created


def toggle_smart_list_item(chat_id, item_id, checked=None):
    chat_id = canonical_chat_id(chat_id)
    with engine.begin() as conn:
        owner = conn.execute(
            text(
                "SELECT it.id, it.checked FROM smart_list_items it "
                "JOIN smart_lists l ON l.id = it.list_id "
                "JOIN users u ON u.id = l.user_id "
                "WHERE it.id = :item_id AND u.telegram_chat_id = :c"
            ),
            {"item_id": item_id, "c": chat_id},
        ).first()
        if not owner:
            return False
        current_checked = bool(owner[1])
        new_val = (not current_checked) if checked is None else bool(checked)
        conn.execute(
            text("UPDATE smart_list_items SET checked = :chk WHERE id = :item_id"),
            {"chk": new_val, "item_id": item_id},
        )
    return True


def delete_smart_list_item(chat_id, item_id):
    chat_id = canonical_chat_id(chat_id)
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "DELETE FROM smart_list_items WHERE id = :item_id AND list_id IN "
                "(SELECT l.id FROM smart_lists l JOIN users u ON u.id = l.user_id WHERE u.telegram_chat_id = :c)"
            ),
            {"item_id": item_id, "c": chat_id},
        )
    return (res.rowcount or 0) > 0


def delete_smart_list(chat_id, list_id):
    chat_id = canonical_chat_id(chat_id)
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "DELETE FROM smart_lists WHERE id = :lid AND user_id = (SELECT id FROM users WHERE telegram_chat_id = :c)"
            ),
            {"lid": list_id, "c": chat_id},
        )
    return (res.rowcount or 0) > 0


def find_or_create_smart_list(chat_id, name, category="general"):
    name_clean = name.strip()
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT l.id FROM smart_lists l JOIN users u ON u.id = l.user_id "
                "WHERE u.telegram_chat_id = :c AND LOWER(l.name) = LOWER(:n)"
            ),
            {"c": chat_id, "n": name_clean},
        ).first()
    if row:
        return row[0]
    return create_smart_list(chat_id, name_clean, category=category)


# ---------- Phase 2: Behavioral Rules ----------

def get_user_rules(chat_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT r.id, r.rule_text, r.is_active, r.created_at "
                "FROM user_rules r JOIN users u ON u.id = r.user_id "
                "WHERE u.telegram_chat_id = :c ORDER BY r.id ASC"
            ),
            {"c": chat_id},
        ).fetchall()
    return [
        {"id": r[0], "rule": r[1], "active": bool(r[2]), "created_at": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3])}
        for r in rows
    ]


def add_user_rule(chat_id, rule_text):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO user_rules (user_id, rule_text, is_active) "
                "SELECT id, :rt, true FROM users WHERE telegram_chat_id = :c RETURNING id"
            ),
            {"rt": rule_text.strip(), "c": chat_id},
        ).first()
    return row[0] if row else None


def delete_user_rule(chat_id, rule_id):
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "DELETE FROM user_rules WHERE id = :rid AND user_id = (SELECT id FROM users WHERE telegram_chat_id = :c)"
            ),
            {"rid": rule_id, "c": chat_id},
        )
    return (res.rowcount or 0) > 0


# ---------- Phase 2: Serendipity Feed ----------

def get_serendipity_memories(chat_id, min_days_old=14, limit=5):
    """Fetch older memories for user review to keep or clear mental clutter."""
    with engine.connect() as conn:
        # SQLite compatibility note: if PostgreSQL use interval, if SQLite use date arithmetic
        is_sqlite = "sqlite" in str(engine.url)
        if is_sqlite:
            rows = conn.execute(
                text(
                    "SELECT m.id, m.category, m.title, m.amount, m.due_date, m.due_time, m.created_at "
                    "FROM memory_items m JOIN users u ON u.id = m.user_id "
                    "WHERE u.telegram_chat_id = :c ORDER BY m.id DESC LIMIT :lim"
                ),
                {"c": chat_id, "lim": limit},
            ).fetchall()
        else:
            rows = conn.execute(
                text(
                    "SELECT m.id, m.category, m.title, m.amount, m.due_date, m.due_time, m.created_at "
                    "FROM memory_items m JOIN users u ON u.id = m.user_id "
                    "WHERE u.telegram_chat_id = :c AND m.created_at < (now() - :days * interval '1 day') "
                    "ORDER BY RANDOM() LIMIT :lim"
                ),
                {"c": chat_id, "days": min_days_old, "lim": limit},
            ).fetchall()
    return [
        {
            "id": r[0],
            "category": r[1],
            "title": r[2],
            "amount": r[3],
            "due_date": str(r[4]) if r[4] else None,
            "due_time": r[5].strftime("%H:%M") if r[5] else None,
            "saved_date": str(r[6].date()) if hasattr(r[6], "date") else str(r[6])
        }
        for r in rows
    ]


# ---------- Phase 2: Relationship & Networking Memory (Enfolo feature) ----------

def get_contact_memories(chat_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.id, c.name, c.company, c.role_title, c.email, c.phone, c.met_at, c.context_notes, c.commitments, c.created_at "
                "FROM contact_memories c JOIN users u ON u.id = c.user_id "
                "WHERE u.telegram_chat_id = :cid ORDER BY c.id DESC"
            ),
            {"cid": chat_id},
        ).fetchall()
    return [
        {
            "id": r[0],
            "name": r[1],
            "company": r[2] or "",
            "role_title": r[3] or "",
            "email": r[4] or "",
            "phone": r[5] or "",
            "met_at": r[6] or "",
            "context_notes": r[7] or "",
            "commitments": r[8] or "",
            "created_at": r[9].isoformat() if hasattr(r[9], "isoformat") else str(r[9])
        }
        for r in rows
    ]


def save_contact_memory(chat_id, name, company=None, role_title=None, email=None, phone=None, met_at=None, context_notes=None, commitments=None):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO contact_memories (user_id, name, company, role_title, email, phone, met_at, context_notes, commitments) "
                "SELECT id, :n, :comp, :role, :em, :ph, :met, :ctx, :com "
                "FROM users WHERE telegram_chat_id = :cid RETURNING id"
            ),
            {
                "cid": chat_id,
                "n": name.strip(),
                "comp": company.strip() if company else None,
                "role": role_title.strip() if role_title else None,
                "em": email.strip() if email else None,
                "ph": phone.strip() if phone else None,
                "met": met_at.strip() if met_at else None,
                "ctx": context_notes.strip() if context_notes else None,
                "com": commitments.strip() if commitments else None,
            },
        ).first()
    return row[0] if row else None


def delete_contact_memory(chat_id, contact_id):
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "DELETE FROM contact_memories WHERE id = :cid AND user_id = (SELECT id FROM users WHERE telegram_chat_id = :chat_id)"
            ),
            {"cid": contact_id, "chat_id": chat_id},
        )
    return (res.rowcount or 0) > 0


def update_contact_commitment(chat_id, contact_name, commitments=None, context_notes=None):
    with engine.begin() as conn:
        conn.execute(
            text(
                "UPDATE contact_memories SET "
                "commitments = COALESCE(CAST(:com AS VARCHAR), commitments), "
                "context_notes = CASE "
                "WHEN CAST(:ctx AS VARCHAR) IS NOT NULL AND context_notes IS NOT NULL THEN context_notes || ' | ' || CAST(:ctx AS VARCHAR) "
                "WHEN CAST(:ctx AS VARCHAR) IS NOT NULL THEN CAST(:ctx AS VARCHAR) ELSE context_notes END "
                "WHERE user_id IN (SELECT id FROM users WHERE telegram_chat_id = :cid OR id IN (SELECT user_id FROM google_tokens LIMIT 1)) "
                "AND LOWER(name) LIKE LOWER(:pattern)"
            ),
            {
                "cid": chat_id,
                "com": commitments.strip() if commitments else None,
                "ctx": context_notes.strip() if context_notes else None,
                "pattern": f"%{contact_name.strip()}%",
            },
        )


# ---------- Persistent Chat Logs & ChatGPT-style Long-Term Memory ----------

_CHAT_HISTORY_CACHE: dict = {}

def log_chat_message(chat_id, role, message):
    chat_id = canonical_chat_id(chat_id)
    """Saves a conversation turn permanently in PostgreSQL, preventing immediate duplicates."""
    if not message or not str(message).strip():
        return
    clean_msg = str(message).strip()
    for k in list(_CHAT_HISTORY_CACHE.keys()):
        if k[0] == chat_id:
            _CHAT_HISTORY_CACHE.pop(k, None)
    try:
        with engine.begin() as conn:
            # Check last logged message for this user to avoid consecutive duplicates
            last = conn.execute(
                text(
                    "SELECT role, message FROM chat_logs "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"cid": chat_id}
            ).first()
            if last and last[0] == role and last[1] == clean_msg:
                return  # Skip duplicate insert

            conn.execute(
                text(
                    "INSERT INTO chat_logs (user_id, role, message) "
                    "SELECT id, :role, :msg FROM users WHERE telegram_chat_id = :cid"
                ),
                {"cid": chat_id, "role": role, "msg": clean_msg}
            )
    except Exception as exc:
        print("log_chat_message error:", repr(exc))


def get_recent_chat_history(chat_id, limit=15):
    chat_id = canonical_chat_id(chat_id)
    """Retrieves the recent conversation history in chronological order without sequential duplicates."""
    import time as _pytime
    now_ts = _pytime.time()
    ck = (chat_id, limit)
    cached = _CHAT_HISTORY_CACHE.get(ck)
    if cached and (now_ts < cached["exp"]):
        return cached["data"]
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT role, message, created_at FROM chat_logs "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "ORDER BY id DESC LIMIT :lim"
                ),
                {"cid": chat_id, "lim": limit * 2}
            ).fetchall()
        # Return chronological order (oldest to newest) with duplicates removed
        cleaned = []
        for r in reversed(rows):
            role, msg = r[0], r[1]
            if cleaned and cleaned[-1]["role"] == role and cleaned[-1]["text"] == msg:
                continue
            cleaned.append({"role": role, "text": msg})
        res = cleaned[-limit:]
        _CHAT_HISTORY_CACHE[ck] = {"exp": now_ts + 45.0, "data": res}
        return res
    except Exception as exc:
        print("get_recent_chat_history error:", repr(exc))
        return []


def clear_chat_history(chat_id):
    """Deletes recent conversation logs for a user."""
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM chat_logs "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid)"
                ),
                {"cid": chat_id}
            )
        return True
    except Exception as exc:
        print("clear_chat_history error:", repr(exc))
        return False


def get_chat_turns_mentioning_keyword(chat_id, keyword, limit=10):
    """Retrieves past chat turns that mention a keyword (e.g. contact name or topic) for episodic long-term memory."""
    if not keyword or len(str(keyword).strip()) < 3:
        return []
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT role, message, created_at FROM chat_logs "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "AND message ILIKE :kw "
                    "ORDER BY id DESC LIMIT :lim"
                ),
                {"cid": chat_id, "kw": f"%{str(keyword).strip()}%", "lim": limit}
            ).fetchall()
        return [{"role": r[0], "text": r[1]} for r in reversed(rows)]
    except Exception as exc:
        print("get_chat_turns_mentioning_keyword error:", repr(exc))
        return []


def save_user_fact(chat_id, fact, category="general"):
    chat_id = canonical_chat_id(chat_id)
    """Saves an extracted life context fact, plan, or preference to user_facts."""
    if not fact or not str(fact).strip():
        return
    clean_fact = str(fact).strip()
    try:
        with engine.begin() as conn:
            # Avoid exact duplicate active facts
            existing = conn.execute(
                text(
                    "SELECT id FROM user_facts "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "AND status = 'active' AND LOWER(fact) = LOWER(:f)"
                ),
                {"cid": chat_id, "f": clean_fact}
            ).first()
            if not existing:
                conn.execute(
                    text(
                        "INSERT INTO user_facts (user_id, fact, category, status) "
                        "SELECT id, :f, :cat, 'active' FROM users WHERE telegram_chat_id = :cid"
                    ),
                    {"cid": chat_id, "f": clean_fact, "cat": category}
                )
    except Exception as exc:
        print("save_user_fact error:", repr(exc))


def get_user_facts(chat_id, limit=25):
    chat_id = canonical_chat_id(chat_id)
    """Retrieves active life context facts, plans, and preferences for the user."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT id, fact, category, created_at FROM user_facts "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "AND status = 'active' ORDER BY updated_at DESC LIMIT :lim"
                ),
                {"cid": chat_id, "lim": limit}
            ).fetchall()
        return [{"id": r[0], "fact": r[1], "category": r[2]} for r in rows]
    except Exception as exc:
        print("get_user_facts error:", repr(exc))
        return []


def delete_matching_user_facts(chat_id, query):
    """Deletes active life context facts, tickets or plans matching query keywords (e.g. 'ticket', 'flight')."""
    if not query or not str(query).strip():
        return 0
    pattern = f"%{query.strip()}%"
    deleted_count = 0
    try:
        with engine.begin() as conn:
            res = conn.execute(
                text(
                    "UPDATE user_facts SET status = 'deleted' "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "AND status = 'active' AND LOWER(fact) LIKE LOWER(:pat)"
                ),
                {"cid": chat_id, "pat": pattern}
            )
            deleted_count += res.rowcount or 0

            # Also clean from reminders if matching
            res_rem = conn.execute(
                text(
                    "DELETE FROM reminders WHERE item_id IN ("
                    "  SELECT id FROM memory_items WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "  AND LOWER(title) LIKE LOWER(:pat)"
                    ")"
                ),
                {"cid": chat_id, "pat": pattern}
            )
            res_items = conn.execute(
                text(
                    "DELETE FROM memory_items WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "AND LOWER(title) LIKE LOWER(:pat)"
                ),
                {"cid": chat_id, "pat": pattern}
            )
            deleted_count += (res_items.rowcount or 0)
    except Exception as exc:
        print("delete_matching_user_facts error:", repr(exc))
    return deleted_count


# ---------- User Third-Party Integrations (Notion, GitHub, etc.) ----------

def save_user_integration(chat_id, service: str, api_key: str, config_data: str = "{}"):
    chat_id = canonical_chat_id(chat_id)
    """Encrypts and securely stores an API key/token for a third-party service."""
    if not api_key or not str(api_key).strip():
        return False
    from crypto import encrypt_aes256_gcm
    enc = encrypt_aes256_gcm(str(api_key).strip())
    svc = str(service).strip().lower()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO user_integrations (user_id, service, api_key_enc, config_data, is_active, updated_at) "
                    "SELECT id, :svc, :enc, :cfg, true, now() FROM users WHERE telegram_chat_id = :cid "
                    "ON CONFLICT (user_id, service) DO UPDATE SET "
                    "api_key_enc = EXCLUDED.api_key_enc, "
                    "config_data = EXCLUDED.config_data, "
                    "is_active = true, "
                    "updated_at = now()"
                ),
                {"cid": chat_id, "svc": svc, "enc": enc, "cfg": config_data or "{}"}
            )
        return True
    except Exception as exc:
        print("save_user_integration error:", repr(exc))
        return False


def get_user_integration(chat_id, service: str):
    chat_id = canonical_chat_id(chat_id)
    """Retrieves and decrypts the API key/token for a user integration."""
    from crypto import decrypt_any
    svc = str(service).strip().lower()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT api_key_enc, config_data, is_active FROM user_integrations "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "AND service = :svc AND is_active = true"
                ),
                {"cid": chat_id, "svc": svc}
            ).first()
        if not row:
            return None
        decrypted_key = decrypt_any(row[0])
        return {
            "api_key": decrypted_key,
            "config_data": row[1] or "{}",
            "is_active": row[2]
        }
    except Exception as exc:
        print("get_user_integration error:", repr(exc))
        return None


def delete_user_integration(chat_id, service: str):
    chat_id = canonical_chat_id(chat_id)
    """Deletes or deactivates a user integration."""
    svc = str(service).strip().lower()
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "DELETE FROM user_integrations "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "AND service = :svc"
                ),
                {"cid": chat_id, "svc": svc}
            )
        return True
    except Exception as exc:
        print("delete_user_integration error:", repr(exc))
        return False


def get_user_integrations_summary(chat_id):
    chat_id = canonical_chat_id(chat_id)
    """Returns a status summary of all supported integrations for a user."""
    integrations = {
        "notion": {"connected": False, "details": "Not connected"},
        "github": {"connected": False, "details": "Not connected"},
    }
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT service, api_key_enc, is_active FROM user_integrations "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid)"
                ),
                {"cid": chat_id}
            ).fetchall()
        for r in rows:
            svc = r[0].lower()
            if r[2] and svc in integrations:
                integrations[svc] = {
                    "connected": True,
                    "details": f"Connected ({svc.capitalize()} Token Active)"
                }
    except Exception as exc:
        print("get_user_integrations_summary error:", repr(exc))

    return integrations


def _init_device_sessions_table():
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS user_device_sessions (
                        id SERIAL PRIMARY KEY,
                        user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                        phone TEXT,
                        device_id TEXT NOT NULL,
                        google_email TEXT,
                        last_login_at TIMESTAMPTZ DEFAULT now(),
                        created_at TIMESTAMPTZ DEFAULT now(),
                        CONSTRAINT uq_user_device UNIQUE (user_id, device_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_user_device ON user_device_sessions(user_id, device_id);
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS user_device_sessions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        user_id INTEGER NOT NULL,
                        phone TEXT,
                        device_id TEXT NOT NULL,
                        google_email TEXT,
                        last_login_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (user_id, device_id)
                    )
                """))
    except Exception as exc:
        print("init_device_sessions_table error:", repr(exc))


def record_device_session(user_id: int, phone: str, device_id: str, google_email: str = None):
    """Upsert a device session record for (user_id, device_id)."""
    if not user_id or not device_id:
        return
    _init_device_sessions_table()
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(
                    text("""
                        INSERT INTO user_device_sessions (user_id, phone, device_id, google_email, last_login_at, created_at)
                        VALUES (:uid, :ph, :did, :email, now(), now())
                        ON CONFLICT (user_id, device_id)
                        DO UPDATE SET
                            phone = COALESCE(EXCLUDED.phone, user_device_sessions.phone),
                            google_email = COALESCE(EXCLUDED.google_email, user_device_sessions.google_email),
                            last_login_at = now()
                    """),
                    {"uid": user_id, "ph": phone, "did": device_id, "email": google_email}
                )
            else:
                conn.execute(
                    text("""
                        INSERT INTO user_device_sessions (user_id, phone, device_id, google_email, last_login_at, created_at)
                        VALUES (:uid, :ph, :did, :email, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                        ON CONFLICT (user_id, device_id)
                        DO UPDATE SET
                            phone = COALESCE(excluded.phone, user_device_sessions.phone),
                            google_email = COALESCE(excluded.google_email, user_device_sessions.google_email),
                            last_login_at = CURRENT_TIMESTAMP
                    """),
                    {"uid": user_id, "ph": phone, "did": device_id, "email": google_email}
                )
    except Exception as exc:
        print("record_device_session error:", repr(exc))


def get_device_session(user_id: int, device_id: str):
    """Retrieve device session for a user and device."""
    if not user_id or not device_id:
        return None
    _init_device_sessions_table()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT user_id, phone, device_id, google_email, last_login_at, created_at
                    FROM user_device_sessions
                    WHERE user_id = :uid AND device_id = :did
                """),
                {"uid": user_id, "did": device_id}
            ).mappings().first()
            return dict(row) if row else None
    except Exception as exc:
        print("get_device_session error:", repr(exc))
        return None


def get_latest_device_session_for_user(user_id: int):
    """Get the most recent device session for a user."""
    if not user_id:
        return None
    _init_device_sessions_table()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("""
                    SELECT user_id, phone, device_id, google_email, last_login_at, created_at
                    FROM user_device_sessions
                    WHERE user_id = :uid
                    ORDER BY last_login_at DESC
                    LIMIT 1
                """),
                {"uid": user_id}
            ).mappings().first()
            return dict(row) if row else None
    except Exception as exc:
        print("get_latest_device_session_for_user error:", repr(exc))
        return None


def check_device_authorization(user_id: int, device_id: str, max_days: int = 365) -> dict:
    """
    Validates if user has authorized access:
    - If device session exists, returns valid = True.
    - If user has an existing session on another device or connected Google account,
      allows seamless adoption for phone-verified users and returns valid = True.
    - Retains google_email for UI display and account switching.
    """
    session = get_device_session(user_id, device_id)
    if session:
        return {
            "valid": True,
            "reason": "valid",
            "google_email": session.get("google_email"),
            "message": "Device session valid"
        }

    # If no session on this specific device_id, check if user has ANY authorized session or token
    other = get_latest_device_session_for_user(user_id)
    if other and other.get("google_email"):
        # Auto-adopt session on this new device
        record_device_session(
            user_id,
            phone=other.get("phone"),
            device_id=device_id,
            google_email=other.get("google_email")
        )
        return {
            "valid": True,
            "reason": "adopted",
            "google_email": other.get("google_email"),
            "message": f"Connected as {other.get('google_email')}."
        }

    # Fallback check directly in google_tokens
    token_info = get_google_token(user_id)
    if token_info and token_info.get("email"):
        record_device_session(
            user_id,
            device_id=device_id,
            google_email=token_info.get("email")
        )
        return {
            "valid": True,
            "reason": "token_found",
            "google_email": token_info.get("email"),
            "message": f"Connected as {token_info.get('email')}."
        }

    return {
        "valid": False,
        "reason": "no_session",
        "google_email": None,
        "message": "No active Google connection found for this account."
    }


def _init_processed_user_emails_table():
    """Ensure the processed_user_emails table exists for deduplication."""
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS processed_user_emails (
                        id SERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        email_id TEXT NOT NULL,
                        sender TEXT,
                        subject TEXT,
                        category TEXT,
                        is_important BOOLEAN DEFAULT FALSE,
                        processed_at TIMESTAMPTZ DEFAULT now(),
                        CONSTRAINT uq_chat_user_email UNIQUE (chat_id, email_id)
                    );
                    CREATE INDEX IF NOT EXISTS idx_processed_user_emails ON processed_user_emails(chat_id, email_id);
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS processed_user_emails (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER NOT NULL,
                        email_id TEXT NOT NULL,
                        sender TEXT,
                        subject TEXT,
                        category TEXT,
                        is_important BOOLEAN DEFAULT 0,
                        processed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (chat_id, email_id)
                    )
                """))
    except Exception as exc:
        print("init_processed_user_emails_table error:", repr(exc))


def is_user_email_processed(chat_id: int, email_id: str) -> bool:
    """Check if a specific email ID has already been inspected for this user."""
    if not chat_id or not email_id:
        return True
    _init_processed_user_emails_table()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM processed_user_emails WHERE chat_id = :c AND email_id = :e"),
                {"c": chat_id, "e": email_id}
            ).first()
            return row is not None
    except Exception as exc:
        print("is_user_email_processed error:", repr(exc))
        return False


def record_processed_user_email(
    chat_id: int,
    email_id: str,
    sender: str = "",
    subject: str = "",
    category: str = "",
    is_important: bool = False,
):
    """Mark an incoming email as processed to prevent duplicate alerts."""
    if not chat_id or not email_id:
        return
    _init_processed_user_emails_table()
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(
                    text("""
                        INSERT INTO processed_user_emails (chat_id, email_id, sender, subject, category, is_important, processed_at)
                        VALUES (:c, :e, :s, :sub, :cat, :imp, now())
                        ON CONFLICT (chat_id, email_id) DO NOTHING
                    """),
                    {
                        "c": chat_id,
                        "e": email_id,
                        "s": (sender or "")[:255],
                        "sub": (subject or "")[:500],
                        "cat": (category or "")[:50],
                        "imp": is_important,
                    }
                )
            else:
                conn.execute(
                    text("""
                        INSERT OR IGNORE INTO processed_user_emails (chat_id, email_id, sender, subject, category, is_important, processed_at)
                        VALUES (:c, :e, :s, :sub, :cat, :imp, CURRENT_TIMESTAMP)
                    """),
                    {
                        "c": chat_id,
                        "e": email_id,
                        "s": (sender or "")[:255],
                        "sub": (subject or "")[:500],
                        "cat": (category or "")[:50],
                        "imp": 1 if is_important else 0,
                    }
                )
    except Exception as exc:
        print("record_processed_user_email error:", repr(exc))


def get_all_connected_google_users() -> list[dict]:
    """Return all users who have an active Google connection linked to a Telegram chat."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT u.id AS user_id, u.telegram_chat_id, gt.email, gt.refresh_token_enc, gt.scopes
                FROM users u
                JOIN google_tokens gt ON u.id = gt.user_id
                WHERE u.telegram_chat_id IS NOT NULL 
                  AND gt.refresh_token_enc IS NOT NULL
            """)).mappings().all()
            return [dict(r) for r in rows]
    except Exception as exc:
        print("get_all_connected_google_users error:", repr(exc))
        return []


def _init_morning_briefing_table():
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS daily_morning_briefings (
                        id SERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        briefing_date VARCHAR(10) NOT NULL,
                        trigger_kind VARCHAR(50) DEFAULT 'standard_8am',
                        sent_at TIMESTAMPTZ DEFAULT now(),
                        CONSTRAINT uq_chat_briefing_date UNIQUE (chat_id, briefing_date)
                    );
                    CREATE INDEX IF NOT EXISTS idx_morning_briefings_lookup ON daily_morning_briefings(chat_id, briefing_date);
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS daily_morning_briefings (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER NOT NULL,
                        briefing_date TEXT NOT NULL,
                        trigger_kind TEXT DEFAULT 'standard_8am',
                        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        UNIQUE (chat_id, briefing_date)
                    )
                """))
    except Exception as exc:
        print("init_morning_briefing_table error:", repr(exc))


def is_morning_briefing_sent(chat_id: int, briefing_date: str) -> bool:
    """Returns True if morning briefing for briefing_date (YYYY-MM-DD) was already dispatched."""
    if not chat_id or not briefing_date:
        return True
    _init_morning_briefing_table()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM daily_morning_briefings WHERE chat_id = :c AND briefing_date = :d"),
                {"c": chat_id, "d": briefing_date}
            ).first()
            return row is not None
    except Exception as exc:
        print("is_morning_briefing_sent error:", repr(exc))
        return False


def record_morning_briefing_sent(chat_id: int, briefing_date: str, trigger_kind: str = "standard_8am"):
    """Marks morning briefing as sent for the date."""
    if not chat_id or not briefing_date:
        return
    _init_morning_briefing_table()
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(
                    text("""
                        INSERT INTO daily_morning_briefings (chat_id, briefing_date, trigger_kind, sent_at)
                        VALUES (:c, :d, :k, now())
                        ON CONFLICT (chat_id, briefing_date) DO NOTHING
                    """),
                    {"c": chat_id, "d": briefing_date, "k": trigger_kind}
                )
            else:
                conn.execute(
                    text("""
                        INSERT OR IGNORE INTO daily_morning_briefings (chat_id, briefing_date, trigger_kind, sent_at)
                        VALUES (:c, :d, :k, CURRENT_TIMESTAMP)
                    """),
                    {"c": chat_id, "d": briefing_date, "k": trigger_kind}
                )
    except Exception as exc:
        print("record_morning_briefing_sent error:", repr(exc))


# ==============================================================================
# SELF-LEARNING & AUTO-CORRECTION ENGINE (Persistent User Corrections)
# ==============================================================================

def _init_user_corrections_table():
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS user_corrections (
                        id SERIAL PRIMARY KEY,
                        chat_id BIGINT NOT NULL,
                        wrong_concept TEXT NOT NULL,
                        correction_rule TEXT NOT NULL,
                        category VARCHAR(50) DEFAULT 'general',
                        created_at TIMESTAMPTZ DEFAULT now(),
                        status VARCHAR(20) DEFAULT 'active'
                    );
                    CREATE INDEX IF NOT EXISTS idx_user_corrections_lookup ON user_corrections(chat_id, status);
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS user_corrections (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER NOT NULL,
                        wrong_concept TEXT NOT NULL,
                        correction_rule TEXT NOT NULL,
                        category TEXT DEFAULT 'general',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        status TEXT DEFAULT 'active'
                    )
                """))
    except Exception as exc:
        print("init_user_corrections_table error:", repr(exc))


def save_user_correction(chat_id: int, wrong_concept: str, correction_rule: str, category: str = "general") -> bool:
    """Saves a permanent correction taught by the user so the bot never repeats the mistake."""
    chat_id = canonical_chat_id(chat_id)
    if not wrong_concept or not correction_rule:
        return False
    _init_user_corrections_table()
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(
                    text("""
                        INSERT INTO user_corrections (chat_id, wrong_concept, correction_rule, category, created_at, status)
                        VALUES (:cid, :wrong, :rule, :cat, now(), 'active')
                    """),
                    {"cid": chat_id, "wrong": wrong_concept.strip(), "rule": correction_rule.strip(), "cat": category.strip()}
                )
            else:
                conn.execute(
                    text("""
                        INSERT INTO user_corrections (chat_id, wrong_concept, correction_rule, category, created_at, status)
                        VALUES (:cid, :wrong, :rule, :cat, CURRENT_TIMESTAMP, 'active')
                    """),
                    {"cid": chat_id, "wrong": wrong_concept.strip(), "rule": correction_rule.strip(), "cat": category.strip()}
                )
            return True
    except Exception as exc:
        print("save_user_correction error:", repr(exc))
        return False


def get_user_corrections(chat_id: int, limit: int = 15):
    """Retrieves all active user corrections for prompt injection."""
    chat_id = canonical_chat_id(chat_id)
    _init_user_corrections_table()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT id, wrong_concept, correction_rule, category, created_at
                    FROM user_corrections
                    WHERE chat_id = :cid AND status = 'active'
                    ORDER BY id DESC LIMIT :lim
                """),
                {"cid": chat_id, "lim": limit}
            ).fetchall()
            return [
                {
                    "id": r[0],
                    "wrong_concept": r[1],
                    "correction_rule": r[2],
                    "category": r[3],
                    "created_at": str(r[4])
                }
                for r in rows
            ]
    except Exception as exc:
        print("get_user_corrections error:", repr(exc))
        return []


def format_user_corrections(chat_id: int) -> str:
    """Formats active corrections into a sharp, unbreachable prompt block."""
    corrections = get_user_corrections(chat_id)
    if not corrections:
        return "None recorded yet. Follow user instructions strictly."
    lines = []
    for c in corrections:
        lines.append(f"• DO NOT say/assume: '{c['wrong_concept']}' -> PERMANENT TRUTH: {c['correction_rule']}")
    return "\n".join(lines)



def get_all_active_telegram_users() -> list[dict]:
    """Retrieves all registered and consented users with valid Telegram chat IDs."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT id, telegram_chat_id, first_name, username
                FROM users
                WHERE telegram_chat_id IS NOT NULL AND consented_at IS NOT NULL
            """)).fetchall()
            return [
                {
                    "user_id": r[0],
                    "chat_id": int(r[1]),
                    "first_name": r[2] or "",
                    "username": r[3] or ""
                }
                for r in rows if r[1]
            ]
    except Exception as exc:
        print("get_all_active_telegram_users error:", repr(exc))
        return []




