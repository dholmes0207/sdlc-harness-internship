"""test_gate_stage_dod.py — the DoD gate end-to-end through gate_stage.

Subprocess tests pin the REAL fail-closed contract: a hard stage whose
change-class is missing a required test type (or whose raw result fails, or
whose coverage is below threshold) → exit 2 + actionable reason; a complete +
passing set → exit 0; a graced (soft) class → exit 0; a soft class (refactor) →
exit 0 advisory. The change-class is injected via HARNESS_CHANGE_CLASS (an
explicit, traced override) so the scenario is deterministic without a contrived
git diff.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_GATE = _HOOKS / "gate_stage.py"

_PASS_JUNIT = '<testsuite name="u" tests="3" failures="0" errors="0" skipped="0"/>'
_FAIL_JUNIT = '<testsuite name="u" tests="3" failures="1" errors="0" skipped="0"/>'


def _mk_plan(root: Path, name="260624-0000-dod") -> Path:
    d = root / "plans" / name
    (d / "artifacts" / "results").mkdir(parents=True)
    (d / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    return d


def _verification(plan_dir: Path, checks):
    (plan_dir / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": plan_dir.name, "actor": "user:alice",
        "ts": "2026-06-24T00:00:00+07:00", "verdict": "PASS", "checks": checks,
    }), encoding="utf-8")


def _result(plan_dir: Path, rel: str, body: str):
    (plan_dir / "artifacts" / rel).write_text(body, encoding="utf-8")


def _run(tmp_path, command, env_extra=None):
    env = dict(os.environ)
    for k in ("PYTEST_CURRENT_TEST", "HARNESS_HOOK_CONFIG", "HARNESS_ACTIVE_PLAN",
              "HARNESS_STAGE_POLICY", "HARNESS_GUARD_POLICY", "CI", "GITLAB_CI",
              "GITHUB_ACTIONS"):
        env.pop(k, None)
    env["HARNESS_ROOT"] = str(tmp_path)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_USER"] = "alice"
    for k, v in (env_extra or {}).items():
        env[k] = v
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(_GATE)], input=payload,
                          capture_output=True, text=True, env=env)


def _trace_events(tmp_path):
    out = []
    trace = tmp_path / "state" / "trace"
    if trace.is_dir():
        for f in sorted(trace.glob("trace-*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                out.append(json.loads(line))
    return out


def test_missing_required_type_advisory(tmp_path):
    # Personal-first: an unmet DoD is advisory locally (exit 0 + [advisory]), the
    # remote receipts-gate is the hard layer.
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    proc = _run(tmp_path, "git push", {"HARNESS_CHANGE_CLASS": "bugfix"})
    assert proc.returncode == 0, proc.stderr
    assert "[advisory]" in proc.stderr and "regression" in proc.stderr


def test_failing_raw_result_advisory(tmp_path):
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _result(d, "results/reg.xml", _FAIL_JUNIT)
    _verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "results/unit.xml"},
        {"name": "regression", "status": "PASS", "format": "junit", "file": "results/reg.xml"}])
    proc = _run(tmp_path, "git push", {"HARNESS_CHANGE_CLASS": "bugfix"})
    assert proc.returncode == 0, proc.stderr
    assert "[advisory]" in proc.stderr


def test_complete_and_passing_clears(tmp_path):
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _result(d, "results/reg.xml", _PASS_JUNIT)
    _verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "results/unit.xml"},
        {"name": "regression", "status": "PASS", "format": "junit", "file": "results/reg.xml"}])
    proc = _run(tmp_path, "git push", {"HARNESS_CHANGE_CLASS": "bugfix"})
    assert proc.returncode == 0, proc.stderr
    assert any(e["event"] == "gate_pass" for e in _trace_events(tmp_path))


def test_soft_refactor_does_not_block(tmp_path):
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    # refactor ships enforcement: soft → even a gap stays advisory.
    proc = _run(tmp_path, "git push", {"HARNESS_CHANGE_CLASS": "refactor"})
    assert proc.returncode == 0, proc.stderr


def test_grace_downgrades_to_soft_and_traces(tmp_path):
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    # tier-2 override at repo root graces bugfix down to soft (missing regression
    # would otherwise block) — must carry a reason and emit policy_grace.
    (tmp_path / "test-policy.yaml").write_text(
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  bugfix:\n"
        "    required: [unit]\n"
        "    enforcement: soft\n"
        "    grace: { reason: 'legacy module under migration', expires: '2099-01-01' }\n",
        encoding="utf-8")
    proc = _run(tmp_path, "git push", {"HARNESS_CHANGE_CLASS": "bugfix"})
    assert proc.returncode == 0, proc.stderr
    assert any(e["event"] == "policy_grace" for e in _trace_events(tmp_path))


def test_lenient_preset_warns_instead_of_blocking(tmp_path):
    d = _mk_plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    gpol = tmp_path / "guard.yaml"
    gpol.write_text("preset: lenient\noverrides: {}\n", encoding="utf-8")
    # bugfix missing regression, but lenient → test_policy_dod warns, not blocks.
    proc = _run(tmp_path, "git push",
                {"HARNESS_CHANGE_CLASS": "bugfix", "HARNESS_GUARD_POLICY": str(gpol)})
    assert proc.returncode == 0, proc.stderr
