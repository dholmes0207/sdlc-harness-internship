"""test_rule_scan_gate.py — rule-scan artifact consistency at the stage gate.

rule-scan.json is the review-rules layer's output (which rules applied + any
violations). It is deliberately NOT in any stage's `requires:` (forcing its
presence would be an AI-applied boundary the gate can't honestly enforce). But
when it IS present, the gate refuses a CONTRADICTION: a critical rule violation
cannot coexist with a review-decision verdict of PASS. Absent rule-scan → the
gate behaves exactly as before (back-compat).

In-process against artifact_check.check_stage, mirroring test_artifact_check.py.
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402


def _mk_plan(root: Path, name: str = "plan-x") -> Path:
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: in_progress\n---\n\n# %s\n" % (name, name),
        encoding="utf-8")
    (d / "artifacts").mkdir()
    return d


def _verification(plan_dir: Path, *, verdict="PASS"):
    rec = {"stage": "push", "plan": plan_dir.name, "actor": "user:alice",
           "ts": "2026-06-24T08:00:00+07:00",
           "checks": [{"name": "pytest", "status": "PASS"}], "verdict": verdict}
    (plan_dir / "artifacts" / "verification.json").write_text(json.dumps(rec))


def _review(plan_dir: Path, *, verdict="PASS"):
    rec = {"verdict": verdict, "reviewer": "user:bob", "role": "reviewer",
           "rationale": "looks correct"}
    (plan_dir / "artifacts" / "review-decision.json").write_text(json.dumps(rec))


def _rule_scan(plan_dir: Path, *, violations, verdict="PASS", drop=None):
    rec = {"rules_applied": ["security"], "violations": violations,
           "verdict": verdict, "reviewer": "user:reviewer",
           "ts": "2026-06-24T08:00:00+07:00"}
    for k in (drop or []):
        rec.pop(k)
    (plan_dir / "artifacts" / "rule-scan.json").write_text(json.dumps(rec))


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    monkeypatch.delenv("HARNESS_STAGE_POLICY", raising=False)
    return tmp_path


def test_critical_violation_blocks_pass(root):
    d = _mk_plan(root)
    _verification(d)
    _review(d, verdict="PASS")
    _rule_scan(d, verdict="BLOCKED", violations=[
        {"rule_id": "security", "severity": "critical", "file": "a.py",
         "line": 12, "finding": "logs a token"}])
    reason = ac.check_stage("push", root)
    assert reason is not None
    assert "critical" in reason.lower()
    assert "pass" in reason.lower()  # names the contradicting review verdict


def test_clean_rule_scan_allows_pass(root):
    d = _mk_plan(root)
    _verification(d)
    _review(d, verdict="PASS")
    _rule_scan(d, violations=[])  # no violations
    assert ac.check_stage("push", root) is None


def test_rule_scan_absent_no_effect(root):
    d = _mk_plan(root)
    _verification(d)  # push only requires verification; no rule-scan present
    assert ac.check_stage("push", root) is None


def test_rule_scan_schema_fields(root):
    d = _mk_plan(root)
    _verification(d)
    _rule_scan(d, violations=[], drop=["verdict"])  # missing a required field
    reason = ac.check_stage("push", root)
    assert reason is not None
    assert "verdict" in reason
    assert "rule-scan" in reason


def test_coverage_blocks_through_check_stage(root, monkeypatch):
    # the coverage branch reaches the PUBLIC gate entry: a hard-mode rule-scan that
    # records a .py change but applies none of the shipped operational py/common
    # rules is refused by check_stage (not just the private consistency fn).
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "hard")
    d = _mk_plan(root)
    _verification(d)                       # push requires verification → satisfied
    rec = {"rules_applied": [], "violations": [], "verdict": "PASS",
           "reviewer": "user:r", "ts": "2026-06-24T08:00:00+07:00",
           "changed_files": ["a.py"]}      # real shipped tree has py + common rules
    (d / "artifacts" / "rule-scan.json").write_text(json.dumps(rec))
    reason = ac.check_stage("push", root)
    assert reason is not None and "rule-coverage" in reason


def test_coverage_soft_passes_through_check_stage(root, monkeypatch):
    # soft ramp (the 2.2 default) never blocks the public gate on incomplete coverage
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "soft")
    d = _mk_plan(root)
    _verification(d)
    rec = {"rules_applied": [], "violations": [], "verdict": "PASS",
           "reviewer": "user:r", "ts": "2026-06-24T08:00:00+07:00",
           "changed_files": ["a.py"]}
    (d / "artifacts" / "rule-scan.json").write_text(json.dumps(rec))
    assert ac.check_stage("push", root) is None


def test_info_violation_does_not_block(root):
    # An info-severity violation is advisory — it must not block a review PASS.
    d = _mk_plan(root)
    _verification(d)
    _review(d, verdict="PASS")
    _rule_scan(d, violations=[
        {"rule_id": "python", "severity": "info", "file": "a.py",
         "line": 3, "finding": "prefer X | None"}])
    assert ac.check_stage("push", root) is None


_CRIT = [{"rule_id": "security", "severity": "critical", "file": "a.py",
          "line": 1, "finding": "logs a token"}]


def test_critical_self_contradiction_blocks(root):
    # rule-scan records a critical violation but its OWN verdict is PASS — a
    # self-contradictory artifact must block regardless of any review-decision.
    d = _mk_plan(root)
    _verification(d)
    _rule_scan(d, verdict="PASS", violations=_CRIT)  # no review-decision present
    reason = ac.check_stage("push", root)
    assert reason is not None
    assert "critical" in reason.lower()


def test_uppercase_critical_detected(root):
    # severity is matched case-insensitively — 'CRITICAL' must not bypass.
    d = _mk_plan(root)
    _verification(d)
    _review(d, verdict="PASS")
    _rule_scan(d, verdict="BLOCKED", violations=[
        {"rule_id": "s", "severity": "CRITICAL", "file": "a.py",
         "line": 1, "finding": "x"}])
    reason = ac.check_stage("push", root)
    assert reason is not None
    assert "critical" in reason.lower()


def test_whitespace_padded_critical_detected(root):
    # ' critical ' (leading/trailing whitespace) must not bypass the block.
    d = _mk_plan(root)
    _verification(d)
    _review(d, verdict="PASS")
    _rule_scan(d, verdict="BLOCKED", violations=[
        {"rule_id": "s", "severity": " critical ", "file": "a.py",
         "line": 1, "finding": "x"}])
    assert ac.check_stage("push", root) is not None


def test_offenum_severity_fails_closed(root):
    # A violation severity outside {critical, info} (e.g. 'high'/'blocker') fails
    # closed — the gate keys off the exact enum, so an off-enum token must block.
    d = _mk_plan(root)
    _verification(d)
    _review(d, verdict="PASS")
    _rule_scan(d, verdict="PASS", violations=[
        {"rule_id": "s", "severity": "high", "file": "a.py",
         "line": 1, "finding": "x"}])
    reason = ac.check_stage("push", root)
    assert reason is not None
    assert "severity" in reason.lower()


def test_malformed_rule_scan_json_blocks(root):
    # rule-scan.json that is not a JSON object fails closed with a named reason.
    d = _mk_plan(root)
    _verification(d)
    (d / "artifacts" / "rule-scan.json").write_text("[]")  # array, not object
    reason = ac.check_stage("push", root)
    assert reason is not None
    assert "rule-scan" in reason


def test_critical_cannot_ride_pass_with_risk(root):
    # critical + an honestly-BLOCKED rule-scan but a review-decision of
    # PASS_WITH_RISK is still a contradiction (critical cannot ride a soft-accept).
    d = _mk_plan(root)
    _verification(d)
    _review(d, verdict="PASS_WITH_RISK")
    _rule_scan(d, verdict="BLOCKED", violations=_CRIT)
    assert ac.check_stage("push", root) is not None


def test_offenum_verdict_fails_closed(root):
    # a present rule-scan with an off-enum verdict fails closed.
    d = _mk_plan(root)
    _verification(d)
    _rule_scan(d, verdict="Passed", violations=[])  # typo'd verdict
    reason = ac.check_stage("push", root)
    assert reason is not None
    assert "verdict" in reason.lower()


def test_honest_blocked_no_review_passes_light_push(root):
    # Designed boundary: an honestly-BLOCKED critical rule-scan with NO
    # review-decision clears the light push gate (push requires only
    # verification). The critical is still caught at pr/ship, where
    # review-decision is required and must be PASS — a forged PASS there trips
    # the contradiction check. This documents the boundary, not a bypass.
    d = _mk_plan(root)
    _verification(d)
    _rule_scan(d, verdict="BLOCKED", violations=_CRIT)  # no review-decision
    assert ac.check_stage("push", root) is None
