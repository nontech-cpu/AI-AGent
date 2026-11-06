import subprocess
import sys
import os
import pytest

def test_static_checks_no_unbound_or_undefined_variables():
    """
    Phase 7c Static Analysis Gate:
    Enforces that zero F821 (Undefined name), F822 (Undefined export),
    F823 (UnboundLocalError / referenced before assignment), F811 (Redefinition),
    or F601 (Duplicate key) errors exist in the codebase.
    """
    python_exe = sys.executable
    cmd = [
        python_exe, "-m", "ruff", "check",
        "--select", "F821,F822,F823,F811,F601",
        "agent", "memory", "connectors", "state", "main.py", "db.py", "google_services.py", "morning_briefing.py"
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd())
    
    assert res.returncode == 0, f"Static check failed with errors:\n{res.stdout}\n{res.stderr}"

