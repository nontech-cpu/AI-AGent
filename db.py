import os
import json
import logging
from typing import Optional, List, Dict, Any, Union
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

logger = logging.getLogger(__name__)

load_dotenv()
is_production = os.getenv("ENV", "").lower() == "production" or os.getenv("RENDER", "").lower() == "true" or os.getenv("ENVIRONMENT", "").lower() == "production"
raw_db_url = os.getenv("DATABASE_URL")
if not raw_db_url:
    if is_production:
        raise RuntimeError("FATAL: DATABASE_URL environment variable is missing in production environment. Refusing silent in-memory SQLite fallback.")
    raw_db_url = "sqlite:///:memory:"

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
    engine = create_engine(
        raw_db_url,
        pool_size=10,
        max_overflow=20,
        pool_timeout=20,
        pool_recycle=300,
        pool_pre_ping=True
    )


def give_consent(chat_id):
    chat_id = canonical_chat_id(chat_id)
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
    # If onboarding happens after morning hours (after 10:30 AM IST),
    # mark today's morning briefing as skipped so new user never receives 'Good Morning' in the evening!
    try:
        from datetime import datetime
        from timing import IST
        import core.timeutil as timeutil
        now_ist = timeutil.now(IST)
        if now_ist.hour >= 11:
            record_morning_briefing_sent(chat_id, now_ist.strftime("%Y-%m-%d"), "onboarding_afternoon_skipped")
    except Exception:
        pass


def has_consent(chat_id):
    chat_id = canonical_chat_id(chat_id)
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


_USER_LANG_CACHE: dict = {}

def get_user_language(chat_id):
    chat_id = canonical_chat_id(chat_id)
    cached = _USER_LANG_CACHE.get(chat_id)
    import time as _t
    now_ts = _t.time()
    if cached and (now_ts - cached.get("ts", 0) < 180.0):
        return cached.get("lang")
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT language FROM users WHERE telegram_chat_id = :c"),
                {"c": chat_id},
            ).first()
            if row and row[0]:
                lang = str(row[0]).strip().lower()
                _USER_LANG_CACHE[chat_id] = {"ts": now_ts, "lang": lang}
                return lang
    except Exception as exc:
        print("get_user_language error:", exc)
    return None


def set_user_language(chat_id, lang: str):
    chat_id = canonical_chat_id(chat_id)
    clean_lang = str(lang).strip().lower()
    import time as _t
    _USER_LANG_CACHE[chat_id] = {"ts": _t.time(), "lang": clean_lang}
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET language = :l WHERE telegram_chat_id = :c"),
                {"l": clean_lang, "c": chat_id},
            )
    except Exception as exc:
        print("set_user_language error:", exc)


def set_user_preference(chat_id, reply_language: str, tone: str = "concise"):
    chat_id = canonical_chat_id(chat_id)
    clean_lang = str(reply_language or "english").strip().lower()
    clean_tone = str(tone or "concise").strip().lower()
    import time as _t
    _USER_LANG_CACHE[chat_id] = {"ts": _t.time(), "lang": clean_lang}
    try:
        with engine.begin() as conn:
            conn.execute(
                text("UPDATE users SET language = :l, persona = :p WHERE telegram_chat_id = :c"),
                {"l": clean_lang, "p": clean_tone, "c": chat_id},
            )
        save_note_or_fact(chat_id, f"User Communication Preference: Language={clean_lang.title()}, Tone={clean_tone.title()}", category="preference")
    except Exception as exc:
        print("set_user_preference error:", exc)
    return {"reply_language": clean_lang, "tone": clean_tone}


def get_user_preference(chat_id):
    chat_id = canonical_chat_id(chat_id)
    import time as _t
    now_ts = _t.time()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT language, persona FROM users WHERE telegram_chat_id = :c"),
                {"c": chat_id},
            ).first()
            if row:
                lang = (str(row[0]).strip() if row[0] else "")
                tone = (str(row[1]).strip() if row[1] else "concise")
                _USER_LANG_CACHE[chat_id] = {"ts": now_ts, "lang": lang}
                return {"reply_language": lang, "tone": tone}
    except Exception as exc:
        print("get_user_preference error:", exc)
    return {"reply_language": "english", "tone": "concise"}


_MEMORY_ITEMS_RECURRENCE_INITIALIZED = False
_MEMORY_ITEMS_STATUS_INITIALIZED = False

def _init_memory_items_status_column():
    global _MEMORY_ITEMS_STATUS_INITIALIZED
    if _MEMORY_ITEMS_STATUS_INITIALIZED:
        return
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(text("ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'active';"))
            else:
                try:
                    conn.execute(text("ALTER TABLE memory_items ADD COLUMN status TEXT DEFAULT 'active';"))
                except Exception:
                    pass
        _MEMORY_ITEMS_STATUS_INITIALIZED = True
    except Exception as exc:
        print("_init_memory_items_status_column error:", exc)

def _init_memory_items_recurrence_column():
    global _MEMORY_ITEMS_RECURRENCE_INITIALIZED
    if _MEMORY_ITEMS_RECURRENCE_INITIALIZED:
        return
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(text("ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS recurrence VARCHAR(50) DEFAULT 'none';"))
            else:
                try:
                    conn.execute(text("ALTER TABLE memory_items ADD COLUMN recurrence TEXT DEFAULT 'none';"))
                except Exception:
                    pass
        _MEMORY_ITEMS_RECURRENCE_INITIALIZED = True
    except Exception as exc:
        print("_init_memory_items_recurrence_column error:", exc)


def save_memory(chat_id, category, title, amount, due_date, due_time, duration_minutes=30, recurrence="none"):
    _init_memory_items_recurrence_column()
    chat_id = canonical_chat_id(chat_id)
    category = category or "appointments"
    tm = due_time
    d = due_date
    dur = int(duration_minutes) if duration_minutes else 30
    rec = (recurrence or "none").strip().lower()
    if engine.dialect.name == "sqlite":
        if hasattr(due_time, "strftime"):
            tm = due_time.strftime("%H:%M:%S")
        if hasattr(due_date, "isoformat"):
            d = due_date.isoformat()
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "INSERT INTO memory_items "
                "(user_id, category, title, amount, due_date, due_time, duration_minutes, recurrence) "
                "SELECT id, :cat, :t, :a, :d, :tm, :dur, :rec FROM users "
                "WHERE telegram_chat_id = :c RETURNING id"
            ),
            {"cat": category, "t": title, "a": amount, "d": d,
             "tm": tm, "dur": dur, "rec": rec, "c": chat_id},
        ).first()
    return row[0] if row else None


def list_memories(chat_id):
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.category, m.title, m.amount, m.due_date, m.due_time, COALESCE(m.duration_minutes, 30) AS duration_minutes "
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
    _init_memory_items_recurrence_column()
    _init_memory_items_status_column()
    d_val = d.isoformat() if hasattr(d, "isoformat") and engine.dialect.name == "sqlite" else d
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.category, m.title, m.amount, m.due_time, m.id, COALESCE(m.duration_minutes, 30) AS duration_minutes, m.due_date, COALESCE(m.recurrence, 'none') AS recurrence "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c "
                "  AND (m.status IS NULL OR m.status != 'deleted') "
                "  AND (m.due_date = :d OR (m.recurrence IS NOT NULL AND m.recurrence != 'none' AND m.due_date <= :d)) "
                "ORDER BY m.due_time NULLS FIRST, m.id"
            ),
            {"c": chat_id, "d": d_val},
        ).fetchall()

    if not rows:
        return []

    from datetime import date as _dt_date
    if isinstance(d, str):
        try:
            target_date = _dt_date.fromisoformat(d.strip()[:10])
        except Exception:
            target_date = None
    elif isinstance(d, _dt_date):
        target_date = d
    else:
        target_date = None

    projected = []
    for r in rows:
        row_due_d = r[6]
        rec = (r[7] if len(r) > 7 else "none") or "none"
        if rec == "none":
            projected.append(r[:7])
            continue

        if target_date is None:
            projected.append(r[:7])
            continue

        if isinstance(row_due_d, str):
            try:
                base_date = _dt_date.fromisoformat(row_due_d.strip()[:10])
            except Exception:
                base_date = target_date
        elif isinstance(row_due_d, _dt_date):
            base_date = row_due_d
        else:
            base_date = target_date

        if target_date < base_date:
            continue

        if rec == "daily":
            projected.append(r[:7])
        elif rec == "weekdays" and target_date.weekday() < 5:
            projected.append(r[:7])
        elif rec == "weekly" and target_date.weekday() == base_date.weekday():
            projected.append(r[:7])

    return projected


def items_for_date_range(chat_id, start_d, end_d):
    chat_id = canonical_chat_id(chat_id)
    _init_memory_items_recurrence_column()
    _init_memory_items_status_column()
    s_val = start_d.isoformat() if hasattr(start_d, "isoformat") and engine.dialect.name == "sqlite" else start_d
    e_val = end_d.isoformat() if hasattr(end_d, "isoformat") and engine.dialect.name == "sqlite" else end_d
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.category, m.title, m.amount, m.due_time, m.due_date, m.id, COALESCE(m.duration_minutes, 30) AS duration_minutes, COALESCE(m.recurrence, 'none') AS recurrence "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c "
                "  AND (m.status IS NULL OR m.status != 'deleted') "
                "  AND ((m.due_date >= :s AND m.due_date <= :e) OR (m.recurrence IS NOT NULL AND m.recurrence != 'none' AND m.due_date <= :e)) "
                "ORDER BY m.due_date ASC, m.due_time NULLS FIRST, m.id"
            ),
            {"c": chat_id, "s": s_val, "e": e_val},
        ).fetchall()

    if not rows:
        return []

    projected = []
    for r in rows:
        projected.append(r[:7])
    return projected


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


def update_memory_item_full(chat_id, item_id, title=None, due_time=None, due_date=None, duration_minutes=None, recurrence=None):
    chat_id = canonical_chat_id(chat_id)
    _init_memory_items_recurrence_column()
    updates = []
    params = {"id": item_id, "c": chat_id}
    if title is not None:
        updates.append("title = :t")
        params["t"] = title
    if due_time is not None:
        updates.append("due_time = :tm")
        params["tm"] = due_time.strftime("%H:%M:%S") if (engine.dialect.name == "sqlite" and hasattr(due_time, "strftime")) else due_time
    if due_date is not None:
        updates.append("due_date = :d")
        params["d"] = due_date.isoformat() if (engine.dialect.name == "sqlite" and hasattr(due_date, "isoformat")) else due_date
    if duration_minutes is not None:
        updates.append("duration_minutes = :dur")
        params["dur"] = int(duration_minutes)
    if recurrence is not None:
        updates.append("recurrence = :rec")
        params["rec"] = (recurrence or "none").strip().lower()
    if updates:
        sql = f"UPDATE memory_items SET {', '.join(updates)} WHERE id = :id AND user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c)"
        with engine.begin() as conn:
            conn.execute(text(sql), params)



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
        ("DELETE FROM turn_traces WHERE chat_id = :c", {"c": chat_id}),
        ("UPDATE users SET state = NULL, state_data = NULL, persona = NULL, consented_at = NULL WHERE id = :u", {"u": uid}),
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
        from agent.prompt import _USER_META_CACHE, _UNIFIED_PROMPT_CACHE
        _USER_META_CACHE.pop(chat_id, None)
        _UNIFIED_PROMPT_CACHE.pop(canonical_chat_id(chat_id), None)
        _UNIFIED_PROMPT_CACHE.pop(chat_id, None)
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


# ---------- Reminders Scheduler & Multi-Instance Locking ----------

_REMINDERS_COLS_INITIALIZED = False

def _init_reminders_scheduler_columns():
    global _REMINDERS_COLS_INITIALIZED
    if _REMINDERS_COLS_INITIALIZED:
        return
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(text("""
                    ALTER TABLE reminders ADD COLUMN IF NOT EXISTS due_at TIMESTAMPTZ;
                    ALTER TABLE reminders ADD COLUMN IF NOT EXISTS sent_at TIMESTAMPTZ;
                    ALTER TABLE reminders ADD COLUMN IF NOT EXISTS claimed_by VARCHAR(64);
                    ALTER TABLE reminders ADD COLUMN IF NOT EXISTS claimed_at TIMESTAMPTZ;
                    ALTER TABLE reminders ADD COLUMN IF NOT EXISTS status VARCHAR(50) DEFAULT 'active';
                """))
            else:
                for col, c_type in [("due_at", "TIMESTAMP"), ("sent_at", "TIMESTAMP"), ("claimed_by", "TEXT"), ("claimed_at", "TIMESTAMP"), ("status", "TEXT DEFAULT 'active'")]:
                    try:
                        conn.execute(text(f"ALTER TABLE reminders ADD COLUMN {col} {c_type}"))
                    except Exception:
                        pass
        _REMINDERS_COLS_INITIALIZED = True
    except Exception as exc:
        logger.debug(f"Reminders scheduler column init notice: {exc}")


def add_reminders(item_id, reminders):
    _init_reminders_scheduler_columns()
    with engine.begin() as conn:
        for remind_at, label in reminders:
            conn.execute(
                text(
                    "INSERT INTO reminders (item_id, remind_at, due_at, label) "
                    "VALUES (:i, :a, :a, :l)"
                ),
                {"i": item_id, "a": remind_at, "l": label},
            )


def claim_pending_reminders(worker_id: str = "worker_1", now = None, limit: int = 50, stale_seconds: int = 300):
    """
    Multi-instance scheduler with concurrency control.
    Uses FOR UPDATE SKIP LOCKED on PostgreSQL to prevent double-sending across Render instances.
    On SQLite, uses an atomic transaction.
    """
    from datetime import datetime, timedelta, timezone
    from timing import IST
    import core.timeutil as timeutil

    if now is None:
        now = timeutil.now(IST)

    _init_reminders_scheduler_columns()
    is_pg = engine.dialect.name == "postgresql"
    stale_cutoff = now - timedelta(seconds=stale_seconds)

    with engine.begin() as conn:
        if is_pg:
            query = text("""
                SELECT r.id, u.telegram_chat_id, m.category, m.title, m.amount,
                       m.due_date, m.due_time, r.label, COALESCE(r.due_at, r.remind_at) as scheduled_at
                FROM reminders r
                JOIN memory_items m ON m.id = r.item_id
                JOIN users u ON u.id = m.user_id
                WHERE (r.sent IS NOT TRUE)
                  AND (m.status IS NULL OR m.status != 'deleted')
                  AND (r.status IS NULL OR r.status != 'deleted')
                  AND (r.claimed_at IS NULL OR r.claimed_at < :stale_cutoff)
                  AND COALESCE(r.due_at, r.remind_at) <= :now
                ORDER BY COALESCE(r.due_at, r.remind_at)
                FOR UPDATE OF r SKIP LOCKED
                LIMIT :limit
            """)
            rows = conn.execute(query, {"now": now, "stale_cutoff": stale_cutoff, "limit": limit}).fetchall()
            if rows:
                claimed_ids = [r[0] for r in rows]
                conn.execute(
                    text("UPDATE reminders SET claimed_by = :wid, claimed_at = :now WHERE id = ANY(:ids)"),
                    {"wid": worker_id, "now": now, "ids": claimed_ids}
                )
            return rows
        else:
            query = text("""
                SELECT r.id, u.telegram_chat_id, m.category, m.title, m.amount,
                       m.due_date, m.due_time, r.label, COALESCE(r.due_at, r.remind_at) as scheduled_at
                FROM reminders r
                JOIN memory_items m ON m.id = r.item_id
                JOIN users u ON u.id = m.user_id
                WHERE (r.sent IS NOT TRUE OR r.sent = 0)
                  AND (m.status IS NULL OR m.status != 'deleted')
                  AND (r.status IS NULL OR r.status != 'deleted')
                  AND (r.claimed_at IS NULL OR r.claimed_at < :stale_cutoff)
                  AND COALESCE(r.due_at, r.remind_at) <= :now
                ORDER BY COALESCE(r.due_at, r.remind_at)
                LIMIT :limit
            """)
            rows = conn.execute(query, {"now": now, "stale_cutoff": stale_cutoff, "limit": limit}).fetchall()
            if rows:
                claimed_ids = [r[0] for r in rows]
                for cid in claimed_ids:
                    conn.execute(
                        text("UPDATE reminders SET claimed_by = :wid, claimed_at = :now WHERE id = :id"),
                        {"wid": worker_id, "now": now, "id": cid}
                    )
            return rows


def get_pending_reminders(now):
    # Compatibility query for legacy callers
    _init_reminders_scheduler_columns()
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT r.id, u.telegram_chat_id, m.category, m.title, m.amount, "
                "m.due_date, m.due_time, r.label "
                "FROM reminders r "
                "JOIN memory_items m ON m.id = r.item_id "
                "JOIN users u ON u.id = m.user_id "
                "WHERE (r.sent IS NOT TRUE OR r.sent = 0) "
                "AND (m.status IS NULL OR m.status != 'deleted') "
                "AND (r.status IS NULL OR r.status != 'deleted') "
                "AND COALESCE(r.due_at, r.remind_at) <= :now "
                "ORDER BY COALESCE(r.due_at, r.remind_at)"
            ),
            {"now": now},
        ).fetchall()


def get_upcoming_reminders(chat_id, now):
    _init_reminders_scheduler_columns()
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT COALESCE(r.due_at, r.remind_at) as remind_at, r.label, m.category, m.title, m.amount, m.due_time "
                "FROM reminders r "
                "JOIN memory_items m ON m.id = r.item_id "
                "JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c "
                "AND (r.sent IS NOT TRUE OR r.sent = 0) "
                "AND COALESCE(r.due_at, r.remind_at) > :now "
                "ORDER BY COALESCE(r.due_at, r.remind_at) LIMIT 30"
            ),
            {"c": chat_id, "now": now},
        ).fetchall()


def mark_sent(reminder_id, sent_at=None):
    if sent_at is None:
        from timing import IST
        import core.timeutil as timeutil
        sent_at = timeutil.now(IST)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE reminders SET sent = true, sent_at = :s, claimed_by = NULL, claimed_at = NULL WHERE id = :i"),
            {"i": reminder_id, "s": sent_at},
        )
        # Self-Advancing Recurring Reminder Dispatcher
        try:
            row = conn.execute(
                text("""
                    SELECT r.item_id, r.remind_at, r.due_at, r.label, m.due_date, m.due_time, COALESCE(m.recurrence, 'none')
                    FROM reminders r
                    JOIN memory_items m ON m.id = r.item_id
                    WHERE r.id = :i
                """),
                {"i": reminder_id}
            ).first()
            if row and row[6] in ("daily", "weekly", "weekdays"):
                item_id, remind_at, due_at, label, base_d, due_t, rec = row
                from datetime import datetime, timedelta
                if isinstance(remind_at, str):
                    try:
                        remind_at = datetime.fromisoformat(remind_at.replace("Z", "+00:00"))
                    except Exception:
                        pass
                if isinstance(due_at, str):
                    try:
                        due_at = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                    except Exception:
                        pass
                step_days = 7 if rec == "weekly" else 1
                next_remind_at = (remind_at + timedelta(days=step_days)) if remind_at else None
                next_due_at = (due_at + timedelta(days=step_days)) if due_at else None
                if rec == "weekdays" and next_due_at and next_due_at.weekday() >= 5:
                    add_days = 2 if next_due_at.weekday() == 5 else 1
                    next_remind_at = (next_remind_at + timedelta(days=add_days)) if next_remind_at else None
                    next_due_at = (next_due_at + timedelta(days=add_days)) if next_due_at else None

                existing_next = conn.execute(
                    text("SELECT id FROM reminders WHERE item_id = :id AND (sent IS NOT TRUE OR sent = 0)"),
                    {"id": item_id}
                ).first()
                if not existing_next and next_remind_at:
                    conn.execute(
                        text("INSERT INTO reminders (item_id, remind_at, due_at, label) VALUES (:item_id, :remind_at, :due_at, :label)"),
                        {"item_id": item_id, "remind_at": next_remind_at, "due_at": next_due_at, "label": label}
                    )
        except Exception as adv_err:
            pass

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

# ---------- Dynamic User Identity Mapping (Phase 3) ----------
# Replaced hardcoded dictionary with dynamic database-backed identity_links table
_IDENTITY_LINKS_CACHE = {}
_IDENTITY_LINKS_CACHE_EXPIRY = 0.0

def get_identity_links() -> dict:
    global _IDENTITY_LINKS_CACHE, _IDENTITY_LINKS_CACHE_EXPIRY
    import time as _t
    now_ts = _t.time()
    if _IDENTITY_LINKS_CACHE and now_ts < _IDENTITY_LINKS_CACHE_EXPIRY:
        return _IDENTITY_LINKS_CACHE

    links = {}
    try:
        if engine.dialect.name == "sqlite":
            with engine.begin() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS identity_links (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        source_chat_id INTEGER UNIQUE NOT NULL,
                        canonical_chat_id INTEGER NOT NULL,
                        label TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
                conn.execute(text("""
                    INSERT OR IGNORE INTO identity_links (source_chat_id, canonical_chat_id, label)
                    VALUES (1234828592, 1758056012, 'Neerav secondary account')
                """))
        with engine.connect() as conn:
            rows = conn.execute(text("SELECT source_chat_id, canonical_chat_id FROM identity_links")).fetchall()
            for r in rows:
                links[int(r[0])] = int(r[1])
        _IDENTITY_LINKS_CACHE = links
        _IDENTITY_LINKS_CACHE_EXPIRY = now_ts + 60.0  # 60s TTL cache
    except Exception:
        pass
    return links

def link_user_identities(source_chat_id: int, canonical_chat_id: int, label: str = "linked_account"):
    """Persist identity link in database and invalidate cache."""
    global _IDENTITY_LINKS_CACHE_EXPIRY
    is_pg = engine.dialect.name == "postgresql"
    with engine.begin() as conn:
        if is_pg:
            conn.execute(text("""
                INSERT INTO identity_links (source_chat_id, canonical_chat_id, label, created_at)
                VALUES (:src, :canon, :lbl, CURRENT_TIMESTAMP)
                ON CONFLICT (source_chat_id) DO UPDATE SET canonical_chat_id = :canon, label = :lbl
            """), {"src": int(source_chat_id), "canon": int(canonical_chat_id), "lbl": label})
        else:
            conn.execute(text("""
                INSERT OR REPLACE INTO identity_links (source_chat_id, canonical_chat_id, label, created_at)
                VALUES (:src, :canon, :lbl, CURRENT_TIMESTAMP)
            """), {"src": int(source_chat_id), "canon": int(canonical_chat_id), "lbl": label})
    _IDENTITY_LINKS_CACHE_EXPIRY = 0.0

def unlink_user_identity(source_chat_id: int):
    """Removes an identity link from database and invalidates cache."""
    global _IDENTITY_LINKS_CACHE_EXPIRY
    try:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM identity_links WHERE source_chat_id = :src"), {"src": int(source_chat_id)})
    except Exception as exc:
        logger.warning(f"unlink_user_identity error: {exc}")
    _IDENTITY_LINKS_CACHE_EXPIRY = 0.0

class _CanonicalMapProxy(dict):
    """Compatibility proxy that delegates lookups to get_identity_links()."""
    def get(self, key, default=None):
        return get_identity_links().get(key, default)
    def items(self):
        return get_identity_links().items()
    def __getitem__(self, key):
        return get_identity_links()[key]
    def __contains__(self, key):
        return key in get_identity_links()
    def __len__(self):
        return len(get_identity_links())

CANONICAL_CHAT_ID_MAP = _CanonicalMapProxy()

def canonical_chat_id(chat_id):
    try:
        cid_int = int(chat_id)
        links = get_identity_links()
        return links.get(cid_int, cid_int)
    except Exception:
        return chat_id


def get_all_linked_chat_ids(chat_id: int) -> set[int]:
    """Returns all Telegram chat IDs associated with this user identity for broadcast notifications."""
    try:
        cid_int = int(chat_id)
    except Exception:
        return {chat_id}
    canon = canonical_chat_id(cid_int)
    targets = {cid_int, canon}
    links = get_identity_links()
    for orig, c in links.items():
        if c == canon:
            targets.add(orig)
    return targets


def get_most_active_chat_id(chat_id: int) -> int:
    """
    Given a chat_id, resolves all linked identity chat IDs and returns the single chat ID
    that had the most recent user interaction. Prevents stale alerts on dormant sessions.
    """
    try:
        cid_int = int(chat_id)
    except Exception:
        return chat_id

    linked = list(get_all_linked_chat_ids(cid_int))
    if len(linked) <= 1:
        return cid_int

    try:
        with engine.connect() as conn:
            # Query recent turn_traces across all linked IDs
            row = conn.execute(
                text("SELECT chat_id FROM turn_traces WHERE chat_id = ANY(:cids) ORDER BY id DESC LIMIT 1"),
                {"cids": linked}
            ).first()
            if row and row[0]:
                return int(row[0])
    except Exception:
        try:
            with engine.connect() as conn:
                row = conn.execute(
                    text("SELECT u.telegram_chat_id FROM chat_logs cl JOIN users u ON cl.user_id = u.id WHERE u.telegram_chat_id = ANY(:cids) ORDER BY cl.id DESC LIMIT 1"),
                    {"cids": linked}
                ).first()
                if row and row[0]:
                    return int(row[0])
        except Exception:
            pass

    return cid_int


def get_user_display_name(chat_id: int) -> str:
    """Return the first name or username of the user for friendly, natural greetings without hardcoding."""
    try:
        cid_int = int(chat_id)
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT first_name, username FROM users WHERE telegram_chat_id = :cid LIMIT 1"),
                {"cid": cid_int}
            ).first()
            if row:
                if row[0] and str(row[0]).strip():
                    return str(row[0]).strip()
                if row[1] and str(row[1]).strip():
                    return str(row[1]).strip().lstrip("@")
    except Exception as exc:
        logger.debug(f"get_user_display_name error for {chat_id}: {exc}")
    return ""


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
    chat_id = canonical_chat_id(chat_id)
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
    chat_id = canonical_chat_id(chat_id)
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
    chat_id = canonical_chat_id(chat_id)
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT m.id, m.title, m.due_date, m.due_time "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.category = 'appointments' "
                "AND (m.status IS NULL OR m.status != 'deleted') "
                "AND m.due_date = :d AND m.due_time IS NOT NULL AND m.due_time < :t "
                "AND (m.title ILIKE '%meeting%' OR m.title ILIKE '%metting%' "
                "OR m.title ILIKE '%meetting%' OR m.title ILIKE '%meetng%' "
                "OR m.title ILIKE '%call%' OR m.title ILIKE '%appointment%') "
                "ORDER BY m.due_time, m.id"
            ),
            {"c": chat_id, "d": due_date, "t": before_time},
        ).fetchall()


def soft_delete_reminders(chat_id, item_ids, scope: str = "all"):
    """Soft delete reminders (status='deleted') strictly by exact IDs owned by this user.
    If scope == 'this' and the item is recurring, only the current occurrence is deleted,
    and the next recurrence is scheduled. If scope == 'all', the entire recurring series is deleted.
    """
    _init_memory_items_status_column()
    chat_id = canonical_chat_id(chat_id)
    ids = [int(v) for v in item_ids if isinstance(v, int) or (isinstance(v, str) and v.isdigit())]
    if not ids:
        return []

    with engine.begin() as conn:
        is_pg = engine.dialect.name == "postgresql"
        if is_pg:
            owned = conn.execute(
                text(
                    "SELECT m.id, m.title, COALESCE(m.recurrence, 'none') FROM memory_items m JOIN users u ON u.id = m.user_id "
                    "WHERE u.telegram_chat_id = :c AND m.id = ANY(:ids) AND (m.status IS NULL OR m.status != 'deleted')"
                ),
                {"c": chat_id, "ids": ids},
            ).fetchall()
        else:
            placeholders = ", ".join([f":id_{i}" for i in range(len(ids))])
            param_dict = {f"id_{i}": val for i, val in enumerate(ids)}
            param_dict["c"] = chat_id
            owned = conn.execute(
                text(
                    f"SELECT m.id, m.title, COALESCE(m.recurrence, 'none') FROM memory_items m JOIN users u ON u.id = m.user_id "
                    f"WHERE u.telegram_chat_id = :c AND m.id IN ({placeholders}) AND (m.status IS NULL OR m.status != 'deleted')"
                ),
                param_dict,
            ).fetchall()

        if not owned:
            return []

        safe_ids = [int(r[0]) for r in owned]

        if scope == "this":
            # Delete only current active occurrence in reminders table
            from datetime import timedelta
            for r_row in owned:
                m_id, m_title, m_rec = r_row[0], r_row[1], r_row[2]
                curr_rem = conn.execute(
                    text("SELECT id, remind_at, due_at, label FROM reminders WHERE item_id = :mid AND (sent IS NOT TRUE OR sent = 0) AND (status IS NULL OR status != 'deleted') ORDER BY id ASC LIMIT 1"),
                    {"mid": m_id}
                ).first()
                if curr_rem:
                    rem_pk, remind_at, due_at, label = curr_rem
                    conn.execute(
                        text("UPDATE reminders SET status = 'deleted' WHERE id = :rid"),
                        {"rid": rem_pk}
                    )
                    # Advance to next recurrence if recurring
                    if m_rec in ("daily", "weekly", "weekdays"):
                        from datetime import datetime
                        if isinstance(remind_at, str):
                            try:
                                remind_at = datetime.fromisoformat(remind_at.replace("Z", "+00:00"))
                            except Exception:
                                pass
                        if isinstance(due_at, str):
                            try:
                                due_at = datetime.fromisoformat(due_at.replace("Z", "+00:00"))
                            except Exception:
                                pass
                        step_days = 7 if m_rec == "weekly" else 1
                        next_remind_at = (remind_at + timedelta(days=step_days)) if remind_at else None
                        next_due_at = (due_at + timedelta(days=step_days)) if due_at else None
                        if m_rec == "weekdays" and next_due_at and next_due_at.weekday() >= 5:
                            add_days = 2 if next_due_at.weekday() == 5 else 1
                            next_remind_at = (next_remind_at + timedelta(days=add_days)) if next_remind_at else None
                            next_due_at = (next_due_at + timedelta(days=add_days)) if next_due_at else None
                        if next_remind_at:
                            conn.execute(
                                text("INSERT INTO reminders (item_id, remind_at, due_at, label) VALUES (:item_id, :remind_at, :due_at, :label)"),
                                {"item_id": m_id, "remind_at": next_remind_at, "due_at": next_due_at, "label": label}
                            )
                else:
                    # No pending reminder row; mark memory_item deleted
                    conn.execute(text("UPDATE memory_items SET status = 'deleted' WHERE id = :mid"), {"mid": m_id})
        else:
            # scope == "all": Delete parent memory_item and all child reminders
            if is_pg:
                conn.execute(
                    text("UPDATE memory_items SET status = 'deleted' WHERE id = ANY(:ids)"),
                    {"ids": safe_ids},
                )
                try:
                    conn.execute(
                        text("UPDATE reminders SET status = 'deleted' WHERE item_id = ANY(:ids)"),
                        {"ids": safe_ids},
                    )
                except Exception:
                    pass
            else:
                placeholders = ", ".join([f":id_{i}" for i in range(len(safe_ids))])
                param_dict = {f"id_{i}": val for i, val in enumerate(safe_ids)}
                conn.execute(
                    text(f"UPDATE memory_items SET status = 'deleted' WHERE id IN ({placeholders})"),
                    param_dict,
                )
                try:
                    conn.execute(
                        text(f"UPDATE reminders SET status = 'deleted' WHERE item_id IN ({placeholders})"),
                        param_dict,
                    )
                except Exception:
                    pass

        return [(r[0], r[1]) for r in owned]


def restore_reminder(chat_id, rem_id: int):
    """Restores a soft-deleted reminder back to active status by exact ID."""
    _init_memory_items_status_column()
    chat_id = canonical_chat_id(chat_id)
    with engine.begin() as conn:
        row = conn.execute(
            text(
                "SELECT m.id, m.title FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.id = :id AND m.status = 'deleted'"
            ),
            {"c": chat_id, "id": int(rem_id)},
        ).first()
        if not row:
            return None
        conn.execute(
            text("UPDATE memory_items SET status = 'active' WHERE id = :id"),
            {"id": int(rem_id)},
        )
        try:
            conn.execute(
                text("UPDATE reminders SET status = 'active' WHERE item_id = :id"),
                {"id": int(rem_id)},
            )
        except Exception:
            pass
        return {"id": row[0], "title": row[1]}


def delete_memory_items(chat_id, item_ids):
    """Delete only previewed items owned by this chat, plus their reminder rows."""
    chat_id = canonical_chat_id(chat_id)
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
    chat_id = canonical_chat_id(chat_id)
    time_labels = {f"{int(hour):02d}:{int(minute):02d}" for hour, minute in times}
    if not time_labels:
        return []
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT m.id, m.title, m.due_date, m.due_time "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.category = 'appointments' "
                "AND (m.status IS NULL OR m.status != 'deleted') "
                "AND m.due_date = :d AND m.due_time IS NOT NULL "
                "ORDER BY m.due_time, m.id"
            ),
            {"c": chat_id, "d": due_date},
        ).fetchall()
    matched = []
    meeting_kws = ("meeting", "appointment", "call", "session", "sync")
    for r in rows:
        r_tm = str(r[3])[:5] if r[3] else ""
        r_title = (r[1] or "").lower()
        if r_tm in time_labels and any(kw in r_title for kw in meeting_kws):
            matched.append(r)
    return matched


def find_recent_meetings(chat_id, limit=3):
    """Return this user's most recently created or upcoming meeting reminders."""
    chat_id = canonical_chat_id(chat_id)
    with engine.connect() as conn:
        return conn.execute(
            text(
                "SELECT m.id, m.title, m.due_date, m.due_time "
                "FROM memory_items m JOIN users u ON u.id = m.user_id "
                "WHERE u.telegram_chat_id = :c AND m.category = 'appointments' "
                "AND (m.status IS NULL OR m.status != 'deleted') "
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

_USER_RULES_CACHE: dict = {}

def get_user_rules(chat_id):
    chat_id = canonical_chat_id(chat_id)
    cached = _USER_RULES_CACHE.get(chat_id)
    import time as _t
    now_ts = _t.time()
    if cached and (now_ts - cached.get("ts", 0) < 120.0):
        return cached.get("rules", [])
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text(
                    "SELECT r.id, r.rule_text, r.is_active, r.created_at "
                    "FROM user_rules r JOIN users u ON u.id = r.user_id "
                    "WHERE u.telegram_chat_id = :c ORDER BY r.id ASC"
                ),
                {"c": chat_id},
            ).fetchall()
        res = [
            {"id": r[0], "rule": r[1], "active": bool(r[2]), "created_at": r[3].isoformat() if hasattr(r[3], "isoformat") else str(r[3])}
            for r in rows
        ]
        _USER_RULES_CACHE[chat_id] = {"ts": now_ts, "rules": res}
        return res
    except Exception as exc:
        logger.error(f"get_user_rules error: {exc}")
        return []


save_user_rule = None  # set below
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

save_user_rule = add_user_rule

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

_users_timezone_checked = False

def _ensure_users_timezone_column():
    global _users_timezone_checked
    if _users_timezone_checked:
        return
    try:
        with engine.begin() as conn:
            is_pg = engine.dialect.name == "postgresql"
            if is_pg:
                conn.execute(text("ALTER TABLE users ADD COLUMN IF NOT EXISTS timezone VARCHAR(64)"))
            else:
                try:
                    conn.execute(text("ALTER TABLE users ADD COLUMN timezone VARCHAR(64)"))
                except Exception:
                    pass
        _users_timezone_checked = True
    except Exception:
        pass


def set_user_timezone(chat_id: int, timezone_str: str):
    """Saves user's preferred local timezone directly to database."""
    chat_id = canonical_chat_id(chat_id)
    _ensure_users_timezone_column()
    try:
        with engine.begin() as conn:
            is_pg = engine.dialect.name == "postgresql"
            if is_pg:
                conn.execute(
                    text("""
                        INSERT INTO users (telegram_chat_id, timezone)
                        VALUES (:c, :tz)
                        ON CONFLICT (telegram_chat_id)
                        DO UPDATE SET timezone = :tz
                    """),
                    {"tz": timezone_str.strip(), "c": chat_id}
                )
            else:
                conn.execute(
                    text("INSERT OR IGNORE INTO users (telegram_chat_id) VALUES (:c)"),
                    {"c": chat_id}
                )
                conn.execute(
                    text("UPDATE users SET timezone = :tz WHERE telegram_chat_id = :c"),
                    {"tz": timezone_str.strip(), "c": chat_id}
                )
    except Exception as exc:
        logger.warning(f"Error setting user timezone for {chat_id}: {exc}")


def get_user_timezone(chat_id: int) -> Optional[str]:
    """Retrieves user's stored local timezone from database."""
    chat_id = canonical_chat_id(chat_id)
    _ensure_users_timezone_column()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT timezone FROM users WHERE telegram_chat_id = :c"),
                {"c": chat_id}
            ).first()
            if row and row[0]:
                return str(row[0]).strip()
    except Exception:
        pass
    return None


_CHAT_HISTORY_CACHE: dict = {}
_chat_logs_channel_checked = False


def _ensure_chat_logs_channel_column():
    global _chat_logs_channel_checked
    if _chat_logs_channel_checked:
        return
    try:
        with engine.begin() as conn:
            is_pg = engine.dialect.name == "postgresql"
            if is_pg:
                conn.execute(text("ALTER TABLE chat_logs ADD COLUMN IF NOT EXISTS channel TEXT DEFAULT 'telegram'"))
            else:
                try:
                    conn.execute(text("ALTER TABLE chat_logs ADD COLUMN channel TEXT DEFAULT 'telegram'"))
                except Exception:
                    pass
        _chat_logs_channel_checked = True
    except Exception:
        pass


def log_chat_message(chat_id, role, message, channel="telegram"):
    chat_id = canonical_chat_id(chat_id)
    """Saves a conversation turn permanently in PostgreSQL, preventing immediate duplicates and tagged by channel."""
    if not message or not str(message).strip():
        return
    _ensure_chat_logs_channel_column()
    try:
        from connectors.trace import redact_secrets
        clean_msg = redact_secrets(str(message).strip())
    except Exception:
        clean_msg = str(message).strip()
    chan = (channel or "telegram").strip().lower()
    # Never poison chat history with emergency fallback error messages
    if 'Bataiye, main aapki kya madad karun' in clean_msg:
        return
    for k in list(_CHAT_HISTORY_CACHE.keys()):
        if k[0] == chat_id:
            _CHAT_HISTORY_CACHE.pop(k, None)
    try:
        with engine.begin() as conn:
            # Check last logged message for this user & channel to avoid consecutive duplicates
            last = conn.execute(
                text(
                    "SELECT role, message FROM chat_logs "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    "  AND (channel = :chan OR (channel IS NULL AND :chan = 'telegram')) "
                    "ORDER BY id DESC LIMIT 1"
                ),
                {"cid": chat_id, "chan": chan}
            ).first()
            if last and last[0] == role and last[1] == clean_msg:
                return  # Skip duplicate insert

            conn.execute(
                text(
                    "INSERT INTO chat_logs (user_id, role, message, channel) "
                    "SELECT id, :role, :msg, :chan FROM users WHERE telegram_chat_id = :cid"
                ),
                {"cid": chat_id, "role": role, "msg": clean_msg, "chan": chan}
            )
    except Exception as exc:
        print("log_chat_message error:", repr(exc))


def get_recent_chat_history(chat_id, limit=15, channel=None):
    chat_id = canonical_chat_id(chat_id)
    """Retrieves the recent conversation history in chronological order without sequential duplicates, scoped to channel."""
    _ensure_chat_logs_channel_column()
    import time as _pytime
    now_ts = _pytime.time()
    chan = (channel or "").strip().lower() if channel else None
    ck = (chat_id, limit, chan)
    cached = _CHAT_HISTORY_CACHE.get(ck)
    if cached and (now_ts < cached["exp"]):
        return cached["data"]
    try:
        with engine.connect() as conn:
            if chan in ("app", "web"):
                # Strictly isolate app/web conversations so private Telegram/WhatsApp chats never appear on the shared app screen
                rows = conn.execute(
                    text(
                        "SELECT role, message, created_at FROM chat_logs "
                        "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                        "  AND channel IN ('app', 'web') "
                        "ORDER BY id DESC LIMIT :lim"
                    ),
                    {"cid": chat_id, "lim": limit * 2}
                ).fetchall()
            elif chan == "telegram":
                rows = conn.execute(
                    text(
                        "SELECT role, message, created_at FROM chat_logs "
                        "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                        "  AND (channel = 'telegram' OR channel IS NULL) "
                        "ORDER BY id DESC LIMIT :lim"
                    ),
                    {"cid": chat_id, "lim": limit * 2}
                ).fetchall()
            elif chan == "whatsapp":
                rows = conn.execute(
                    text(
                        "SELECT role, message, created_at FROM chat_logs "
                        "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                        "  AND channel = 'whatsapp' "
                        "ORDER BY id DESC LIMIT :lim"
                    ),
                    {"cid": chat_id, "lim": limit * 2}
                ).fetchall()
            else:
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
            role, msg = r[0], (r[1] or '').strip()
            if not msg or 'Bataiye, main aapki kya madad karun' in msg:
                continue
            if cleaned and cleaned[-1]["role"] == role and cleaned[-1]["text"] == msg:
                continue
            cleaned.append({"role": role, "text": msg})
        res = cleaned[-limit:]
        _CHAT_HISTORY_CACHE[ck] = {"exp": now_ts + 45.0, "data": res}
        return res
    except Exception as exc:
        print("get_recent_chat_history error:", repr(exc))
        return []

get_chat_history_turns = get_recent_chat_history


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


def search_relevant_user_facts(chat_id, query_text: str, limit: int = 4) -> list:
    """
    Phase 3: Cognitive Memory RAG.
    Delegates directly to memories table via hybrid vector/FTS/trigram search (RRF, k=60),
    eliminating fragile stopword/keyword filtering.
    """
    chat_id = canonical_chat_id(chat_id)
    if not query_text or not str(query_text).strip():
        return []

    try:
        from memory.store import resolve_user_id, search_memories_hybrid
        uid = resolve_user_id(chat_id)
        mems = search_memories_hybrid(uid, query_text, limit=limit)
        results = [m["content"] for m in mems if m.get("content")]
        if results:
            return results[:limit]
    except Exception as exc:
        logger.warning(f"search_relevant_user_facts hybrid search error: {exc}")

    matched_facts = []
    seen = set()
    try:
        with engine.connect() as conn:
            rows = conn.execute(
                text("""
                    SELECT content FROM memories
                    WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid)
                      AND status = 'active' AND valid_to IS NULL
                    ORDER BY importance DESC, created_at DESC LIMIT :lim
                """),
                {"cid": chat_id, "lim": limit}
            ).fetchall()
            for r in rows:
                if r[0] and r[0].lower() not in seen:
                    seen.add(r[0].lower())
                    matched_facts.append(r[0])
    except Exception:
        pass
    return matched_facts[:limit]


# ---------- User Third-Party Integrations (Notion, GitHub, etc.) ----------

def save_user_integration(chat_id, service: str, api_key: str, config_data: str = "{}"):
    _init_frontier_sqlite_if_needed()
    chat_id = canonical_chat_id(chat_id)
    """Encrypts and securely stores an API key/token for a third-party service."""
    if not api_key or not str(api_key).strip():
        return False
    from crypto import encrypt_aes256_gcm
    enc = encrypt_aes256_gcm(str(api_key).strip())
    svc = str(service).strip().lower()
    is_pg = engine.dialect.name == "postgresql"
    now_expr = "now()" if is_pg else "CURRENT_TIMESTAMP"
    true_expr = "true" if is_pg else "1"
    try:
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"INSERT INTO user_integrations (user_id, service, api_key_enc, config_data, is_active, updated_at) "
                    f"SELECT id, :svc, :enc, :cfg, {true_expr}, {now_expr} FROM users WHERE telegram_chat_id = :cid "
                    f"ON CONFLICT (user_id, service) DO UPDATE SET "
                    f"api_key_enc = EXCLUDED.api_key_enc, "
                    f"config_data = EXCLUDED.config_data, "
                    f"is_active = {true_expr}, "
                    f"updated_at = {now_expr}"
                ),
                {"cid": chat_id, "svc": svc, "enc": enc, "cfg": config_data or "{}"}
            )
        return True
    except Exception as exc:
        print("save_user_integration error:", repr(exc))
        return False


def get_user_integration(chat_id, service: str):
    _init_frontier_sqlite_if_needed()
    chat_id = canonical_chat_id(chat_id)
    """Retrieves and decrypts the API key/token for a user integration."""
    from crypto import decrypt_any
    svc = str(service).strip().lower()
    is_pg = engine.dialect.name == "postgresql"
    active_cond = "is_active = true" if is_pg else "is_active = 1"
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text(
                    "SELECT api_key_enc, config_data, is_active FROM user_integrations "
                    "WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) "
                    f"AND service = :svc AND {active_cond}"
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
    _init_frontier_sqlite_if_needed()
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
    """Check if a specific email ID has already been inspected for this user or any linked identities."""
    if not chat_id or not email_id:
        return True
    _init_processed_user_emails_table()
    try:
        linked_ids = list(get_all_linked_chat_ids(chat_id))
        is_pg = engine.dialect.name == "postgresql"
        with engine.connect() as conn:
            if is_pg:
                row = conn.execute(
                    text("SELECT id FROM processed_user_emails WHERE chat_id = ANY(:cids) AND email_id = :e"),
                    {"cids": linked_ids, "e": email_id}
                ).first()
            else:
                cids_str = ",".join(str(int(i)) for i in linked_ids)
                row = conn.execute(
                    text(f"SELECT id FROM processed_user_emails WHERE chat_id IN ({cids_str}) AND email_id = :e"),
                    {"e": email_id}
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
    """Mark an incoming email as processed across all linked accounts to prevent duplicate alerts."""
    if not chat_id or not email_id:
        return
    _init_processed_user_emails_table()
    try:
        linked_ids = list(get_all_linked_chat_ids(chat_id))
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            for cid in linked_ids:
                if is_pg:
                    conn.execute(
                        text("""
                            INSERT INTO processed_user_emails (chat_id, email_id, sender, subject, category, is_important, processed_at)
                            VALUES (:c, :e, :s, :sub, :cat, :imp, now())
                            ON CONFLICT (chat_id, email_id) DO NOTHING
                        """),
                        {
                            "c": cid,
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
                            "c": cid,
                            "e": email_id,
                            "s": (sender or "")[:255],
                            "sub": (subject or "")[:500],
                            "cat": (category or "")[:50],
                            "imp": 1 if is_important else 0,
                        }
                    )
    except Exception as exc:
        print("record_processed_user_email error:", repr(exc))


def has_user_email_baseline(chat_id: int) -> bool:
    """Check if a user or any linked identities already has at least one recorded email in processed_user_emails."""
    if not chat_id:
        return False
    _init_processed_user_emails_table()
    try:
        linked_ids = list(get_all_linked_chat_ids(chat_id))
        is_pg = engine.dialect.name == "postgresql"
        with engine.connect() as conn:
            if is_pg:
                row = conn.execute(
                    text("SELECT 1 FROM processed_user_emails WHERE chat_id = ANY(:cids) LIMIT 1"),
                    {"cids": linked_ids}
                ).first()
            else:
                cids_str = ",".join(str(int(i)) for i in linked_ids)
                row = conn.execute(
                    text(f"SELECT 1 FROM processed_user_emails WHERE chat_id IN ({cids_str}) LIMIT 1")
                ).first()
            return row is not None
    except Exception as exc:
        print("has_user_email_baseline error:", repr(exc))
        return False


def get_all_connected_google_users() -> list[dict]:
    """Return all users who have an active Google connection linked to a Telegram chat."""
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT u.id AS user_id, u.telegram_chat_id, gt.email, gt.refresh_token_enc, gt.scopes,
                       gt.created_at, gt.updated_at
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
    chat_id = canonical_chat_id(chat_id)
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
    chat_id = canonical_chat_id(chat_id)
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
# SEMANTIC USER LOCATION STATE MACHINE (Zero-Regex, Dynamic Global Context)
# ==============================================================================

def _init_user_location_table():
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS user_location_state (
                        id SERIAL PRIMARY KEY,
                        chat_id BIGINT UNIQUE NOT NULL,
                        current_city VARCHAR(100) NOT NULL,
                        region VARCHAR(100),
                        country VARCHAR(100) DEFAULT 'India',
                        travel_status VARCHAR(50) DEFAULT 'home',
                        confidence NUMERIC(3, 2) DEFAULT 1.0,
                        source VARCHAR(50) DEFAULT 'conversational_context',
                        updated_at TIMESTAMPTZ DEFAULT now()
                    );
                    CREATE INDEX IF NOT EXISTS idx_user_location_chat ON user_location_state(chat_id);
                """))
            else:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS user_location_state (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        chat_id INTEGER UNIQUE NOT NULL,
                        current_city TEXT NOT NULL,
                        region TEXT,
                        country TEXT DEFAULT 'India',
                        travel_status TEXT DEFAULT 'home',
                        confidence REAL DEFAULT 1.0,
                        source TEXT DEFAULT 'conversational_context',
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """))
    except Exception as exc:
        print("init_user_location_table error:", repr(exc))


def get_user_location_state(chat_id: int) -> Optional[Dict[str, Any]]:
    """Returns the user's semantic location state {current_city, region, country, travel_status} without regex."""
    if not chat_id:
        return None
    chat_id = canonical_chat_id(chat_id)
    _init_user_location_table()
    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT current_city, region, country, travel_status, confidence, source FROM user_location_state WHERE chat_id = :c"),
                {"c": chat_id}
            ).first()
            if row:
                return {
                    "current_city": row[0],
                    "region": row[1] or "",
                    "country": row[2] or "India",
                    "travel_status": row[3] or "home",
                    "confidence": float(row[4] or 1.0),
                    "source": row[5] or "conversational_context"
                }
            # Fallback auto-seed: check user structured memories for location
            try:
                with engine.connect() as conn:
                    loc_row = conn.execute(
                        text("""
                            SELECT structured FROM memories 
                            WHERE user_id = (SELECT id FROM users WHERE telegram_chat_id = :cid) 
                              AND source_type IN ('location', 'city', 'profile')
                              AND status = 'active'
                            ORDER BY id DESC LIMIT 1
                        """),
                        {"cid": chat_id}
                    ).mappings().first()
                    if loc_row and loc_row.get("structured") and isinstance(loc_row["structured"], dict) and loc_row["structured"].get("city"):
                        city = loc_row["structured"]["city"].title()
                        reg = loc_row["structured"].get("region", "")
                        cntry = loc_row["structured"].get("country", "")
                        set_user_location_state(chat_id, city, reg, cntry, source="persisted_fact")
                        return {
                            "current_city": city,
                            "region": reg,
                            "country": cntry,
                            "travel_status": "home",
                            "confidence": 1.0,
                            "source": "persisted_fact"
                        }
            except Exception:
                pass
            return None
    except Exception as exc:
        print("get_user_location_state error:", repr(exc))
        return None


def set_user_location_state(
    chat_id: int,
    current_city: str,
    region: Optional[str] = None,
    country: str = "India",
    travel_status: str = "home",
    confidence: float = 1.0,
    source: str = "conversational_context"
) -> bool:
    """Updates the user's semantic location state across all linked devices."""
    if not chat_id or not current_city:
        return False
    chat_id = canonical_chat_id(chat_id)
    _init_user_location_table()
    try:
        clean_city = str(current_city).strip().title()
        # Clean out common noise tokens
        for prefix in ("Is ", "In ", "At ", "Near "):
            if clean_city.startswith(prefix):
                clean_city = clean_city[len(prefix):].strip()
        if clean_city.lower() in ("unknown", "null", "none", "india", "is", "the", "a", "from", "to"):
            return False
        clean_region = str(region).strip().title() if region else None
        clean_country = str(country).strip().title() if country else "India"

        linked_ids = get_all_linked_chat_ids(chat_id)
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            for cid in linked_ids:
                if is_pg:
                    conn.execute(
                        text("""
                            INSERT INTO user_location_state (chat_id, current_city, region, country, travel_status, confidence, source, updated_at)
                            VALUES (:c, :city, :region, :country, :status, :conf, :src, now())
                            ON CONFLICT (chat_id)
                            DO UPDATE SET
                                current_city = EXCLUDED.current_city,
                                region = COALESCE(EXCLUDED.region, user_location_state.region),
                                country = EXCLUDED.country,
                                travel_status = EXCLUDED.travel_status,
                                confidence = EXCLUDED.confidence,
                                source = EXCLUDED.source,
                                updated_at = now()
                        """),
                        {
                            "c": cid,
                            "city": clean_city,
                            "region": clean_region,
                            "country": clean_country,
                            "status": travel_status,
                            "conf": confidence,
                            "src": source
                        }
                    )
                else:
                    conn.execute(
                        text("""
                            INSERT INTO user_location_state (chat_id, current_city, region, country, travel_status, confidence, source, updated_at)
                            VALUES (:c, :city, :region, :country, :status, :conf, :src, CURRENT_TIMESTAMP)
                            ON CONFLICT (chat_id)
                            DO UPDATE SET
                                current_city = excluded.current_city,
                                region = COALESCE(excluded.region, user_location_state.region),
                                country = excluded.country,
                                travel_status = excluded.travel_status,
                                confidence = excluded.confidence,
                                source = excluded.source,
                                updated_at = CURRENT_TIMESTAMP
                        """),
                        {
                            "c": cid,
                            "city": clean_city,
                            "region": clean_region,
                            "country": clean_country,
                            "status": travel_status,
                            "conf": confidence,
                            "src": source
                        }
                    )
        return True
    except Exception as exc:
        print("set_user_location_state error:", repr(exc))
        return False


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


# ===========================================================================
# Frontier Memorae Engine: Structured Memory Bubbles & pgvector Cosine Search
# ===========================================================================

_FRONTIER_SQLITE_INIT = False

def _init_frontier_sqlite_if_needed():
    global _FRONTIER_SQLITE_INIT
    if _FRONTIER_SQLITE_INIT:
        return
    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS memory_bubbles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    slug TEXT NOT NULL,
                    name TEXT NOT NULL,
                    color_hex TEXT DEFAULT '#6366F1',
                    icon_name TEXT DEFAULT 'Folder',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, slug)
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS memories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    memory_type TEXT DEFAULT 'episodic',
                    title TEXT,
                    content TEXT NOT NULL,
                    url TEXT,
                    image_url TEXT,
                    ocr_extracted_text TEXT,
                    ai_summary TEXT,
                    bubble_slug TEXT DEFAULT 'everything',
                    tags TEXT DEFAULT '[]',
                    status TEXT DEFAULT 'active',
                    embedding TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            for col, col_type in [
                ("title", "TEXT"),
                ("url", "TEXT"),
                ("image_url", "TEXT"),
                ("ocr_extracted_text", "TEXT"),
                ("ai_summary", "TEXT"),
                ("bubble_slug", "TEXT DEFAULT 'everything'"),
                ("tags", "TEXT DEFAULT '[]'"),
            ]:
                try:
                    conn.execute(text(f"ALTER TABLE memories ADD COLUMN {col} {col_type}"))
                except Exception:
                    pass

            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_integrations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    service TEXT NOT NULL,
                    api_key_enc TEXT,
                    config_data TEXT DEFAULT '{}',
                    is_active INTEGER DEFAULT 1,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, service)
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS email_links (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    email TEXT NOT NULL,
                    verified INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS user_mcp_servers (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    server_name TEXT NOT NULL,
                    server_url TEXT NOT NULL,
                    auth_token TEXT,
                    is_active INTEGER DEFAULT 1,
                    tools_cache TEXT DEFAULT '[]',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(user_id, server_name)
                )
            """))
    _FRONTIER_SQLITE_INIT = True


def get_internal_user_id(chat_id) -> int:
    """Resolves chat_id to users.id primary key. Never returns None."""
    chat_id = canonical_chat_id(chat_id)
    try:
        ensure_user(chat_id)
    except Exception:
        pass

    try:
        with engine.connect() as conn:
            row = conn.execute(
                text("SELECT id FROM users WHERE telegram_chat_id = :c"),
                {"c": chat_id}
            ).fetchone()
            if row and row[0]:
                return row[0]
            row2 = conn.execute(
                text("SELECT id FROM users WHERE id = :uid"),
                {"uid": chat_id}
            ).fetchone()
            if row2 and row2[0]:
                return row2[0]
    except Exception:
        pass
    return chat_id


def get_memory_bubbles(chat_id) -> list[dict]:
    """Retrieves all memory bubbles (folders) for the user."""
    _init_frontier_sqlite_if_needed()
    uid = get_internal_user_id(chat_id)
    is_postgres = "postgresql" in engine.url.drivername
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT slug, name, color_hex, icon_name, created_at
                FROM memory_bubbles
                WHERE user_id = :uid
                ORDER BY id ASC
            """), {"uid": uid}).fetchall()
            
            if not rows:
                # Seed default bubbles on the fly
                defaults = [
                    ('everything', 'Everything', '#6366F1', 'Inbox'),
                    ('work', 'Work & Projects', '#3B82F6', 'Briefcase'),
                    ('travel', 'Travel & Transit', '#EC4899', 'Plane'),
                    ('health', 'Health & Fitness', '#10B981', 'Heart'),
                    ('finance', 'Finance & Bills', '#F59E0B', 'CreditCard')
                ]
                with engine.begin() as wconn:
                    for s, n, c, i in defaults:
                        if is_postgres:
                            wconn.execute(text("""
                                INSERT INTO memory_bubbles (user_id, slug, name, color_hex, icon_name)
                                VALUES (:u, :s, :n, :c, :i)
                                ON CONFLICT (user_id, slug) DO NOTHING
                            """), {"u": uid, "s": s, "n": n, "c": c, "i": i})
                        else:
                            wconn.execute(text("""
                                INSERT OR IGNORE INTO memory_bubbles (user_id, slug, name, color_hex, icon_name)
                                VALUES (:u, :s, :n, :c, :i)
                            """), {"u": uid, "s": s, "n": n, "c": c, "i": i})
                return [{"slug": s, "name": n, "color_hex": c, "icon_name": i} for s, n, c, i in defaults]

            return [
                {
                    "slug": r[0],
                    "name": r[1],
                    "color_hex": r[2],
                    "icon_name": r[3],
                    "created_at": str(r[4])
                }
                for r in rows
            ]
    except Exception as exc:
        logger.error(f"get_memory_bubbles error: {exc}")
        return []


def create_memory_bubble(chat_id, slug: str, name: str, color_hex: str = "#6366F1", icon_name: str = "Folder") -> bool:
    """Creates a new structured memory bubble (folder)."""
    _init_frontier_sqlite_if_needed()
    uid = get_internal_user_id(chat_id)
    slug_clean = slug.strip().lower()
    is_postgres = "postgresql" in engine.url.drivername
    try:
        with engine.begin() as conn:
            if is_postgres:
                conn.execute(text("""
                    INSERT INTO memory_bubbles (user_id, slug, name, color_hex, icon_name)
                    VALUES (:u, :s, :n, :c, :i)
                    ON CONFLICT (user_id, slug) DO UPDATE
                    SET name = :n, color_hex = :c, icon_name = :i
                """), {"u": uid, "s": slug_clean, "n": name.strip(), "c": color_hex, "i": icon_name})
            else:
                conn.execute(text("""
                    INSERT INTO memory_bubbles (user_id, slug, name, color_hex, icon_name)
                    VALUES (:u, :s, :n, :c, :i)
                    ON CONFLICT (user_id, slug) DO UPDATE
                    SET name = :n, color_hex = :c, icon_name = :i
                """), {"u": uid, "s": slug_clean, "n": name.strip(), "c": color_hex, "i": icon_name})
        return True
    except Exception as exc:
        logger.error(f"create_memory_bubble error: {exc}")
        return False


def save_bubble_memory(
    chat_id,
    content: str,
    title: Optional[str] = None,
    bubble_slug: str = "everything",
    image_url: Optional[str] = None,
    url: Optional[str] = None,
    ocr_extracted_text: Optional[str] = None,
    ai_summary: Optional[str] = None,
    tags: Optional[list] = None,
    memory_type: str = "episodic",
    embedding: Optional[list] = None
) -> Optional[int]:
    """
    Saves a memory item into pgvector with bubble categorization and automatic embedding.
    """
    _init_frontier_sqlite_if_needed()
    uid = get_internal_user_id(chat_id)

    clean_content = (content or "").strip()
    if not clean_content and not ocr_extracted_text:
        return None

    # Text to vectorize includes title, content, and OCR text
    text_to_embed = " ".join(filter(None, [title, clean_content, ocr_extracted_text]))
    if not embedding and text_to_embed:
        try:
            from services.embeddings import embed_text as gen_emb
            embedding = gen_emb(text_to_embed)
        except Exception as exc:
            logger.warning(f"Embedding generation fallback in save_bubble_memory: {exc}")
            embedding = None

    tags_json = json.dumps(tags or [])
    is_postgres = "postgresql" in engine.url.drivername

    try:
        with engine.begin() as conn:
            if is_postgres and embedding:
                stmt = text("""
                    INSERT INTO memories (
                        user_id, memory_type, content, title, url, image_url,
                        ocr_extracted_text, ai_summary, bubble_slug, tags, embedding, status
                    ) VALUES (
                        :uid, :mtype, :content, :title, :url, :image_url,
                        :ocr, :summary, :bubble, CAST(:tags AS jsonb), CAST(:emb AS vector), 'active'
                    ) RETURNING id
                """)
                res = conn.execute(stmt, {
                    "uid": uid,
                    "mtype": memory_type,
                    "content": clean_content,
                    "title": title or (clean_content[:60] if clean_content else "Asset Note"),
                    "url": url,
                    "image_url": image_url,
                    "ocr": ocr_extracted_text,
                    "summary": ai_summary,
                    "bubble": bubble_slug.lower(),
                    "tags": tags_json,
                    "emb": str(embedding)
                }).fetchone()
                return res[0] if res else None
            else:
                stmt = text("""
                    INSERT INTO memories (
                        user_id, memory_type, content, title, url, image_url,
                        ocr_extracted_text, ai_summary, bubble_slug, tags, status
                    ) VALUES (
                        :uid, :mtype, :content, :title, :url, :image_url,
                        :ocr, :summary, :bubble, :tags, 'active'
                    )
                """)
                res = conn.execute(stmt, {
                    "uid": uid,
                    "mtype": memory_type,
                    "content": clean_content,
                    "title": title or (clean_content[:60] if clean_content else "Asset Note"),
                    "url": url,
                    "image_url": image_url,
                    "ocr": ocr_extracted_text,
                    "summary": ai_summary,
                    "bubble": bubble_slug.lower(),
                    "tags": tags_json
                })
                return res.lastrowid or 1
    except Exception as exc:
        logger.error(f"save_bubble_memory error: {exc}")
        return None


def search_bubble_memories_cosine(
    chat_id,
    query_text: str,
    bubble_slug: Optional[str] = None,
    limit: int = 5,
    min_similarity: float = 0.30
) -> list[dict]:
    """
    Executes true pgvector cosine similarity search: 1 - (embedding <=> :q_emb).
    Returns ranked memories with similarity scores.
    """
    _init_frontier_sqlite_if_needed()
    uid = get_internal_user_id(chat_id)

    clean_query = query_text.strip()
    if not clean_query:
        return []

    is_postgres = "postgresql" in engine.url.drivername
    if not is_postgres:
        # Fallback to smart text matching for SQLite
        all_mems = get_bubble_memories(chat_id, bubble_slug=bubble_slug, limit=limit * 2)
        q_low = clean_query.lower()
        matched = []
        for m in all_mems:
            combined = f"{m.get('title') or ''} {m.get('content') or ''} {m.get('ocr_extracted_text') or ''}".lower()
            q_words = [w for w in q_low.split() if len(w) > 2]
            matches = sum(1 for w in q_words if w in combined)
            sim = 0.5 + (0.5 * matches / max(len(q_words), 1)) if matches > 0 else 0.45
            m["similarity"] = round(sim, 2)
            matched.append(m)
        return sorted(matched, key=lambda x: x["similarity"], reverse=True)[:limit]

    try:
        from services.embeddings import embed_query
        q_emb = embed_query(clean_query)
        if not q_emb:
            return []

        bubble_filter = ""
        params = {
            "uid": uid,
            "q_emb": str(q_emb),
            "lim": limit,
            "min_sim": min_similarity
        }

        if bubble_slug and bubble_slug.lower() != "everything":
            bubble_filter = "AND m.bubble_slug = :bubble"
            params["bubble"] = bubble_slug.lower()

        sql = f"""
            SELECT 
                m.id,
                m.title,
                m.content,
                m.bubble_slug,
                m.image_url,
                m.ocr_extracted_text,
                m.ai_summary,
                m.created_at,
                (1.0 - (m.embedding <=> CAST(:q_emb AS vector))) AS similarity
            FROM memories m
            WHERE m.user_id = :uid
              AND m.status = 'active'
              AND m.embedding IS NOT NULL
              {bubble_filter}
            ORDER BY m.embedding <=> CAST(:q_emb AS vector) ASC
            LIMIT :lim
        """

        with engine.connect() as conn:
            rows = conn.execute(text(sql), params).fetchall()
            results = []
            for r in rows:
                sim = float(r[8]) if r[8] is not None else 0.0
                if sim >= min_similarity:
                    results.append({
                        "id": r[0],
                        "title": r[1],
                        "content": r[2],
                        "bubble_slug": r[3],
                        "image_url": r[4],
                        "ocr_extracted_text": r[5],
                        "ai_summary": r[6],
                        "created_at": str(r[7]),
                        "similarity": round(sim, 4)
                    })
            return results
    except Exception as exc:
        logger.error(f"search_bubble_memories_cosine error: {exc}")
        return []


def get_bubble_memories(chat_id, bubble_slug: Optional[str] = None, limit: int = 20) -> list[dict]:
    """Retrieves recent memories within a specific bubble or across all bubbles."""
    _init_frontier_sqlite_if_needed()
    uid = get_internal_user_id(chat_id)
    try:
        params = {"uid": uid, "lim": limit}
        where_clause = "WHERE user_id = :uid AND status = 'active'"
        if bubble_slug and bubble_slug.lower() != "everything":
            where_clause += " AND bubble_slug = :bubble"
            params["bubble"] = bubble_slug.lower()

        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT id, title, content, bubble_slug, image_url, ocr_extracted_text, ai_summary, created_at
                FROM memories
                {where_clause}
                ORDER BY id DESC
                LIMIT :lim
            """), params).fetchall()

            return [
                {
                    "id": r[0],
                    "title": r[1],
                    "content": r[2],
                    "bubble_slug": r[3],
                    "image_url": r[4],
                    "ocr_extracted_text": r[5],
                    "ai_summary": r[6],
                    "created_at": str(r[7])
                }
                for r in rows
            ]
    except Exception as exc:
        logger.error(f"get_bubble_memories error: {exc}")
        return []


# ===========================================================================
# Frontier Memorae Engine: Model Context Protocol (MCP) Server Registry
# ===========================================================================

def get_user_mcp_servers(chat_id) -> list[dict]:
    """Retrieves all registered active MCP servers for a user."""
    _init_frontier_sqlite_if_needed()
    uid = get_internal_user_id(chat_id)
    is_postgres = "postgresql" in engine.url.drivername
    active_cond = "is_active = true" if is_postgres else "is_active = 1"
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(f"""
                SELECT id, server_name, server_url, is_active, tools_cache, created_at
                FROM user_mcp_servers
                WHERE user_id = :uid AND {active_cond}
                ORDER BY id ASC
            """), {"uid": uid}).fetchall()

            servers = []
            for r in rows:
                tools = r[4]
                if isinstance(tools, str):
                    try:
                        tools = json.loads(tools)
                    except Exception:
                        tools = []
                servers.append({
                    "id": r[0],
                    "server_name": r[1],
                    "server_url": r[2],
                    "is_active": bool(r[3]),
                    "tools": tools or [],
                    "created_at": str(r[5])
                })
            return servers
    except Exception as exc:
        logger.error(f"get_user_mcp_servers error: {exc}")
        return []


def save_user_mcp_server(
    chat_id,
    server_name: str,
    server_url: str,
    auth_token: Optional[str] = None,
    tools_cache: Optional[list] = None
) -> bool:
    """Registers or updates an MCP server for the user."""
    _init_frontier_sqlite_if_needed()
    uid = get_internal_user_id(chat_id)
    is_postgres = "postgresql" in engine.url.drivername
    tools_json = json.dumps(tools_cache or [])
    try:
        with engine.begin() as conn:
            if is_postgres:
                conn.execute(text("""
                    INSERT INTO user_mcp_servers (user_id, server_name, server_url, auth_token, tools_cache, is_active)
                    VALUES (:uid, :sname, :surl, :token, CAST(:tcache AS jsonb), true)
                    ON CONFLICT (user_id, server_name) DO UPDATE
                    SET server_url = :surl, auth_token = :token, tools_cache = CAST(:tcache AS jsonb), is_active = true, updated_at = now()
                """), {
                    "uid": uid,
                    "sname": server_name.strip(),
                    "surl": server_url.strip(),
                    "token": auth_token,
                    "tcache": tools_json
                })
            else:
                conn.execute(text("""
                    INSERT INTO user_mcp_servers (user_id, server_name, server_url, auth_token, tools_cache, is_active)
                    VALUES (:uid, :sname, :surl, :token, :tcache, 1)
                    ON CONFLICT(user_id, server_name) DO UPDATE
                    SET server_url = :surl, auth_token = :token, tools_cache = :tcache, is_active = 1, updated_at = CURRENT_TIMESTAMP
                """), {
                    "uid": uid,
                    "sname": server_name.strip(),
                    "surl": server_url.strip(),
                    "token": auth_token,
                    "tcache": tools_json
                })
        return True
    except Exception as exc:
        logger.error(f"save_user_mcp_server error: {exc}")
        return False


def delete_user_mcp_server(chat_id, server_name: str) -> bool:
    """Removes or deactivates an MCP server."""
    _init_frontier_sqlite_if_needed()
    uid = get_internal_user_id(chat_id)
    try:
        with engine.begin() as conn:
            conn.execute(text("""
                DELETE FROM user_mcp_servers
                WHERE user_id = :uid AND lower(server_name) = lower(:sname)
            """), {"uid": uid, "sname": server_name.strip()})
        return True
    except Exception as exc:
        logger.error(f"delete_user_mcp_server error: {exc}")
        return False


# ---------- Final Hardening: Retention Purge & Calendar Event ID Scoping ----------

SOFT_DELETE_RETENTION_DAYS = int(os.getenv("SOFT_DELETE_RETENTION_DAYS", "30"))

def purge_expired_soft_deleted(days: Optional[int] = None) -> int:
    """
    Retention policy: physically purges soft-deleted memory items and reminders older than N days.
    Hard deletes (e.g. /forget and wipe) bypass soft-delete and purge immediately.
    """
    if days is None:
        days = SOFT_DELETE_RETENTION_DAYS
    from datetime import timedelta
    import core.timeutil as timeutil
    from timing import IST
    cutoff = timeutil.now(IST) - timedelta(days=days)
    is_pg = engine.dialect.name == "postgresql"
    total_purged = 0

    with engine.begin() as conn:
        if is_pg:
            # Delete associated reminder rows first
            conn.execute(
                text("""
                    DELETE FROM reminders 
                    WHERE item_id IN (
                        SELECT id FROM memory_items 
                        WHERE status = 'deleted' AND created_at < :cutoff
                    )
                """),
                {"cutoff": cutoff}
            )
            res = conn.execute(
                text("DELETE FROM memory_items WHERE status = 'deleted' AND created_at < :cutoff"),
                {"cutoff": cutoff}
            )
            total_purged = res.rowcount or 0
        else:
            conn.execute(
                text("""
                    DELETE FROM reminders 
                    WHERE item_id IN (
                        SELECT id FROM memory_items 
                        WHERE status = 'deleted' AND created_at < :cutoff
                    )
                """),
                {"cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S")}
            )
            res = conn.execute(
                text("DELETE FROM memory_items WHERE status = 'deleted' AND created_at < :cutoff"),
                {"cutoff": cutoff.strftime("%Y-%m-%d %H:%M:%S")}
            )
            total_purged = res.rowcount or 0

    return total_purged


_MEMORY_ITEMS_CAL_ID_INITIALIZED = False

def _init_memory_items_calendar_id_column():
    global _MEMORY_ITEMS_CAL_ID_INITIALIZED
    if _MEMORY_ITEMS_CAL_ID_INITIALIZED:
        return
    try:
        is_pg = engine.dialect.name == "postgresql"
        with engine.begin() as conn:
            if is_pg:
                conn.execute(text("ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS google_calendar_event_id VARCHAR(128);"))
            else:
                try:
                    conn.execute(text("ALTER TABLE memory_items ADD COLUMN google_calendar_event_id TEXT;"))
                except Exception:
                    pass
        _MEMORY_ITEMS_CAL_ID_INITIALIZED = True
    except Exception as exc:
        print("_init_memory_items_calendar_id_column error:", exc)


def update_memory_calendar_id(chat_id, item_id: int, cal_event_id: str):
    """Store synced Google Calendar event ID on memory item."""
    _init_memory_items_calendar_id_column()
    chat_id = canonical_chat_id(chat_id)
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE memory_items SET google_calendar_event_id = :cid WHERE id = :id AND user_id IN (SELECT id FROM users WHERE telegram_chat_id = :c)"),
            {"cid": cal_event_id, "id": int(item_id), "c": chat_id}
        )







