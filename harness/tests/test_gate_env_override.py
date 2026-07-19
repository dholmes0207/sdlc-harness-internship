"""test_gate_env_override.py — the stage gate makes an in-session posture-env
override tamper-EVIDENT.

HARNESS_STAGE_POLICY / HARNESS_PROTECTED_BRANCHES / HARNESS_GUARD_POLICY are
legitimate dev/test flexibility knobs (the pre-push transport hook prefix-scrubs
them, re-judging a push against tracked config). In-session prevention would
break that seam, so the gate does the honest thing instead: when a posture-env
override is active while gating a stage, it emits a `gate_env_override` trace +
a stderr advisory. The override is HONORED (it may be a real dev redirect) but
no longer SILENT — an agent quietly redirecting the policy leaves an audit trail.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_GATE = _HOOKS / "gate_stage.py"
_SHIPPED_POLICY = Path(__file__).resolve().parent.parent / "data" / "stage-policy.yaml"


def _mk_plan_with_verification(root: Path):
    d = root / "plans" / "260612-0800-feature-x"
    (d / "artifacts").mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: x\nstatus: in_progress\n---\n", encoding="utf-8")
    (d / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": d.name, "actor": "user:alice",
        "ts": "2026-06-12T08:00:00+07:00",
        "checks": [{"name": "pytest", "status": "PASS"}], "verdict": "PASS",
    }), encoding="utf-8")
    return d


def _run(tmp_path, command, env_extra=None):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_HOOK_CONFIG", None)
    env.pop("HARNESS_ACTIVE_PLAN", None)
    env.pop("HARNESS_STAGE_POLICY", None)
    env["HARNESS_ROOT"] = str(tmp_path)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_USER"] = "alice"
    for k, v in (env_extra or {}).items():
        env[k] = v
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(_GATE)], input=payload,
                          capture_output=True, text=True, env=env)


def _events(tmp_path):
    out = []
    trace = tmp_path / "state" / "trace"
    if trace.is_dir():
        for f in sorted(trace.glob("trace-*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                out.append(json.loads(line))
    return out


def test_env_override_present_is_traced_and_advised(tmp_path):
    _mk_plan_with_verification(tmp_path)
    # point at the SHIPPED policy → identical verdict, but env override is set
    proc = _run(tmp_path, "git push",
                env_extra={"HARNESS_STAGE_POLICY": str(_SHIPPED_POLICY)})
    assert proc.returncode == 0          # honored: same policy, push still passes
    over = [e for e in _events(tmp_path) if e["event"] == "gate_env_override"]
    assert over, "an active posture-env override must be traced"
    assert "HARNESS_STAGE_POLICY" in (over[0].get("note") or "")
    assert over[0]["actor"] == "user:alice"
    assert "override" in proc.stderr.lower()


def test_no_override_no_event(tmp_path):
    _mk_plan_with_verification(tmp_path)
    proc = _run(tmp_path, "git push")   # no posture env set
    assert proc.returncode == 0
    over = [e for e in _events(tmp_path) if e["event"] == "gate_env_override"]
    assert over == []


def test_override_irrelevant_when_no_stage(tmp_path):
    # a non-stage command never reaches the gate body → no override noise
    proc = _run(tmp_path, "ls -la",
                env_extra={"HARNESS_GUARD_POLICY": "/tmp/x.yaml"})
    assert proc.returncode == 0
    assert [e for e in _events(tmp_path) if e["event"] == "gate_env_override"] == []
