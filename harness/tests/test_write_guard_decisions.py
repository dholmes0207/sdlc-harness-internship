"""test_write_guard_decisions.py — the decisions SSOT on the guard list (R1/DC-6).

The cross-scope confirm gate is only real if the register CLI is the SINGLE write
path into docs/decisions.yaml. Otherwise an agent edits the file directly with
Write/Edit and the gate is theater. So docs/decisions.yaml + docs/decisions.md
join GUARD_LIST: agent tool-edits are blocked (exit 2); the register CLI writes
via atomic_write (Bash/script, not a guarded tool) and still lands. Other docs
must NOT be over-blocked.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_GUARD = _HOOKS / "write_guard.py"


def _mk_root(tmp_path):
    root = tmp_path / "proj"
    for rel in ("harness/hooks", "docs"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def _run(root, tool, target):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_HOOK_CONFIG", None)
    env["HARNESS_ROOT"] = str(root)
    env["HARNESS_STATE_DIR"] = str(root / "harness" / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(root / "logs")
    env["HARNESS_USER"] = "guard-tester"
    payload = {"tool_name": tool, "tool_input": {"file_path": str(target)},
               "session_id": "wg-dec"}
    return subprocess.run([sys.executable, str(_GUARD)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env)


@pytest.mark.parametrize("rel", ["docs/decisions.yaml", "docs/decisions.md"])
def test_blocks_decisions_ssot(tmp_path, rel):
    root = _mk_root(tmp_path)
    r = _run(root, "Edit", root / rel)
    assert r.returncode == 2
    assert "may not edit it" in r.stderr or "BLOCKED" in r.stderr


def test_does_not_overblock_other_docs(tmp_path):
    root = _mk_root(tmp_path)
    r = _run(root, "Edit", root / "docs" / "system-architecture.md")
    assert r.returncode == 0


def test_blocks_write_tool_too(tmp_path):
    root = _mk_root(tmp_path)
    r = _run(root, "Write", root / "docs" / "decisions.yaml")
    assert r.returncode == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
