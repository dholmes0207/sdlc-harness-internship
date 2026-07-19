"""Tests for rule_view — the review-time consumer of the std operational zone.

rule_view reads the standards tree (P1), selects the operational rule-leaves
whose scope intersects the changed files (P0 scope_match), derives lang from
scope, and emits a rule-scan.json honouring the artifact-rule-scan.json
byte-contract the gate validates. It is the unified replacement for the flat
review_rules.load_rules reader; producers dual-read during the transition.
"""

import json
import pytest
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import rule_view  # noqa: E402
import artifact_check  # noqa: E402


# --- std operational tree fixture ---------------------------------------------

_OP_PY = """id: STD-REVIEW-PY
type: std_area
zone: operational
title: "Python Review"
rule_groups:
  - id: STD-REVIEW-PY-RG1
    title: "PY"
    rules:
      - id: STD-REVIEW-PY-RG1-R1
        title: "py r1"
        scope: ["**/*.py"]
        severity: critical
      - id: STD-REVIEW-PY-RG1-R2
        title: "py r2 (disabled)"
        scope: ["**/*.py"]
        severity: info
        enabled: false
"""

_OP_GO = """id: STD-REVIEW-GO
type: std_area
zone: operational
title: "Go Review"
rule_groups:
  - id: STD-REVIEW-GO-RG1
    title: "GO"
    rules:
      - id: STD-REVIEW-GO-RG1-R1
        title: "go r1"
        scope: ["**/*.go"]
        severity: info
"""


def _std_tree(root):
    areas = root / "harness" / "standards" / "areas"
    areas.mkdir(parents=True, exist_ok=True)
    (areas / "STD-REVIEW-PY.std.yaml").write_text(_OP_PY, encoding="utf-8")
    (areas / "STD-REVIEW-GO.std.yaml").write_text(_OP_GO, encoding="utf-8")
    return root


_EXPECTED = {
    ("src/a.py",): {"STD-REVIEW-PY-RG1-R1"},          # R2 is disabled
    ("pkg/b.go",): {"STD-REVIEW-GO-RG1-R1"},
    ("src/a.py", "pkg/b.go"): {"STD-REVIEW-PY-RG1-R1", "STD-REVIEW-GO-RG1-R1"},
    ("docs/x.md",): set(),
}


# --- Tests Before: lock the gate (a consumer-made rule-scan passes it) --------

def test_rule_scan_contract_fixture(tmp_path):
    _std_tree(tmp_path)
    scan = rule_view.build_rule_scan(
        tmp_path, ["src/a.py"],
        violations=[{"rule_id": "STD-REVIEW-PY-RG1-R1", "severity": "critical",
                     "file": "src/a.py", "line": 3, "finding": "bad"}])
    assert scan["verdict"] == "BLOCKED"   # critical -> BLOCKED so the gate accepts
    plan_dir = tmp_path / "plan"
    (plan_dir / "artifacts").mkdir(parents=True)
    (plan_dir / "artifacts" / "rule-scan.json").write_text(
        json.dumps(scan), encoding="utf-8")
    # the existing gate consistency check finds no contradiction (returns None)
    assert artifact_check._rule_scan_consistency(plan_dir) is None


# --- Tests After: consumer behaviour -----------------------------------------

def test_tree_consumer_applicable(tmp_path):
    _std_tree(tmp_path)
    for diff, expected in _EXPECTED.items():
        out = rule_view.load_rules_from_tree(tmp_path, list(diff))
        assert set(out["rules_applied"]) == expected, (diff, out["rules_applied"])


def test_lang_derived_from_scope(tmp_path):
    _std_tree(tmp_path)
    out = rule_view.load_rules_from_tree(tmp_path, ["src/a.py"])
    assert "STD-REVIEW-PY-RG1-R1" in out["rules_applied"]
    assert "STD-REVIEW-PY-RG1-R2" not in out["rules_applied"]   # disabled dropped
    assert "python" in out["langs"]                              # lang derived, no field


def test_rule_scan_schema_valid(tmp_path):
    _std_tree(tmp_path)
    scan = rule_view.build_rule_scan(tmp_path, ["src/a.py"])
    for f in ("rules_applied", "violations", "verdict", "reviewer", "ts"):
        assert f in scan, f
    assert scan["verdict"] in ("PASS", "PASS_WITH_RISK", "BLOCKED")
    assert isinstance(scan["violations"], list)
    for v in scan["violations"]:
        assert v["severity"] in ("critical", "info")
    assert isinstance(scan["reviewer"], str) and scan["reviewer"]
    assert isinstance(scan["ts"], str) and scan["ts"]


def test_verdict_derives_from_violations(tmp_path):
    _std_tree(tmp_path)
    crit = rule_view.build_rule_scan(
        tmp_path, ["src/a.py"],
        violations=[{"rule_id": "X", "severity": "critical",
                     "file": "a.py", "line": 1, "finding": "x"}])
    assert crit["verdict"] == "BLOCKED"
    info = rule_view.build_rule_scan(
        tmp_path, ["src/a.py"],
        violations=[{"rule_id": "X", "severity": "info",
                     "file": "a.py", "line": 1, "finding": "x"}])
    assert info["verdict"] == "PASS_WITH_RISK"
    clean = rule_view.build_rule_scan(tmp_path, ["src/a.py"])
    assert clean["verdict"] == "PASS"
    # an off-enum/missing severity does NOT inflate to PASS_WITH_RISK
    malformed = rule_view.build_rule_scan(
        tmp_path, ["src/a.py"],
        violations=[{"rule_id": "X", "file": "a.py", "line": 1, "finding": "x"}])
    assert malformed["verdict"] == "PASS"


def test_consumer_failsoft_empty(tmp_path, monkeypatch):
    # the tree is the single source now; if the consumer raises, load_rules_dual
    # returns a safe-empty result tagged 'tree-error' so a review never breaks.
    _std_tree(tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("tree consumer exploded")

    monkeypatch.setattr(rule_view, "load_rules", _boom)
    result, source = rule_view.load_rules_dual(tmp_path, ["src/a.py"])
    assert source == "tree-error"
    assert result["rules_applied"] == []


# --- P0: faceted single-door loader ------------------------------------------

def test_load_rules_parity_with_from_tree(tmp_path):
    _std_tree(tmp_path)
    for diff in (["src/a.py"], ["pkg/b.go"], ["src/a.py", "pkg/b.go"], ["docs/x.md"]):
        a = rule_view.load_rules(tmp_path, scope_intersects=diff)
        b = rule_view.load_rules_from_tree(tmp_path, diff)
        assert a["rules_applied"] == b["rules_applied"], diff
        assert a["langs"] == b["langs"], diff
        assert a["override_warnings"] == b["override_warnings"], diff
        assert [r["id"] for r in a["rules"]] == [r["id"] for r in b["rules"]], diff


def test_load_rules_dual_still_failsoft(tmp_path, monkeypatch):
    _std_tree(tmp_path)

    def _boom(*a, **k):
        raise RuntimeError("x")

    monkeypatch.setattr(rule_view, "load_rules", _boom)
    result, source = rule_view.load_rules_dual(tmp_path, ["src/a.py"])
    assert source == "tree-error"
    assert result["rules_applied"] == []
    assert set(result) == {"rules", "rules_applied", "langs",
                           "override_warnings", "conflicts"}


def test_load_rules_facets(tmp_path):
    _std_tree(tmp_path)
    # floor facet: fixture has no floor rule -> floor=True yields none
    assert rule_view.load_rules(tmp_path, floor=True)["rules_applied"] == []
    # severity facet: PY R1 is critical, GO R1 is info
    crit = rule_view.load_rules(tmp_path, severity="critical")["rules_applied"]
    assert crit == ["STD-REVIEW-PY-RG1-R1"]
    # types facet: a non-rule node type yields no rule-leaves
    assert rule_view.load_rules(tmp_path, types=("std_area",))["rules_applied"] == []
    # zone facet: a zone with no areas yields none
    assert rule_view.load_rules(tmp_path, zone="charter")["rules_applied"] == []


def test_load_rules_none_scope_returns_full_operational(tmp_path, monkeypatch):
    _std_tree(tmp_path)
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    full = rule_view.load_rules(tmp_path, scope_intersects=None)["rules_applied"]
    assert set(full) == {"STD-REVIEW-PY-RG1-R1", "STD-REVIEW-GO-RG1-R1"}  # R2 disabled
    # the disabled R2 is in the pre-override set, so an override can re-enable it
    ovf = tmp_path / "ov.yaml"
    ovf.write_text(json.dumps({"overrides": [
        {"rule_id": "STD-REVIEW-PY-RG1-R2", "reason": "re-enable", "enabled": True}]}),
        encoding="utf-8")
    monkeypatch.setenv("HARNESS_USER_OVERRIDE", str(ovf))
    full2 = rule_view.load_rules(tmp_path, scope_intersects=None)["rules_applied"]
    assert "STD-REVIEW-PY-RG1-R2" in full2


# --- P1: conflict surfacing (review path) is gate-hot-path-safe ---------------

def _repo_with_conflict(root, monkeypatch):
    """Shipped critical PY rule + a layer-b new rule of opposite severity on the
    same scope -> detect_conflicts should flag it."""
    monkeypatch.setenv("HARNESS_ROOT", str(root))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(root / "harness" / "state"))
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    _std_tree(root)
    folder = root / "docs" / "standards"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "harness-self.std.yaml").write_text(
        json.dumps({"overrides": [{"rule_id": "USR-X", "reason": "d",
                                   "severity": "info", "scope": ["**/*.py"]}]}),
        encoding="utf-8")


def test_dual_surfaces_conflicts(tmp_path, monkeypatch):
    _repo_with_conflict(tmp_path, monkeypatch)
    result, source = rule_view.load_rules_dual(tmp_path, ["src/a.py"])
    assert source == "tree"
    assert result["conflicts"]   # opposite-severity overlap surfaced for review


def test_disjoint_scope_no_conflict(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "harness" / "state"))
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    _std_tree(tmp_path)
    folder = tmp_path / "docs" / "standards"
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "harness-self.std.yaml").write_text(
        json.dumps({"overrides": [{"rule_id": "USR-X", "reason": "d",
                                   "severity": "info", "scope": ["**/*.rb"]}]}),
        encoding="utf-8")
    result, _ = rule_view.load_rules_dual(tmp_path, ["src/a.py"])
    assert result["conflicts"] == []


def test_gate_hot_path_never_audits_conflicts(tmp_path, monkeypatch):
    # the gate hot-path (artifact_check._coverage_check) loads via
    # load_rules_from_tree, which must NOT trigger the conflict audit [F7].
    _repo_with_conflict(tmp_path, monkeypatch)
    import user_override
    calls = {"n": 0}
    real = user_override.detect_conflicts
    monkeypatch.setattr(user_override, "detect_conflicts",
                        lambda *a, **k: calls.__setitem__("n", calls["n"] + 1) or real(*a, **k))
    out = rule_view.load_rules_from_tree(tmp_path, ["src/a.py"])
    assert "conflicts" not in out          # from_tree stays conflict-free
    assert calls["n"] == 0                 # the audit was never invoked on this path


# --- layer-b harness-self rules (docs/standards/harness-self.std.yaml) --------
# These assert the REAL layer-b file's new rules load through load_rules_dual and
# select on their scopes (the harness dogfoods its own architecture/standards
# invariants). Root = the real repo so the actual std tree + override folder apply.

_REPO = Path(__file__).resolve().parent.parent.parent


@pytest.mark.dev_repo
def test_sad_freshness_rule_selected():
    result, _src = rule_view.load_rules_dual(_REPO, ["harness/hooks/x.py"])
    assert "USR-HARNESS-SAD-FRESHNESS" in result["rules_applied"]


def test_sad_freshness_rule_disjoint():
    result, _src = rule_view.load_rules_dual(_REPO, ["README.md"])
    assert "USR-HARNESS-SAD-FRESHNESS" not in result["rules_applied"]


@pytest.mark.dev_repo
def test_promoted_lessons_load():
    result, _src = rule_view.load_rules_dual(_REPO, ["harness/scripts/foo.py"])
    ids = set(result["rules_applied"])
    assert "USR-HARNESS-CONFIG-FIELD-ROUNDTRIP" in ids
    assert "USR-HARNESS-PRODUCER-CONSUMER-WIRING" in ids
    assert "USR-HARNESS-LAYER-BOUNDARY" in ids


def test_layer_boundary_disjoint_on_non_py():
    result, _src = rule_view.load_rules_dual(_REPO, ["docs/x.md"])
    assert "USR-HARNESS-LAYER-BOUNDARY" not in result["rules_applied"]
