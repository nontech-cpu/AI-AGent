import os
import sys
import json
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, os.getcwd())
import db

def count_token_prefixes():
    prefixes = ["secret_", "ntn_", "ghp_", "github_pat_", "gsk_", "AIza"]
    results = {}
    
    with db.engine.connect() as conn:
        # 1. chat_logs table
        chat_counts = {}
        for p in prefixes:
            cnt = conn.execute(db.text("""
                SELECT COUNT(*) FROM chat_logs WHERE message LIKE :p
            """), {"p": f"%{p}%"}).scalar()
            chat_counts[p] = cnt
        results["chat_logs"] = chat_counts

        # 2. turn_traces table
        trace_counts = {}
        for p in prefixes:
            cnt = conn.execute(db.text("""
                SELECT COUNT(*) FROM turn_traces 
                WHERE (message LIKE :p 
                       OR CAST(tools_called AS text) LIKE :p 
                       OR CAST(tool_results AS text) LIKE :p)
            """), {"p": f"%{p}%"}).scalar()
            trace_counts[p] = cnt
        results["turn_traces"] = trace_counts

        # 3. memories table
        mem_counts = {}
        for p in prefixes:
            cnt = conn.execute(db.text("""
                SELECT COUNT(*) FROM memories 
                WHERE (content LIKE :p OR CAST(structured AS text) LIKE :p)
            """), {"p": f"%{p}%"}).scalar()
            mem_counts[p] = cnt
        results["memories"] = mem_counts

        # 4. pending_actions table
        pa_counts = {}
        for p in prefixes:
            cnt = conn.execute(db.text("""
                SELECT COUNT(*) FROM pending_actions 
                WHERE CAST(payload AS text) LIKE :p
            """), {"p": f"%{p}%"}).scalar()
            pa_counts[p] = cnt
        results["pending_actions"] = pa_counts

    print("Token Prefixes Read-Only Audit Results:")
    print(json.dumps(results, indent=2))
    
    out_path = os.path.join("docs", "verification", "phase7c", "token_audit_counts.json")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=2)
    print(f"Saved counts to {out_path}")

if __name__ == "__main__":
    count_token_prefixes()
