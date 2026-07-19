"""test_grace_expiry_coercion.py — a config typo must FAIL loud, never crash.

PyYAML parses an UNQUOTED `expires: 2026-12-31` to a `datetime.date`, while the
gate's injected `now` stays a plain string. The old `date >= str` raised
TypeError, which the DoD branch in gate_stage swallowed as fail-OPEN. This is a
PRECONDITION for the fail-closed flip: once an evaluator crash blocks, an
unquoted expiry would block every push over one missing quote. So the common
config-typo must resolve to a FAIL-with-message ("quote it"), not a crash, and a
correctly-quoted grace must keep working.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402


def _tier1(grace_line, enforcement="soft", required="[unit, integration]"):
    return (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  feature:\n"
        "    required: %s\n"
        "    enforcement: %s\n"
        "    grace: %s\n"
        "test_types:\n"
        "  unit: { format: junit }\n"
        "  integration: { format: junit }\n" % (required, enforcement, grace_line))


def _w(p, body):
    p.write_text(body, encoding="utf-8")
    return p


def _plan(tmp_path):
    d = tmp_path / "plans" / "260625-0000-g"
    (d / "artifacts" / "results").mkdir(parents=True)
    (d / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    # only a unit result; integration is the gap the grace papers over.
    (d / "artifacts" / "results" / "unit.xml").write_text(
        '<testsuite name="u" tests="1" failures="0" errors="0" skipped="0"/>',
        encoding="utf-8")
    (d / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": d.name, "actor": "user:a",
        "ts": "2026-06-25T00:00:00+07:00", "verdict": "PASS",
        "checks": [{"name": "unit", "status": "PASS", "format": "junit",
                    "file": "results/unit.xml"}],
    }), encoding="utf-8")
    return d


# 1 — an UNQUOTED expires (date object) FAILs with a message, never TypeError --
def test_unquoted_expires_does_not_crash(tmp_path, monkeypatch):
    # unquoted → PyYAML yields datetime.date; old code did `date >= str` (crash)
    monkeypatch.setenv("HARNESS_TEST_POLICY", str(_w(
        tmp_path / "t1.yaml", _tier1("{ reason: 'wip', expires: 2026-12-31 }"))))
    monkeypatch.setenv("HARNESS_NOW", "2026-06-24")
    d = _plan(tmp_path)
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    assert v.status == "FAIL"
    assert "quote" in (v.reason or "").lower(), v.reason


# 2 — a correctly-quoted future expires is still honored (GRACE) --------------
def test_quoted_expires_still_works(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TEST_POLICY", str(_w(
        tmp_path / "t1.yaml",
        _tier1("{ reason: 'wip', expires: '2030-01-01' }", required="[unit]"))))
    monkeypatch.setenv("HARNESS_NOW", "2026-06-24")
    d = _plan(tmp_path)
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    # only unit is required and it passes → an in-date grace reports GRACE.
    assert v.status == "GRACE", v
    assert "quote" not in (v.reason or "").lower()


# 2b — a scalar `coverage` typo FAILs with a message, never AttributeError ----
def test_scalar_coverage_does_not_crash(tmp_path, monkeypatch):
    # `coverage: 80` (vs `coverage: {line: 80}`) is a common typo; the old code
    # crashed `(spec.get("coverage") or {}).get("line")` at gate time.
    policy = (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  feature: { required: [unit], enforcement: hard, coverage: 80 }\n"
        "test_types:\n"
        "  unit: { format: junit }\n")
    monkeypatch.setenv("HARNESS_TEST_POLICY", str(_w(tmp_path / "t1.yaml", policy)))
    monkeypatch.setenv("HARNESS_NOW", "2026-06-24")
    d = _plan(tmp_path)
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    assert v.status == "FAIL"
    assert "coverage" in (v.reason or "").lower(), v.reason


# 2c — a quoted-but-non-ISO expires is rejected at load (not silently mis-judged)
def test_non_iso_quoted_expires_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TEST_POLICY", str(_w(
        tmp_path / "t1.yaml", _tier1("{ reason: 'wip', expires: '12/31/2030' }"))))
    monkeypatch.setenv("HARNESS_NOW", "2026-06-24")
    d = _plan(tmp_path)
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    assert v.status == "FAIL"
    assert "iso" in (v.reason or "").lower() or "date" in (v.reason or "").lower(), v.reason


# 2d — a tier-2 scalar coverage typo FAILs with an actionable message ---------
def test_tier2_scalar_coverage_actionable(tmp_path, monkeypatch):
    t1 = (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  feature: { required: [unit], enforcement: hard, coverage: { line: 80 } }\n"
        "test_types:\n"
        "  unit: { format: junit }\n")
    monkeypatch.setenv("HARNESS_TEST_POLICY", str(_w(tmp_path / "t1.yaml", t1)))
    _w(tmp_path / "test-policy.yaml",
       'schema_version: "1.0"\nchange_classes:\n  feature: { coverage: 50 }\n')
    monkeypatch.setenv("HARNESS_NOW", "2026-06-24")
    d = _plan(tmp_path)
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    assert v.status == "FAIL"
    assert "coverage" in (v.reason or "").lower(), v.reason


# 2e — a scalar test_types entry does NOT crash the evaluator -----------------
def test_scalar_test_type_does_not_crash(tmp_path, monkeypatch):
    t1 = (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  feature: { required: [unit], enforcement: hard }\n"
        "test_types:\n"
        "  unit: junit\n")  # scalar; should be { format: junit }
    monkeypatch.setenv("HARNESS_TEST_POLICY", str(_w(tmp_path / "t1.yaml", t1)))
    monkeypatch.setenv("HARNESS_NOW", "2026-06-24")
    d = _plan(tmp_path)
    # the required `unit` check is present but carries no format of its own.
    (d / "artifacts" / "results" / "unit.xml").unlink()
    (d / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": d.name, "actor": "user:a",
        "ts": "2026-06-25T00:00:00+07:00", "verdict": "PASS",
        "checks": [{"name": "unit", "status": "PASS"}],
    }), encoding="utf-8")
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)  # must NOT raise
    assert v.status == "FAIL"


# 3 — an expired grace restores the full hard gate (regression of restore path)
def test_expired_grace_restores_hard(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_TEST_POLICY", str(_w(
        tmp_path / "t1.yaml", _tier1("{ reason: 'wip', expires: '2026-01-01' }"))))
    monkeypatch.setenv("HARNESS_NOW", "2026-06-24")  # past the grace
    d = _plan(tmp_path)
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    assert v.status == "FAIL"
    assert v.enforcement == "hard"
    assert "integration" in v.reason
