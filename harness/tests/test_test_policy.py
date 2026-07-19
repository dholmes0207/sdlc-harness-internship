"""test_test_policy.py — the two-tier test-policy loader/merger.

Tier-1 (harness/data/test-policy.yaml) ships the default DoD; tier-2
(<repo-root>/test-policy.yaml, OUTSIDE harness/**) is the repo override. Merge
contract:
  - a change-class ONLY in tier-2 is added (union, ADD-only).
  - enforcement/required for a class in BOTH: tier-2 wins WHEN it strengthens
    (or matches) the gate.
  - WEAKENING the gate (hard→soft, dropping a required type) is refused unless
    wrapped in `grace` carrying a non-empty `reason`; an honored grace emits a
    policy_grace trace (git-visible backdoor, never silent).
  - schema_version MAJOR mismatch between tiers → raise (fail-loud).
  - tier-2 absent → clean tier-1 fallback, no raise.
Round-trip every asserted field at a NON-DEFAULT value so a silent
read-only/write-only break cannot pass.
"""
import json
import pytest
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import test_policy as tp  # noqa: E402

_TIER1 = """\
schema_version: "1.0"
preset: pyramid
change_classes:
  feature: { required: [unit, integration], coverage: {line: 80} }
  bugfix:  { required: [unit, regression] }
  refactor: { required: [unit], enforcement: soft }
test_types:
  unit:        { format: junit }
  integration: { format: junit }
  regression:  { format: junit }
  coverage:    { format: cobertura }
components:
  - { path: "**", enforcement: hard }
"""


def _write(p: Path, body: str) -> Path:
    p.write_text(body, encoding="utf-8")
    return p


def test_loads_tier1_only_when_no_override(tmp_path):
    t1 = _write(tmp_path / "test-policy.yaml", _TIER1)
    pol = tp.load_test_policy(tier1_path=t1, tier2_path=None, trace=False)
    assert pol["preset"] == "pyramid"
    assert pol["change_classes"]["feature"]["required"] == ["unit", "integration"]
    assert pol["change_classes"]["feature"]["coverage"]["line"] == 80


def test_tier2_new_class_is_union_added(tmp_path):
    t1 = _write(tmp_path / "t1.yaml", _TIER1)
    t2 = _write(tmp_path / "t2.yaml", (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  hotfix: { required: [unit, smoke], enforcement: hard }\n"))
    pol = tp.load_test_policy(tier1_path=t1, tier2_path=t2, trace=False)
    # new class present, tier-1 classes untouched
    assert pol["change_classes"]["hotfix"]["required"] == ["unit", "smoke"]
    assert pol["change_classes"]["feature"]["required"] == ["unit", "integration"]


def test_tier2_wins_when_it_strengthens(tmp_path):
    t1 = _write(tmp_path / "t1.yaml", _TIER1)
    # refactor ships enforcement: soft; tier-2 RAISES it to hard (strengthen) +
    # adds a required type — allowed directly, no grace needed.
    t2 = _write(tmp_path / "t2.yaml", (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  refactor: { required: [unit, integration], enforcement: hard }\n"))
    pol = tp.load_test_policy(tier1_path=t1, tier2_path=t2, trace=False)
    assert pol["change_classes"]["refactor"]["enforcement"] == "hard"
    assert pol["change_classes"]["refactor"]["required"] == ["unit", "integration"]


def test_weakening_without_grace_is_refused(tmp_path):
    t1 = _write(tmp_path / "t1.yaml", _TIER1)
    # feature ships hard (default) with [unit, integration]; tier-2 drops to soft
    # and removes integration WITHOUT grace → must raise.
    t2 = _write(tmp_path / "t2.yaml", (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  feature: { required: [unit], enforcement: soft }\n"))
    with pytest.raises(tp.TestPolicyError, match=r"(?i)grace"):
        tp.load_test_policy(tier1_path=t1, tier2_path=t2, trace=False)


def test_weakening_with_grace_reason_is_honored_and_traced(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    t1 = _write(tmp_path / "t1.yaml", _TIER1)
    t2 = _write(tmp_path / "t2.yaml", (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  feature:\n"
        "    required: [unit]\n"
        "    enforcement: soft\n"
        "    grace: { reason: 'legacy module, migration in progress', expires: '2099-01-01' }\n"))
    pol = tp.load_test_policy(tier1_path=t1, tier2_path=t2, trace=True)
    assert pol["change_classes"]["feature"]["enforcement"] == "soft"
    assert pol["change_classes"]["feature"]["grace"]["reason"]
    traces = list((tmp_path / "trace").glob("trace-*.jsonl"))
    assert traces, "an honored grace must emit a policy_grace trace"
    events = [json.loads(ln) for ln in traces[0].read_text().splitlines()]
    assert any(e.get("event") == "policy_grace" for e in events)


def test_grace_without_reason_is_refused(tmp_path):
    t1 = _write(tmp_path / "t1.yaml", _TIER1)
    t2 = _write(tmp_path / "t2.yaml", (
        'schema_version: "1.0"\n'
        "change_classes:\n"
        "  feature: { required: [unit], enforcement: soft, grace: {} }\n"))
    try:
        tp.load_test_policy(tier1_path=t1, tier2_path=t2, trace=False)
    except tp.TestPolicyError as e:
        assert "reason" in str(e).lower()
    else:
        raise AssertionError("grace without a reason must raise")


def test_schema_major_mismatch_raises(tmp_path):
    t1 = _write(tmp_path / "t1.yaml", _TIER1)
    t2 = _write(tmp_path / "t2.yaml", (
        'schema_version: "2.0"\n'
        "change_classes:\n"
        "  hotfix: { required: [unit] }\n"))
    try:
        tp.load_test_policy(tier1_path=t1, tier2_path=t2, trace=False)
    except tp.TestPolicyError as e:
        assert "schema" in str(e).lower()
    else:
        raise AssertionError("a major schema_version mismatch must raise")


def test_missing_tier1_raises(tmp_path):
    # tier-1 is the spine — a missing/unreadable tier-1 must fail loud, never
    # default-to-empty (a gate with no policy must not silently pass).
    try:
        tp.load_test_policy(tier1_path=tmp_path / "nope.yaml", tier2_path=None,
                           trace=False)
    except tp.TestPolicyError:
        pass
    else:
        raise AssertionError("a missing tier-1 policy must raise")


def test_resolve_for_class_returns_required_and_enforcement(tmp_path):
    t1 = _write(tmp_path / "t1.yaml", _TIER1)
    pol = tp.load_test_policy(tier1_path=t1, tier2_path=None, trace=False)
    spec = tp.resolve_for_class(pol, "bugfix")
    assert spec["required"] == ["unit", "regression"]
    # unset enforcement defaults to hard (hard-default posture)
    assert spec["enforcement"] == "hard"


def test_shipped_tier1_default_loads():
    # the real shipped default must parse + carry the documented classes.
    pol = tp.load_test_policy(trace=False)
    assert "feature" in pol["change_classes"]
    assert pol["schema_version"].split(".")[0] == "1"
