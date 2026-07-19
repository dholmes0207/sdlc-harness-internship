"""test_evaluate_test_policy.py — the DoD evaluation folded into artifact_check.

evaluate_test_policy resolves the required test types for a change-class from
test-policy, then RE-READS the raw normalized result files (never trusting the
artifact's self-declared status/verdict) to reach its own PASS/FAIL. Honest
posture: it proves the result FILES say pass, not who produced them.

Returns a Verdict(status, reason, enforcement):
  - a required type with no result file / a failing raw result / coverage below
    threshold → status FAIL with a reason naming the gap.
  - a soft (or ambiguous) class never yields a hard FAIL the caller blocks on —
    enforcement rides along so gate_stage routes soft → advisory.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402

_PASS_JUNIT = ('<testsuite name="u" tests="3" failures="0" errors="0" '
               'skipped="0"/>')
_FAIL_JUNIT = ('<testsuite name="u" tests="3" failures="1" errors="0" '
               'skipped="0"/>')
_COV_LOW = '<coverage line-rate="0.55" branch-rate="0.4"/>'
_COV_OK = '<coverage line-rate="0.92" branch-rate="0.8"/>'


def _plan(tmp_path) -> Path:
    d = tmp_path / "plans" / "260624-0000-x"
    (d / "artifacts" / "results").mkdir(parents=True)
    (d / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    return d


def _write_verification(plan_dir: Path, checks):
    (plan_dir / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": plan_dir.name, "actor": "user:a",
        "ts": "2026-06-24T00:00:00+07:00", "verdict": "PASS", "checks": checks,
    }), encoding="utf-8")


def _result(plan_dir: Path, rel: str, body: str):
    (plan_dir / "artifacts" / rel).write_text(body, encoding="utf-8")


# --- missing required type -----------------------------------------------------
def test_bugfix_missing_regression_fails(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit",
         "file": "results/unit.xml"}])
    v = ac.evaluate_test_policy(d, "bugfix", ["src/auth.py"], root=tmp_path)
    assert v.status == "FAIL"
    assert "regression" in v.reason
    assert v.enforcement == "hard"


# --- raw failure caught even when status claims PASS ---------------------------
def test_failing_raw_result_overrides_claimed_pass(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _result(d, "results/reg.xml", _FAIL_JUNIT)  # raw says 1 failure
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit",
         "file": "results/unit.xml"},
        # the agent CLAIMS PASS but the raw file has a failure — gate re-reads.
        {"name": "regression", "status": "PASS", "format": "junit",
         "file": "results/reg.xml"}])
    v = ac.evaluate_test_policy(d, "bugfix", ["src/auth.py"], root=tmp_path)
    assert v.status == "FAIL"
    assert "regression" in v.reason and "fail" in v.reason.lower()


# --- coverage threshold --------------------------------------------------------
def test_feature_coverage_below_threshold_fails(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _result(d, "results/int.xml", _PASS_JUNIT)
    _result(d, "results/cov.xml", _COV_LOW)  # 55% < 80
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "results/unit.xml"},
        {"name": "integration", "status": "PASS", "format": "junit", "file": "results/int.xml"},
        {"name": "coverage", "status": "PASS", "format": "cobertura", "file": "results/cov.xml"}])
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    assert v.status == "FAIL"
    assert "coverage" in v.reason.lower()


# --- happy path ----------------------------------------------------------------
def test_feature_complete_and_passing(tmp_path):
    d = _plan(tmp_path)
    _result(d, "results/unit.xml", _PASS_JUNIT)
    _result(d, "results/int.xml", _PASS_JUNIT)
    _result(d, "results/cov.xml", _COV_OK)
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "results/unit.xml"},
        {"name": "integration", "status": "PASS", "format": "junit", "file": "results/int.xml"},
        {"name": "coverage", "status": "PASS", "format": "cobertura", "file": "results/cov.xml"}])
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    assert v.status == "PASS", v.reason


# --- soft / ambiguous never hard-fails -----------------------------------------
def test_refactor_missing_is_soft_not_hard(tmp_path):
    d = _plan(tmp_path)
    _write_verification(d, [])  # nothing supplied
    v = ac.evaluate_test_policy(d, "refactor", ["src/x.py"], root=tmp_path,
                               ambiguous=True)
    # refactor ships enforcement: soft → a gap is advisory, not a hard block.
    assert v.status == "FAIL"  # missing tests → test DoD not met
    assert v.enforcement == "soft"  # refactor gap is advisory, not a hard block


# --- symlink-escape containment on the result file -----------------------------
def test_result_file_escaping_plan_dir_is_refused(tmp_path):
    d = _plan(tmp_path)
    outside = tmp_path / "evil.xml"
    outside.write_text(_PASS_JUNIT, encoding="utf-8")
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit",
         "file": "../../evil.xml"}])
    v = ac.evaluate_test_policy(d, "bugfix", ["src/a.py"], root=tmp_path)
    # a path escaping artifacts/ must not be read as a valid result → FAIL
    assert v.status == "FAIL"
