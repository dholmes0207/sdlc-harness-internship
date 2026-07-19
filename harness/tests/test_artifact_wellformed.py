"""test_artifact_wellformed.py — producer-side well-formedness validator.

validate_verification_wellformed is a SHIFT-LEFT structural check the test/cook
producer runs right after writing verification.yaml — it catches a check that
declares a `format` but names a non-canonical test_type, or points at a result
file that does not exist / will not parse. It is deliberately NARROWER than the
DoD gate (evaluate_test_policy): it judges STRUCTURE (can the gate read this?),
never POLICY (is the suite sufficient?). An empty-but-valid JUnit is well-formed
even though a DoD floor may later reject it.
"""
import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402

_PASS_JUNIT = ('<testsuite name="u" tests="3" failures="0" errors="0" '
               'skipped="0"/>')
_EMPTY_JUNIT = '<testsuite name="u" tests="0" failures="0" errors="0"/>'


def _plan(tmp_path) -> Path:
    d = tmp_path / "plans" / "260625-0000-x"
    (d / "artifacts").mkdir(parents=True)
    (d / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    return d


def _write_verification(plan_dir: Path, checks):
    (plan_dir / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": plan_dir.name, "actor": "user:a",
        "ts": "2026-06-25T00:00:00+07:00", "verdict": "PASS", "checks": checks,
    }), encoding="utf-8")


def _result(plan_dir: Path, rel: str, body: str):
    (plan_dir / "artifacts" / rel).write_text(body, encoding="utf-8")


# 1 — a format-bearing check with a non-canonical name is rejected ------------
def test_format_check_noncanonical_name_rejected(tmp_path):
    d = _plan(tmp_path)
    _result(d, "u.xml", _PASS_JUNIT)
    _write_verification(d, [
        {"name": "jest-unit", "status": "PASS", "format": "junit", "file": "u.xml"},
    ])
    ok, problems = ac.validate_verification_wellformed(d)
    assert ok is False
    blob = " ".join(problems)
    assert "jest-unit" in blob and "canonical test_type" in blob


# 2 — a format-bearing check that omits `file` is rejected ---------------------
def test_format_check_missing_file_rejected(tmp_path):
    d = _plan(tmp_path)
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit"},
    ])
    ok, problems = ac.validate_verification_wellformed(d)
    assert ok is False
    assert any("unit" in p for p in problems)


# 3 — a format-bearing check pointing at a phantom file is rejected ------------
def test_format_check_phantom_file_rejected(tmp_path):
    d = _plan(tmp_path)
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "nope.xml"},
    ])
    ok, problems = ac.validate_verification_wellformed(d)
    assert ok is False
    assert any("nope.xml" in p or "result file" in p for p in problems)


# 4 — a canonical name + parseable file passes --------------------------------
def test_canonical_name_parseable_file_passes(tmp_path):
    d = _plan(tmp_path)
    _result(d, "u.xml", _PASS_JUNIT)
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "u.xml"},
    ])
    ok, problems = ac.validate_verification_wellformed(d)
    assert ok is True, problems
    assert problems == []


# 5 — a status-only check (no format) is out of scope -------------------------
def test_status_only_check_ignored(tmp_path):
    d = _plan(tmp_path)
    _write_verification(d, [
        {"name": "anything-goes", "status": "PASS"},
    ])
    ok, problems = ac.validate_verification_wellformed(d)
    assert ok is True, problems


# 6 — a manual check travels its own evidence path, not a result file ---------
def test_manual_check_ignored(tmp_path):
    d = _plan(tmp_path)
    _write_verification(d, [
        {"name": "manual", "status": "PASS", "format": "manual",
         "evidence_tier": "anchored", "detail": "smoke run, see anchor"},
    ])
    ok, problems = ac.validate_verification_wellformed(d)
    assert ok is True, problems


# 7 — the CLI exits 1 on a bad artifact, 0 on a good one ----------------------
def test_cli_exit_code(tmp_path):
    bad = _plan(tmp_path)
    _result(bad, "u.xml", _PASS_JUNIT)
    _write_verification(bad, [
        {"name": "jest-unit", "status": "PASS", "format": "junit", "file": "u.xml"},
    ])
    r = subprocess.run(
        [sys.executable, "-m", "artifact_check", "--validate-verification", str(bad)],
        cwd=str(_SCRIPTS), capture_output=True, text=True)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "jest-unit" in (r.stdout + r.stderr)

    good = tmp_path / "plans" / "260625-0001-y"
    (good / "artifacts").mkdir(parents=True)
    (good / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    (good / "artifacts" / "u.xml").write_text(_PASS_JUNIT, encoding="utf-8")
    (good / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": good.name, "actor": "user:a",
        "ts": "2026-06-25T00:00:00+07:00", "verdict": "PASS",
        "checks": [{"name": "unit", "status": "PASS", "format": "junit", "file": "u.xml"}],
    }), encoding="utf-8")
    r2 = subprocess.run(
        [sys.executable, "-m", "artifact_check", "--validate-verification", str(good)],
        cwd=str(_SCRIPTS), capture_output=True, text=True)
    assert r2.returncode == 0, (r2.stdout, r2.stderr)


# 8 — well-formedness is structural: a valid-but-EMPTY JUnit passes (R6) ------
def test_empty_but_valid_junit_passes(tmp_path):
    d = _plan(tmp_path)
    _result(d, "u.xml", _EMPTY_JUNIT)
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "u.xml"},
    ])
    ok, problems = ac.validate_verification_wellformed(d)
    assert ok is True, problems


# 9 — a result file that escapes artifacts/ via symlink is rejected -----------
def test_symlink_escape_file_rejected(tmp_path):
    d = _plan(tmp_path)
    secret = tmp_path / "outside.xml"
    secret.write_text(_PASS_JUNIT, encoding="utf-8")
    link = d / "artifacts" / "u.xml"
    try:
        link.symlink_to(secret)
    except (OSError, NotImplementedError):
        import pytest
        pytest.skip("symlinks unsupported on this platform")
    _write_verification(d, [
        {"name": "unit", "status": "PASS", "format": "junit", "file": "u.xml"},
    ])
    ok, problems = ac.validate_verification_wellformed(d)
    assert ok is False
    assert any("escape" in p.lower() or "forged" in p.lower() for p in problems), problems
