import os
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv()
raw_db_url = os.getenv("DATABASE_URL") or "sqlite:///:memory:"
if raw_db_url.startswith("postgres://"):
    raw_db_url = raw_db_url.replace("postgres://", "postgresql://", 1)
engine = create_engine(raw_db_url, pool_pre_ping=True)


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
    with engine.connect() as conn:
        lists = conn.execute(
            text(
                "SELECT l.id, l.name, l.category, l.created_at "
                "FROM smart_lists l JOIN users u ON u.id = l.user_id "
                "WHERE u.telegram_chat_id = :c ORDER BY l.id ASC"
            ),
            {"c": chat_id},
        ).fetchall()

        result = []
        for l in lists:
            list_id = l[0]
            items = conn.execute(
                text(
                    "SELECT id, title, quantity, checked FROM smart_list_items "
                    "WHERE list_id = :lid ORDER BY id ASC"
                ),
                {"lid": list_id},
            ).fetchall()
            result.append({
                "id": list_id,
                "name": l[1],
                "category": l[2],
                "items": [
                    {"id": it[0], "title": it[1], "quantity": it[2], "checked": bool(it[3])}
                    for it in items
                ]
            })
    return result


def create_smart_list(chat_id, name, category="general"):
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO smart_lists (user_id, name, category) "
                "SELECT id, :n, :cat FROM users WHERE telegram_chat_id = :c RETURNING id"
            ),
            {"n": name.strip(), "cat": category, "c": chat_id},
        ).first()
    return row[0] if row else None


def add_smart_list_item(chat_id, list_id, title, quantity=None):
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


def toggle_smart_list_item(chat_id, item_id, checked=None):
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
    with engine.begin() as conn:
        res = conn.execute(
            text(
                "DELETE FROM smart_lists WHERE id = :lid AND user_id = (SELECT id FROM users WHERE telegram_chat_id = :c)"
            ),
            {"lid": list_id, "c": chat_id},
        )
    return (res.rowcount or 0) > 0


def find_or_create_smart_list(chat_id, name):
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
    return create_smart_list(chat_id, name_clean)


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
                "commitments = COALESCE(:com, commitments), "
                "context_notes = CASE "
                "WHEN :ctx IS NOT NULL AND context_notes IS NOT NULL THEN context_notes || ' | ' || :ctx "
                "WHEN :ctx IS NOT NULL THEN :ctx ELSE context_notes END "
                "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
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

def log_chat_message(chat_id, role, message):
    """Saves a conversation turn permanently in PostgreSQL."""
    if not message or not str(message).strip():
        return
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO chat_logs (user_id, role, message) "
                    "SELECT id, :role, :msg FROM users WHERE telegram_chat_id = :cid"
                ),
                {"cid": chat_id, "role": role, "msg": str(message).strip()}
            )
    except Exception as exc:
        print("log_chat_message error:", repr(exc))


def get_recent_chat_history(chat_id, limit=15):
    """Retrieves the recent conversation history in chronological order."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT role, message, created_at FROM chat_logs "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "ORDER BY id DESC LIMIT :lim"
                ),
                {"cid": chat_id, "lim": limit}
            ).fetchall()
        # Return chronological order (oldest to newest)
        return [{"role": r[0], "text": r[1]} for r in reversed(rows)]
    except Exception as exc:
        print("get_recent_chat_history error:", repr(exc))
        return []


def save_user_fact(chat_id, fact, category="general"):
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

