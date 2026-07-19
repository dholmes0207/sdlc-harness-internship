"""test_skill_quality_gate.py — PostToolUse fail-closed gate on SKILL.md content.

The hook FILE is human-placed: write_guard guards harness/hooks/*.py, so an agent
cannot write it inside a session (an agent must not wire its own compliance hook).
These subprocess tests therefore SKIP cleanly until a human drops
the file in, then validate the real stdin → exit contract. The gate's brain
(check_skill_structure.write_gate_reason) is covered unconditionally in
test_check_skill_structure.py, so the logic is tested whether or not the hook is yet
installed.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOK = Path(__file__).resolve().parent.parent / "hooks" / "skill_quality_gate.py"

requires_hook = pytest.mark.skipif(
    not _HOOK.is_file(),
    reason="skill_quality_gate.py is human-placed (write_guard) — not installed yet")


def _skill(tmp_path, body, name="demo"):
    d = tmp_path / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(
        "---\nname: hs:%s\ndescription: A demo skill. Use when you need a demo here.\n"
        "---\n# Title\n%s\n" % (name, body),
        encoding="utf-8")
    return d / "SKILL.md"


def _run_hook(payload):
    return subprocess.run(
        [sys.executable, str(_HOOK)], input=json.dumps(payload),
        capture_output=True, text=True)


@requires_hook
def test_hook_blocks_on_dangling_ref(tmp_path):
    sk = _skill(tmp_path, "See references/nope.md for detail.")
    r = _run_hook({"tool_name": "Write", "tool_input": {"file_path": str(sk)}})
    assert r.returncode == 2
    assert str(sk) in (r.stderr + r.stdout)  # actionable: names the path


@requires_hook
def test_hook_failopen_on_shape_only(tmp_path):
    # A clean body with only a (advisory) shape concern => the gate allows it.
    sk = _skill(tmp_path, "clean prose body with no leaks")
    r = _run_hook({"tool_name": "Write", "tool_input": {"file_path": str(sk)}})
    assert r.returncode == 0


@requires_hook
def test_hook_inert_on_non_skill_md(tmp_path):
    other = tmp_path / "notes.md"
    other.write_text("references/nope.md\n", encoding="utf-8")
    r = _run_hook({"tool_name": "Write", "tool_input": {"file_path": str(other)}})
    assert r.returncode == 0
