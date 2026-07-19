"""test_security_gate_via_policy.py — security/a11y ride the SAME DoD gate.

No separate security hook: a `components:` glob (auth/payment) adds `security`
as a required, hard test type for any change that touches a matching path. The
P1 DoD machinery then enforces it by re-reading the SARIF result:
  - a sensitive change with no security SARIF → FAIL (hard).
  - a security SARIF with a critical/error finding → FAIL (hard).
  - a clean security SARIF → PASS.
  - a non-sensitive change is NOT security-gated (no glob match).
a11y is the same mechanism with an axe SARIF.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402
import test_result_readers as rdr  # noqa: E402

_PASS_JUNIT = '<testsuite name="u" tests="2" failures="0" errors="0" skipped="0"/>'


def _sarif(results):
    return json.dumps({"version": "2.1.0",
                       "runs": [{"tool": {"driver": {"name": "semgrep"}},
                                 "results": results}]})


def _plan(tmp_path) -> Path:
    d = tmp_path / "plans" / "260624-0000-sec"
    (d / "artifacts" / "results").mkdir(parents=True)
    (d / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    return d


def _verification(plan_dir: Path, checks):
    (plan_dir / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": plan_dir.name, "actor": "user:a",
        "ts": "2026-06-24T00:00:00+07:00", "verdict": "PASS", "checks": checks,
    }), encoding="utf-8")


def _result(plan_dir: Path, rel: str, body: str):
    (plan_dir / "artifacts" / rel).write_text(body, encoding="utf-8")


# --- the SARIF verdict helper --------------------------------------------------
def test_sarif_verdict_fails_on_error_level():
    v, detail = rdr.sarif_verdict({"results": [
        {"ruleId": "X", "level": "error", "severity": "high"}]})
    assert v == "FAIL"
    assert "1" in detail or "high" in detail.lower() or "error" in detail.lower()


def test_sarif_verdict_passes_when_clean():
    v, _ = rdr.sarif_verdict({"results": [
        {"ruleId": "X", "level": "note", "severity": "low"}]})
    assert v == "PASS"


# --- the gate: sensitive path requires security --------------------------------
def test_auth_change_missing_security_blocks(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    # a refactor (soft class) that TOUCHES auth/** must still hard-require security.
    v = ac.evaluate_test_policy(d, "refactor", ["src/auth/login.py"],
                               root=tmp_path, ambiguous=True)
    assert v.status == "FAIL"
    assert "security" in v.reason
    assert v.enforcement == "hard"  # the component lifts the security req to hard


def test_auth_change_with_critical_security_blocks(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _result(d, "results/sec.sarif", _sarif([
        {"ruleId": "B602", "level": "error",
         "properties": {"security-severity": "9.1"}}]))
    _verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "results/unit.xml"},
        {"name": "security", "status": "PASS", "format": "sarif", "file": "results/sec.sarif"}])
    # refactor (needs only unit) touching payment/** → security is the lone gap.
    v = ac.evaluate_test_policy(d, "refactor", ["src/payment/charge.py"],
                               root=tmp_path, ambiguous=True)
    assert v.status == "FAIL"
    assert "security" in v.reason


def test_auth_change_with_clean_security_passes(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _result(d, "results/int.xml", _PASS_JUNIT)
    _result(d, "results/cov.xml", '<coverage line-rate="0.95" branch-rate="0.9"/>')
    _result(d, "results/sec.sarif", _sarif([
        {"ruleId": "B101", "level": "note"}]))
    _verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "results/unit.xml"},
        {"name": "integration", "status": "PASS", "format": "junit", "file": "results/int.xml"},
        {"name": "coverage", "status": "PASS", "format": "cobertura", "file": "results/cov.xml"},
        {"name": "security", "status": "PASS", "format": "sarif", "file": "results/sec.sarif"}])
    v = ac.evaluate_test_policy(d, "feature", ["src/auth/session.py"], root=tmp_path)
    assert v.status == "PASS", v.reason


def test_non_sensitive_change_not_security_gated(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _result(d, "results/reg.xml", _PASS_JUNIT)
    _verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "results/unit.xml"},
        {"name": "regression", "status": "PASS", "format": "junit", "file": "results/reg.xml"}])
    # a bugfix nowhere near auth/payment → security is NOT required.
    v = ac.evaluate_test_policy(d, "bugfix", ["src/utils/format.py"], root=tmp_path)
    assert v.status == "PASS", v.reason


# --- end-to-end wiring through gate_stage (HARNESS_CHANGED_PATHS seam) ----------
import os  # noqa: E402
import subprocess  # noqa: E402

_GATE = Path(__file__).resolve().parent.parent / "hooks" / "gate_stage.py"


def _run_gate(tmp_path, command, env_extra):
    env = dict(os.environ)
    for k in ("PYTEST_CURRENT_TEST", "HARNESS_HOOK_CONFIG", "HARNESS_ACTIVE_PLAN",
              "HARNESS_STAGE_POLICY", "HARNESS_GUARD_POLICY", "CI", "GITLAB_CI",
              "GITHUB_ACTIONS"):
        env.pop(k, None)
    env["HARNESS_ROOT"] = str(tmp_path)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_USER"] = "alice"
    env.update(env_extra)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(_GATE)], input=payload,
                          capture_output=True, text=True, env=env)


def test_gate_blocks_auth_change_without_security(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _verification(d, [{"name": "unit", "status": "PASS", "format": "junit",
                       "file": "results/unit.xml"}])
    proc = _run_gate(tmp_path, "git push", {
        "HARNESS_CHANGE_CLASS": "refactor",
        "HARNESS_CHANGED_PATHS": "src/auth/login.py"})
    # Personal-first: the security-review requirement is advisory locally (exit 0),
    # enforced by the remote receipts-gate.
    assert proc.returncode == 0, proc.stderr
    assert "[advisory]" in proc.stderr and "security" in proc.stderr


def test_gate_clears_auth_change_with_clean_security(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _result(d, "results/sec.sarif", _sarif([{"ruleId": "B101", "level": "note"}]))
    _verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "results/unit.xml"},
        {"name": "security", "status": "PASS", "format": "sarif", "file": "results/sec.sarif"}])
    proc = _run_gate(tmp_path, "git push", {
        "HARNESS_CHANGE_CLASS": "refactor",
        "HARNESS_CHANGED_PATHS": "src/auth/login.py"})
    assert proc.returncode == 0, proc.stderr
