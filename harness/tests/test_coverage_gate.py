"""test_coverage_gate.py — the rule-coverage branch of _rule_scan_consistency (P5).

The gate derives every applicable operational rule from scope ∩ the diff the
producer RECORDED (rule-scan.changed_files — never a fresh git diff, which would
check a different file universe than was reviewed) and refuses a rule-scan that
omits one. A skip needs a capability-gated rule-coverage-skip artifact (not an
in-diff comment a rule-ignoring actor could forge); a floor rule cannot be
skipped at all. Ramp: off (no-op) / soft (warn) / hard (block). The pre-existing
critical→BLOCKED contradiction logic is untouched.
"""

import json
import sys
from pathlib import Path

import pytest
import yaml as _yaml

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402

# Shipped roster seeds this identity as admin (carries override_gate).
ADMIN = "user:hieu.bt2409@gmail.com"


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("HARNESS_RULE_COVERAGE", raising=False)
    return tmp_path


_OP_TREE = """id: STD-REVIEW-PY
type: std_area
zone: operational
title: "Python Review"
rule_groups:
  - id: STD-REVIEW-PY-RG1
    title: "PY"
    rules:
      - id: STD-REVIEW-PY-RG1-R1
        title: "r1"
        scope: ["**/*.py"]
        severity: info
      - id: STD-REVIEW-PY-RG1-R2
        title: "r2 floor"
        scope: ["**/*.py"]
        severity: critical
        floor: true
"""


def _op_tree(root):
    areas = root / "harness" / "standards" / "areas"
    areas.mkdir(parents=True, exist_ok=True)
    (areas / "STD-REVIEW-PY.std.yaml").write_text(_OP_TREE, encoding="utf-8")


def _mk_plan(root, name="260625-1200-cov", author="user:someone-else@x.com"):
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: in_progress\nauthor: %s\n---\n\n# %s\n"
        % (name, author, name), encoding="utf-8")
    (d / "artifacts").mkdir()
    return d


def _rule_scan(plan_dir, *, rules_applied, changed_files, violations=None,
               verdict="PASS"):
    rec = {
        "rules_applied": rules_applied,
        "violations": violations or [],
        "verdict": verdict,
        "reviewer": "user:bob",
        "ts": "2026-06-25T12:00:00+07:00",
    }
    if changed_files is not None:
        rec["changed_files"] = changed_files
    (plan_dir / "artifacts" / "rule-scan.json").write_text(
        json.dumps(rec), encoding="utf-8")


def _skip(plan_dir, rule_id, *, actor=ADMIN, reason="known-noise, tracked"):
    rec = {"skips": [{"plan": plan_dir.name, "rule_id": rule_id,
                      "actor": actor, "reason": reason,
                      "ts": "2026-06-25T12:00:00+07:00"}]}
    (plan_dir / "artifacts" / "rule-coverage-skip.json").write_text(
        json.dumps(rec), encoding="utf-8")


# --- no-op cases (back-compat: presence-only) --------------------------------

def test_coverage_no_rulescan_noop(root):
    d = _mk_plan(root)
    assert ac._rule_scan_consistency(d) is None


def test_coverage_no_changed_files_noop(root, monkeypatch):
    # an old-shape rule-scan with no changed_files → coverage no-op even in hard
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "hard")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=[], changed_files=None)
    assert ac._rule_scan_consistency(d) is None


# --- block / pass ------------------------------------------------------------

def test_coverage_block_on_missing(root, monkeypatch):
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "hard")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=["STD-REVIEW-PY-RG1-R1"], changed_files=["a.py"])
    reason = ac._rule_scan_consistency(d)
    assert reason and "STD-REVIEW-PY-RG1-R2" in reason   # the omitted rule named


def test_coverage_pass_complete(root, monkeypatch):
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "hard")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=["STD-REVIEW-PY-RG1-R1", "STD-REVIEW-PY-RG1-R2"],
               changed_files=["a.py"], verdict="BLOCKED",
               violations=[{"rule_id": "STD-REVIEW-PY-RG1-R2", "severity": "critical",
                            "file": "a.py", "line": 1, "finding": "x"}])
    assert ac._rule_scan_consistency(d) is None


def test_coverage_uses_recorded_changed_files(root, monkeypatch):
    # [F1] coverage derives from rule-scan.changed_files and never shells out to
    # git (a git re-derive would check a different file universe).
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "hard")
    import subprocess
    real_run = subprocess.run

    def _no_git(cmd, *a, **k):
        argv = cmd if isinstance(cmd, (list, tuple)) else [cmd]
        assert "git" not in str(argv[0]), "coverage must not invoke git"
        return real_run(cmd, *a, **k)

    monkeypatch.setattr(subprocess, "run", _no_git)
    _op_tree(root)
    d = _mk_plan(root)
    # CF names a .py file → both py rules applicable; rules_applied is empty
    _rule_scan(d, rules_applied=[], changed_files=["a.py"])
    reason = ac._rule_scan_consistency(d)
    assert reason and "STD-REVIEW-PY-RG1-R1" in reason


# --- ramp --------------------------------------------------------------------

def test_coverage_derivation_tracks_recorded_not_worktree(root, monkeypatch):
    # [F1, positive] applicability derives from rule-scan.changed_files VERBATIM,
    # not the live working tree: a recorded .py name that does not exist on disk
    # still makes the py rules applicable; a recorded non-py file makes them not.
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "hard")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=[], changed_files=["ghost.py"])   # no such file on disk
    reason = ac._rule_scan_consistency(d)
    assert reason and "STD-REVIEW-PY-RG1-R1" in reason            # derived from the name
    _rule_scan(d, rules_applied=[], changed_files=["notes.md"])   # no py → no py rule
    assert ac._rule_scan_consistency(d) is None


def test_coverage_soft_ramp(root, monkeypatch):
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "soft")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=[], changed_files=["a.py"])
    assert ac._rule_scan_consistency(d) is None       # soft warns, never blocks


def test_coverage_off_noop(root, monkeypatch):
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "off")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=[], changed_files=["a.py"])
    assert ac._rule_scan_consistency(d) is None


# --- skip: capability-gated [F2] ---------------------------------------------

def test_coverage_skip_noncap_actor_refused(root, monkeypatch):
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "hard")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=["STD-REVIEW-PY-RG1-R2"], changed_files=["a.py"])
    _skip(d, "STD-REVIEW-PY-RG1-R1", actor="user:nobody@x.com")   # no override_gate
    reason = ac._rule_scan_consistency(d)
    assert reason and "STD-REVIEW-PY-RG1-R1" in reason


# --- floor: not skippable [F3] -----------------------------------------------

def test_floor_rule_not_skippable(root, monkeypatch):
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "hard")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=["STD-REVIEW-PY-RG1-R1"], changed_files=["a.py"])
    _skip(d, "STD-REVIEW-PY-RG1-R2")            # valid admin skip of a FLOOR rule
    reason = ac._rule_scan_consistency(d)
    assert reason and "STD-REVIEW-PY-RG1-R2" in reason   # floor cannot be skipped


# --- fail-closed -------------------------------------------------------------

def test_coverage_failclosed_on_error(root, monkeypatch):
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "hard")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=[], changed_files=["a.py"])
    import rule_view
    monkeypatch.setattr(rule_view, "load_rules_from_tree",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    reason = ac._rule_scan_consistency(d)
    assert reason and "coverage" in reason.lower()


def test_coverage_soft_does_not_block_on_internal_error(root, monkeypatch):
    # soft ramp must never block, even when the coverage check errors internally
    monkeypatch.setenv("HARNESS_RULE_COVERAGE", "soft")
    _op_tree(root)
    d = _mk_plan(root)
    _rule_scan(d, rules_applied=[], changed_files=["a.py"])
    import rule_view
    monkeypatch.setattr(rule_view, "load_rules_from_tree",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ac._rule_scan_consistency(d) is None   # soft warns, never blocks


# --- the old contradiction logic is untouched --------------------------------

def test_critical_contradiction_still_blocks(root):
    _op_tree(root)
    d = _mk_plan(root)
    (d / "artifacts" / "rule-scan.yaml").write_text(_yaml.safe_dump({
        "rules_applied": ["STD-REVIEW-PY-RG1-R1", "STD-REVIEW-PY-RG1-R2"],
        "verdict": "PASS", "reviewer": "user:bob", "changed_files": ["a.py"],
        "ts": "2026-06-25T12:00:00+07:00",
        "violations": [{"rule_id": "x", "severity": "critical"}],
    }), encoding="utf-8")
    reason = ac._rule_scan_consistency(d)
    assert reason and "critical" in reason.lower()       # contradiction still caught
