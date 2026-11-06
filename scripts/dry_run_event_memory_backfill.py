import os
import sys
import json
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.getcwd())
import db
from memory.store import resolve_user_id

def generate_dry_run_backfill():
    rows_to_insert = []
    
    with db.engine.connect() as conn:
        # Check existing event memories to enforce idempotency
        # Unique key concept: (user_id, source_type, source_id)
        existing_mem_rows = conn.execute(db.text("""
            SELECT user_id, source_type, structured 
            FROM memories 
            WHERE memory_type = 'event' AND status = 'active'
        """)).fetchall()
        
        existing_keys = set()
        for em in existing_mem_rows:
            uid, st, struct = em
            if isinstance(struct, str):
                try:
                    struct = json.loads(struct)
                except Exception:
                    struct = {}
            sid = struct.get("source_id") or struct.get("pending_action_id") or struct.get("trace_id")
            if sid is not None:
                existing_keys.add((uid, st, str(sid)))

        # 1. Backfill from confirmed pending_actions
        pending_rows = conn.execute(db.text("""
            SELECT id, chat_id, action_kind, payload, status, created_at 
            FROM pending_actions 
            WHERE status IN ('confirmed', 'executed', 'completed')
            ORDER BY id ASC
        """)).fetchall()

        for p in pending_rows:
            p_id, chat_id, action_kind, payload, status, created_at = p
            uid = resolve_user_id(chat_id)
            payload = payload or {}
            source_id = str(p_id)
            
            if action_kind == "send_email":
                source_type = "gmail"
                dedup_key = (uid, source_type, source_id)
                if dedup_key in existing_keys:
                    continue
                to_addr = payload.get("to", "")
                subject = payload.get("subject", "")
                content = f"Sent email to {to_addr} with subject '{subject}'"
                rows_to_insert.append({
                    "user_id": uid,
                    "chat_id": chat_id,
                    "memory_type": "event",
                    "content": content,
                    "source_type": source_type,
                    "structured": {
                        "action": "email_sent",
                        "recipient": to_addr,
                        "subject": subject,
                        "source_type": source_type,
                        "source_id": source_id,
                        "pending_action_id": p_id,
                        "confidence": 1.0
                    },
                    "importance": 3,  # Strict: 2-3, never >= 4 so it never enters get_core_profile
                    "valid_from": created_at.isoformat() if created_at else datetime.utcnow().isoformat(),
                    "created_at": created_at.isoformat() if created_at else datetime.utcnow().isoformat()
                })
                existing_keys.add(dedup_key)

            elif action_kind == "create_meeting":
                source_type = "google_calendar"
                dedup_key = (uid, source_type, source_id)
                if dedup_key in existing_keys:
                    continue
                title = payload.get("title", "Meeting")
                start_iso = payload.get("start_iso", "")
                content = f"Created calendar meeting '{title}' scheduled for {start_iso}"
                rows_to_insert.append({
                    "user_id": uid,
                    "chat_id": chat_id,
                    "memory_type": "event",
                    "content": content,
                    "source_type": source_type,
                    "structured": {
                        "action": "calendar_created",
                        "title": title,
                        "start_time": start_iso,
                        "source_type": source_type,
                        "source_id": source_id,
                        "pending_action_id": p_id,
                        "confidence": 1.0
                    },
                    "importance": 3,
                    "valid_from": created_at.isoformat() if created_at else datetime.utcnow().isoformat(),
                    "created_at": created_at.isoformat() if created_at else datetime.utcnow().isoformat()
                })
                existing_keys.add(dedup_key)

            elif action_kind == "delete_reminder":
                source_type = "reminder"
                dedup_key = (uid, source_type, source_id)
                if dedup_key in existing_keys:
                    continue
                title = payload.get("title", "")
                content = f"Deleted reminder '{title}'"
                rows_to_insert.append({
                    "user_id": uid,
                    "chat_id": chat_id,
                    "memory_type": "event",
                    "content": content,
                    "source_type": source_type,
                    "structured": {
                        "action": "reminder_deleted",
                        "title": title,
                        "source_type": source_type,
                        "source_id": source_id,
                        "pending_action_id": p_id,
                        "confidence": 1.0
                    },
                    "importance": 3,
                    "valid_from": created_at.isoformat() if created_at else datetime.utcnow().isoformat(),
                    "created_at": created_at.isoformat() if created_at else datetime.utcnow().isoformat()
                })
                existing_keys.add(dedup_key)

        # 2. Backfill from turn_traces tool_results
        traces = conn.execute(db.text("""
            SELECT id, chat_id, created_at, tools_called, tool_results
            FROM turn_traces
            WHERE tool_results IS NOT NULL
            ORDER BY id ASC
        """)).fetchall()

        for t in traces:
            trace_id, chat_id, created_at, tools_called, tool_results = t
            uid = resolve_user_id(chat_id)
            if not isinstance(tool_results, list):
                continue
            for res in tool_results:
                if not isinstance(res, dict):
                    continue
                tool_name = res.get("tool_name", "")
                status = res.get("status", "")
                source_id = str(trace_id)
                
                if tool_name == "create_reminder" and status == "success":
                    source_type = "reminder"
                    dedup_key = (uid, source_type, f"trace_{trace_id}_{res.get('title','')}")
                    if dedup_key in existing_keys:
                        continue
                    title = res.get("title", "")
                    due_date = res.get("due_date", "")
                    due_time = res.get("due_time", "")
                    rows_to_insert.append({
                        "user_id": uid,
                        "chat_id": chat_id,
                        "memory_type": "event",
                        "content": f"Created reminder '{title}' for {due_date} at {due_time}",
                        "source_type": source_type,
                        "structured": {
                            "action": "reminder_created",
                            "title": title,
                            "due_date": due_date,
                            "due_time": due_time,
                            "source_type": source_type,
                            "source_id": source_id,
                            "trace_id": trace_id,
                            "confidence": 1.0
                        },
                        "importance": 3,
                        "valid_from": created_at.isoformat() if created_at else datetime.utcnow().isoformat(),
                        "created_at": created_at.isoformat() if created_at else datetime.utcnow().isoformat()
                    })
                    existing_keys.add(dedup_key)

                elif tool_name == "delete_reminder" and status == "success":
                    source_type = "reminder"
                    dedup_key = (uid, source_type, f"trace_{trace_id}_{res.get('title','')}")
                    if dedup_key in existing_keys:
                        continue
                    title = res.get("title", "")
                    rows_to_insert.append({
                        "user_id": uid,
                        "chat_id": chat_id,
                        "memory_type": "event",
                        "content": f"Deleted reminder '{title}'",
                        "source_type": source_type,
                        "structured": {
                            "action": "reminder_deleted",
                            "title": title,
                            "source_type": source_type,
                            "source_id": source_id,
                            "trace_id": trace_id,
                            "confidence": 1.0
                        },
                        "importance": 3,
                        "valid_from": created_at.isoformat() if created_at else datetime.utcnow().isoformat(),
                        "created_at": created_at.isoformat() if created_at else datetime.utcnow().isoformat()
                    })
                    existing_keys.add(dedup_key)

    output_data = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "description": "DRY-RUN Backfill of connector actions into memories table (memory_type='event')",
        "pipeline_method": "memory.store.save_memory_item(user_id, content, memory_type='event', structured=..., importance=3, valid_from=...)",
        "total_rows_to_insert": len(rows_to_insert),
        "breakdown_by_source": {},
        "rows": rows_to_insert
    }

    for r in rows_to_insert:
        st = r["source_type"]
        output_data["breakdown_by_source"][st] = output_data["breakdown_by_source"].get(st, 0) + 1

    out_path = os.path.join("docs", "verification", "phase7b", "dry_run_event_memory_backfill.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(output_data, f, indent=2)

    print(f"Generated dry run backfill with {len(rows_to_insert)} rows.")
    print("Breakdown:", output_data["breakdown_by_source"])
    print(f"Saved to {out_path}")
    return rows_to_insert

if __name__ == "__main__":
    generate_dry_run_backfill()
