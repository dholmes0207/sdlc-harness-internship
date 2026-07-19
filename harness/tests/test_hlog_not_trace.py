"""test_hlog_not_trace.py — hlog stays on the DIAG side of the audit-trace line.

The audit trace (trace_log) never rotates and is hash-chained; the telemetry/diag
streams rotate. hlog is diag: it must NOT import trace_log (a diag write must never
touch the audit ledger) and must stay cheap — importing it must not pull a heavy
concurrency stack (asyncio/multiprocessing), the thing that ruled out loguru.
"""
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"


def test_hlog_does_not_import_trace_log():
    import ast
    tree = ast.parse((_HOOKS / "hlog.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert "trace_log" not in imported, "hlog must not import the audit trace"
    assert "hook_runtime" not in imported, "hlog stays stdlib-only, off the runtime"


def test_hlog_import_is_cheap():
    # in a clean subprocess, importing hlog must not drag in the heavy stacks
    code = (
        "import sys; sys.path.insert(0, %r); import hlog; "
        "bad=[m for m in ('asyncio','multiprocessing') if m in sys.modules]; "
        "print(','.join(bad))" % str(_HOOKS)
    )
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "", "hlog pulled a heavy module: %s" % r.stdout.strip()
