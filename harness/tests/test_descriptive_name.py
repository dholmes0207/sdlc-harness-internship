"""test_descriptive_name.py — advise against generic file names (nudge class).

A PreToolUse:Write nudge that fires when a NEW file is about to be written with a
low-information name (report.md / utils.js / temp.py). Actionable where CK's
unconditional naming-reminder injection is not: by PreToolUse the path is already
chosen, so a blanket reminder cannot influence THIS write — a generic-name
detector can, by flagging the lazy name before it lands.

Posture: nudge (default OFF, opt-in), advisory + fail-open — never blocks, always
continues. Its value is the autonomous /goal path, where UserPromptSubmit context
injection (which carries the naming guidance) never fires, so a PreToolUse hook is
the only place the guidance still reaches the agent.

Tested via direct core() (pure function) + subprocess exit contract.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_HOOK = _HOOKS / "descriptive_name.py"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))


def _write(file_path: str) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": file_path},
            "session_id": "s1"}


# ---- core: generic names flagged, descriptive names pass --------------------

def test_generic_report_name_flagged():
    import descriptive_name
    msg = descriptive_name.core(_write("plans/reports/report.md"))
    assert msg and "report.md" in msg
    assert "generic" in msg.lower() or "descriptive" in msg.lower()


def test_descriptive_name_passes():
    import descriptive_name
    assert descriptive_name.core(_write("harness/scripts/stage_detector.py")) is None
    assert descriptive_name.core(_write("plans/reports/triage-260620-blind-review-report.md")) is None


def test_conventional_names_allowlisted():
    import descriptive_name
    for p in ("src/index.js", "pkg/main.go", "a/__init__.py", "README.md",
              "tests/conftest.py", "setup.py"):
        assert descriptive_name.core(_write(p)) is None, p


def test_only_write_tool_not_edit():
    import descriptive_name
    # Edit/MultiEdit touch an EXISTING file — the name is already chosen, do not nag
    for tool in ("Edit", "MultiEdit", "Bash", "Read"):
        d = {"tool_name": tool, "tool_input": {"file_path": "report.md"}}
        assert descriptive_name.core(d) is None, tool


def test_malformed_payload_is_inert():
    import descriptive_name
    assert descriptive_name.core({}) is None
    assert descriptive_name.core({"tool_name": "Write", "tool_input": {}}) is None
    assert descriptive_name.core({"tool_name": "Write"}) is None


# ---- subprocess: nudge exit contract (default OFF, fail-open) ---------------

def _cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "hooks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _run(config: Path, payload, raw=False):
    env = dict(os.environ, HARNESS_HOOK_CONFIG=str(config))
    stdin = payload if raw else json.dumps(payload)
    return subprocess.run([sys.executable, str(_HOOK)], input=stdin,
                          text=True, capture_output=True, env=env)


def test_subprocess_default_off_is_silent(tmp_path):
    # nudge default OFF: even a generic name produces NO advisory, just continue
    r = _run(_cfg(tmp_path, "hooks: {}\n"), _write("report.md"))
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("continue") is True
    assert "systemMessage" not in out


def test_subprocess_enabled_emits_advisory(tmp_path):
    # advisory rides the systemMessage sink (nudge-channels.yaml: descriptive_name
    # -> systemMessage), carried in the terminal stdout blob, not on stderr.
    cfg = _cfg(tmp_path, "hooks:\n  descriptive_name: {enabled: true}\n")
    r = _run(cfg, _write("report.md"))
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("continue") is True
    assert "report.md" in out.get("systemMessage", "")


def test_subprocess_enabled_descriptive_is_silent(tmp_path):
    cfg = _cfg(tmp_path, "hooks:\n  descriptive_name: {enabled: true}\n")
    r = _run(cfg, _write("harness/scripts/stage_detector.py"))
    assert r.returncode == 0
    assert "systemMessage" not in json.loads(r.stdout)


def test_subprocess_fail_open_on_garbage(tmp_path):
    cfg = _cfg(tmp_path, "hooks:\n  descriptive_name: {enabled: true}\n")
    r = _run(cfg, "not json", raw=True)
    assert r.returncode == 0
    assert json.loads(r.stdout).get("continue") is True
