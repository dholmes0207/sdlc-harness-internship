"""test_telemetry_anchor_hook.py — the manual-test telemetry anchor, end to end.

manual_test_anchor is a PostToolUse(Bash) telemetry hook. During a manual-test
session (HARNESS_MANUAL_TEST_SESSION set) it records each Bash command as an
anchor {anchor_id, cmd_hash, output_hash?} so a manual-test artifact can cite a
trace the HOOK witnessed — not one the agent asserts. The gate later cross-checks
the cited id exists. Scoped: outside a session it records nothing (no sink flood).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_ANCHOR = _HOOKS / "manual_test_anchor.py"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import manual_test as mt  # noqa: E402


def _run(tmp_path, command, session=True, response="OK", session_id=None):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_TELEMETRY_DISABLED", None)
    env["HARNESS_ROOT"] = str(tmp_path)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_USER"] = "tester"
    if session:
        env["HARNESS_MANUAL_TEST_SESSION"] = "1"
    else:
        env.pop("HARNESS_MANUAL_TEST_SESSION", None)
    body = {"tool_name": "Bash",
            "tool_input": {"command": command},
            "tool_response": {"stdout": response}}
    if session_id is not None:
        body["session_id"] = session_id
    payload = json.dumps(body)
    return subprocess.run([sys.executable, str(_ANCHOR)], input=payload,
                          capture_output=True, text=True, env=env)


def _sink(tmp_path):
    # Union legacy sink + per-session shards (P2 shards session records).
    d = tmp_path / "state" / "telemetry"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("manual-test-anchor*.jsonl")):
        if p.name.endswith(".bak"):
            continue
        out.extend(json.loads(ln) for ln in p.read_text().splitlines() if ln.strip())
    return out


def test_records_anchor_during_session(tmp_path):
    cmd = "curl -s http://localhost:8080/health"
    proc = _run(tmp_path, cmd)
    assert proc.returncode == 0, proc.stderr
    recs = _sink(tmp_path)
    assert recs, "an anchor must be recorded during a manual-test session"
    assert recs[0]["anchor_id"] == mt.anchor_id_for(cmd)


def test_no_record_outside_session(tmp_path):
    _run(tmp_path, "curl -s http://localhost/x", session=False)
    assert _sink(tmp_path) == []


def test_cited_anchor_cross_checks_through_manual_test(tmp_path):
    cmd = "curl -s http://localhost:8080/login"
    _run(tmp_path, cmd)
    # the gate-side cross-check (manual_test.anchor_exists) must find it.
    assert mt.anchor_exists(mt.anchor_id_for(cmd), root=tmp_path / "state")


def test_hook_never_blocks(tmp_path):
    # telemetry class: even a malformed payload must continue (exit 0).
    proc = subprocess.run([sys.executable, str(_ANCHOR)], input="not json",
                          capture_output=True, text=True,
                          env={**os.environ, "HARNESS_ROOT": str(tmp_path),
                               "HARNESS_STATE_DIR": str(tmp_path / "state"),
                               "HARNESS_MANUAL_TEST_SESSION": "1"})
    assert proc.returncode == 0


# --- session marker gate: arm anchoring without the env var --------------------

def test_marker_arms_hook_without_env(tmp_path):
    # No HARNESS_MANUAL_TEST_SESSION, but a session marker is armed -> the hook
    # records (the subagent/forgot-env case the marker exists to cover).
    sid = "sess-A"
    mt.arm_session(tmp_path / "state", sid)
    cmd = "curl -s http://localhost:8080/health"
    proc = _run(tmp_path, cmd, session=False, session_id=sid)
    assert proc.returncode == 0, proc.stderr
    recs = _sink(tmp_path)
    assert recs and recs[0]["anchor_id"] == mt.anchor_id_for(cmd)


def test_no_env_no_marker_no_record(tmp_path):
    # A session id is present but no marker is armed and no env -> nothing.
    _run(tmp_path, "curl -s http://localhost/x", session=False, session_id="sess-A")
    assert _sink(tmp_path) == []


def test_marker_session_scoped_in_hook(tmp_path):
    # Marker armed for sess-A must NOT arm anchoring for sess-B.
    mt.arm_session(tmp_path / "state", "sess-A")
    _run(tmp_path, "curl -s http://localhost/x", session=False, session_id="sess-B")
    assert _sink(tmp_path) == []


def test_hook_record_carries_source_hook(tmp_path):
    _run(tmp_path, "curl -s http://localhost/health")
    recs = _sink(tmp_path)
    assert recs and recs[0].get("source") == "hook"
