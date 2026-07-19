"""test_guard_config.py — the posture CLI (show / set / set-preset).

`show` is read-only: it resolves and prints every registered guard's effective
mode so a human can audit posture at a glance. `set` / `set-preset` rewrite
guard-policy.yaml AND append a who/when `guard_config_changed` audit line; a
write that lowers a safety-floor guard is flagged break_glass. The CLI exit
code + the trace file are the real contract, so these run the script as a
subprocess against a tmp policy + tmp state dir.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "scripts" / "guard_config.py"
_SCRIPTS = _ROOT / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import guard_policy  # noqa: E402


def _seed(tmp_path, preset="balanced", overrides=None):
    import yaml

    doc = {"schema_version": "1.0", "preset": preset}
    if overrides is not None:
        doc["overrides"] = overrides
    p = tmp_path / "guard-policy.yaml"
    # keep a comment header so the preservation test has something to find
    p.write_text("# guard-policy.yaml — test seed\n" + yaml.safe_dump(doc),
                 encoding="utf-8")
    return p


def _env(tmp_path, policy):
    env = dict(os.environ)
    env["HARNESS_GUARD_POLICY"] = str(policy)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_USER"] = "tester@x.com"
    return env


def _run(args, env):
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        capture_output=True, text=True, env=env)


def _trace_events(tmp_path):
    out = []
    d = tmp_path / "state" / "trace"
    for f in sorted(d.glob("trace-*.jsonl")) if d.is_dir() else []:
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


# --------------------------------------------------------------------- show ---

def test_show_lists_every_guard_with_mode(tmp_path):
    policy = _seed(tmp_path, "balanced")
    r = _run(["show"], _env(tmp_path, policy))
    assert r.returncode == 0, r.stderr
    for gid in guard_policy.GUARD_REGISTRY:
        assert gid in r.stdout
    assert "balanced" in r.stdout


def test_show_reflects_preset(tmp_path):
    policy = _seed(tmp_path, "lenient")
    r = _run(["show"], _env(tmp_path, policy))
    assert r.returncode == 0, r.stderr
    # under lenient: enforcement -> warn, safety floor -> block. (The advisory
    # category currently has no registered member to render.)
    lines = {ln.split()[0]: ln for ln in r.stdout.splitlines() if ln.strip()
             and ln.split()[0] in guard_policy.GUARD_REGISTRY}
    assert "warn" in lines["standards_strict_gate"]
    assert "block" in lines["privacy_read_guard"]


# ---------------------------------------------------------------------- set ---

def test_set_writes_override_and_traces(tmp_path):
    policy = _seed(tmp_path, "balanced")
    env = _env(tmp_path, policy)
    r = _run(["set", "standards_strict_gate", "warn"], env)
    assert r.returncode == 0, r.stderr
    assert guard_policy.resolve_mode("standards_strict_gate", policy) == "warn"
    changed = [e for e in _trace_events(tmp_path)
               if e["event"] == "guard_config_changed"]
    assert changed and "tester@x.com" in changed[-1]["actor"]
    assert "standards_strict_gate" in changed[-1]["note"]
    assert "block" in changed[-1]["note"] and "warn" in changed[-1]["note"]


def test_set_preset_writes_and_traces(tmp_path):
    policy = _seed(tmp_path, "balanced")
    env = _env(tmp_path, policy)
    r = _run(["set-preset", "lenient"], env)
    assert r.returncode == 0, r.stderr
    assert guard_policy.load_guard_policy(policy)["preset"] == "lenient"
    note = [e for e in _trace_events(tmp_path)
            if e["event"] == "guard_config_changed"][-1]["note"]
    assert "preset" in note and "balanced" in note and "lenient" in note


def test_set_floor_breach_is_flagged_and_logged(tmp_path):
    policy = _seed(tmp_path, "balanced")
    env = _env(tmp_path, policy)
    r = _run(["set", "bash_safety_guard", "off"], env)
    assert r.returncode == 0, r.stderr
    assert "break" in r.stderr.lower()  # operator warned on stderr
    assert guard_policy.resolve_mode("bash_safety_guard", policy) == "off"  # honored
    rec = [e for e in _trace_events(tmp_path)
           if e["event"] == "guard_config_changed"][-1]
    assert "break_glass" in (rec["note"] + str(rec.get("status", "")))


def test_set_rejects_bad_mode(tmp_path):
    policy = _seed(tmp_path, "balanced")
    r = _run(["set", "gate_stage", "sometimes"], _env(tmp_path, policy))
    assert r.returncode == 2


def test_set_rejects_unknown_guard(tmp_path):
    policy = _seed(tmp_path, "balanced")
    r = _run(["set", "ghost_guard", "off"], _env(tmp_path, policy))
    assert r.returncode == 2


def test_set_preset_rejects_bad_preset(tmp_path):
    policy = _seed(tmp_path, "balanced")
    r = _run(["set-preset", "yolo"], _env(tmp_path, policy))
    assert r.returncode == 2


def test_header_comment_preserved_on_write(tmp_path):
    policy = _seed(tmp_path, "balanced")
    _run(["set", "standards_strict_gate", "warn"], _env(tmp_path, policy))
    assert "guard-policy.yaml" in policy.read_text(encoding="utf-8").splitlines()[0]
