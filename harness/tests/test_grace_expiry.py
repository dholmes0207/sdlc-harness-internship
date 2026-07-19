"""test_grace_expiry.py — a grace has TEETH: it expires and re-arms the gate.

A grace that only flags is a permanent backdoor. So:
  - a tier-2 grace MUST carry `expires` (ISO date) AND `reason`; either missing
    → policy load REJECTS (fail-loud).
  - past `expires`, the gate STOPS honoring the grace and re-applies the FULL
    hard gate (enforcement + the pre-grace required set), not just a flag.
  - within `expires`, the grace is honored (soft).
The current date is INJECTED (HARNESS_NOW) so the decision is deterministic and
testable — never a wall-clock read inside the gate.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import test_policy as tp  # noqa: E402
import artifact_check as ac  # noqa: E402

_TIER1 = (
    'schema_version: "1.0"\n'
    "change_classes:\n"
    "  feature: { required: [unit, integration], enforcement: hard }\n"
    "test_types:\n"
    "  unit: { format: junit }\n"
    "  integration: { format: junit }\n")


def _tier2(grace_body):
    return (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  feature:\n"
        "    required: [unit]\n"
        "    enforcement: soft\n"
        "    grace: %s\n" % grace_body)


def _w(p, body):
    p.write_text(body, encoding="utf-8")
    return p


# --- load-time validation ------------------------------------------------------
def test_grace_without_expires_is_rejected(tmp_path):
    t1 = _w(tmp_path / "t1.yaml", _TIER1)
    t2 = _w(tmp_path / "t2.yaml", _tier2("{ reason: 'legacy' }"))
    try:
        tp.load_test_policy(tier1_path=t1, tier2_path=t2, trace=False)
    except tp.TestPolicyError as e:
        assert "expires" in str(e).lower()
    else:
        raise AssertionError("a grace with no expires must be rejected")


def test_grace_with_reason_and_expires_loads(tmp_path):
    t1 = _w(tmp_path / "t1.yaml", _TIER1)
    t2 = _w(tmp_path / "t2.yaml",
            _tier2("{ reason: 'legacy', expires: '2099-01-01' }"))
    pol = tp.load_test_policy(tier1_path=t1, tier2_path=t2, trace=False)
    g = pol["change_classes"]["feature"]["grace"]
    assert g["expires"] == "2099-01-01" and g["reason"]


# --- gate-time expiry teeth ----------------------------------------------------
def _plan(tmp_path):
    d = tmp_path / "plans" / "260624-0000-g"
    (d / "artifacts" / "results").mkdir(parents=True)
    (d / "plan.md").write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    # only a unit result; integration is the gap the grace papers over.
    (d / "artifacts" / "results" / "unit.xml").write_text(
        '<testsuite name="u" tests="1" failures="0" errors="0" skipped="0"/>',
        encoding="utf-8")
    (d / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": d.name, "actor": "user:a",
        "ts": "2026-06-24T00:00:00+07:00", "verdict": "PASS",
        "checks": [{"name": "unit", "status": "PASS", "format": "junit",
                    "file": "results/unit.xml"}],
    }), encoding="utf-8")
    return d


def test_grace_in_date_is_honored_soft(tmp_path, monkeypatch):
    _w(tmp_path / "test-policy.yaml",
       _tier2("{ reason: 'legacy', expires: '2099-01-01' }"))
    monkeypatch.setenv("HARNESS_TEST_POLICY", str(_w(tmp_path / "t1.yaml", _TIER1)))
    monkeypatch.setenv("HARNESS_NOW", "2026-06-24")
    d = _plan(tmp_path)
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    # graced → soft; the missing integration does not hard-block.
    assert v.enforcement == "soft"
    assert v.status != "FAIL" or v.enforcement == "soft"


def test_grace_past_expiry_re_blocks_hard(tmp_path, monkeypatch):
    _w(tmp_path / "test-policy.yaml",
       _tier2("{ reason: 'legacy', expires: '2026-01-01' }"))
    monkeypatch.setenv("HARNESS_TEST_POLICY", str(_w(tmp_path / "t1.yaml", _TIER1)))
    monkeypatch.setenv("HARNESS_NOW", "2026-06-24")  # past the grace
    d = _plan(tmp_path)
    v = ac.evaluate_test_policy(d, "feature", ["src/x.py"], root=tmp_path)
    # expired grace → full hard gate restored: missing integration HARD-blocks.
    assert v.status == "FAIL"
    assert v.enforcement == "hard"
    assert "integration" in v.reason
