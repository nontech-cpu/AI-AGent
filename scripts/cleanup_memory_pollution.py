"""
Phase 0 Cleanup Script: Archives existing polluted, negative, reminder, and duplicate rows.
Safe and non-destructive: sets status = 'archived'. Never deletes data.
"""
import sys
import os
import argparse
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text
import db

NEGATIVE_PATTERNS = [
    "%has not%", "%not saved%", "%not shared%", "%never shared%",
    "%nahi bataya%", "%nahi mila%", "%no record%", "%unrecorded%",
    "%missing%", "%not recorded%", "%records show%"
]

REMINDER_PATTERNS = [
    "%remind user%", "%reminder set%", "%user wants reminder%",
    "%reminder at 10%", "%scheduled at 10%", "%reminder:%", "%has a reminder%"
]

def run_cleanup(execute: bool = False):
    print("=" * 70)
    print(f"PeculiarAI Memory Pollution Cleanup (Mode: {'EXECUTE' if execute else 'DRY RUN'})")
    print("=" * 70)

    rows_to_archive = []

    with db.engine.connect() as conn:
        # 1. Negative & absence statements
        for pat in NEGATIVE_PATTERNS:
            found = conn.execute(
                text("SELECT id, user_id, memory_type, content, status FROM memories WHERE status = 'active' AND content ILIKE :p"),
                {"p": pat}
            ).fetchall()
            for r in found:
                rows_to_archive.append((r[0], r[1], r[2], r[3], "negative_pollution"))

        # 2. Reminder clutter in memories table
        for pat in REMINDER_PATTERNS:
            found = conn.execute(
                text("SELECT id, user_id, memory_type, content, status FROM memories WHERE status = 'active' AND content ILIKE :p"),
                {"p": pat}
            ).fetchall()
            for r in found:
                rows_to_archive.append((r[0], r[1], r[2], r[3], "reminder_clutter"))

        # 3. Deduplicate exact duplicate contents (keep the newest row, archive older ones)
        dupes = conn.execute(text("""
            SELECT content, user_id, COUNT(*), ARRAY_AGG(id ORDER BY id DESC)
            FROM memories
            WHERE status = 'active'
            GROUP BY content, user_id
            HAVING COUNT(*) > 1;
        """)).fetchall()

        for d in dupes:
            content = d[0]
            uid = d[1]
            ids = d[3] # ids is list descending [newest, older, oldest]
            keep_id = ids[0]
            archive_ids = ids[1:]
            for aid in archive_ids:
                rows_to_archive.append((aid, uid, "duplicate", content, f"duplicate_of_{keep_id}"))

    # Dedupe row list by memory ID
    unique_to_archive = {}
    for r in rows_to_archive:
        unique_to_archive[r[0]] = r

    print(f"Total candidate rows to archive: {len(unique_to_archive)}")
    for mid, r in sorted(unique_to_archive.items()):
        print(f"  • ID {mid} [{r[4]}]: {r[3][:70]}...")

    if execute:
        if unique_to_archive:
            ids_list = list(unique_to_archive.keys())
            with db.engine.begin() as conn:
                conn.execute(
                    text("UPDATE memories SET status = 'archived', updated_at = NOW() WHERE id = ANY(:ids)"),
                    {"ids": ids_list}
                )
            print(f"\nSuccessfully archived {len(ids_list)} polluted/duplicate memory rows!")
        else:
            print("\nNo rows needed archiving.")
    else:
        print("\n[Dry Run Complete] Run with --execute to perform archiving.")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--execute", action="store_true", help="Perform actual archiving")
    args = parser.parse_args()
    run_cleanup(execute=args.execute)
