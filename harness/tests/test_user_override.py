"""Tests for user_override — the standards.user.yaml override layer (P6).

A repo may override an operational rule by id (change severity/enabled/scope) —
always loud, always requiring a reason. A floor rule is non-overridable: an
override by id is refused, and a new-id user rule that scope-overlaps a floor
rule with a weaker posture is refused too (no floor-shadow). Conflict-detect
warns on a real scope overlap with opposite severity, and does NOT warn on
disjoint scopes (using the P0 glob-intersection predicates).
"""

import sys
from pathlib import Path

import yaml as _yaml

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import user_override  # noqa: E402
import rule_view  # noqa: E402


def _rule(rid, scope, severity="info", floor=False, enabled=True):
    return {"id": rid, "type": "rule", "scope": scope, "severity": severity,
            "floor": floor, "enabled": enabled, "relates_to_std": []}


_OP = """id: STD-REVIEW-PY
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


# --- override by id ----------------------------------------------------------

def test_user_override_applies():
    rules = [_rule("R1", ["**/*.py"], "critical")]
    out, warns = user_override.apply(rules, [
        {"rule_id": "R1", "reason": "too strict in this repo", "severity": "info"}])
    r1 = next(r for r in out if r["id"] == "R1")
    assert r1["severity"] == "info"
    assert r1.get("_user_override_reason") == "too strict in this repo"
    assert any("overrid" in w.lower() for w in warns)


def test_non_string_rule_id_skipped_not_raised():
    # a non-string (unhashable) rule_id is skipped, never raises into the consumer
    rules = [_rule("R1", ["**/*.py"])]
    out, warns = user_override.apply(rules, [{"rule_id": ["a", "b"], "reason": "x"}])
    assert out == rules
    assert any("non-string rule_id" in w for w in warns)


def test_override_requires_reason():
    rules = [_rule("R1", ["**/*.py"])]
    out, warns = user_override.apply(rules, [{"rule_id": "R1", "severity": "critical"}])
    assert next(r for r in out if r["id"] == "R1")["severity"] == "info"   # not applied
    assert any("reason" in w.lower() for w in warns)


# --- floor refuse [F3] -------------------------------------------------------

def test_floor_override_refused():
    rules = [_rule("F1", ["**/*.py"], "critical", floor=True)]
    out, warns = user_override.apply(rules, [
        {"rule_id": "F1", "reason": "please relax", "severity": "info"}])
    assert next(r for r in out if r["id"] == "F1")["severity"] == "critical"   # unchanged
    assert any("floor" in w.lower() and ("reject" in w.lower() or "refus" in w.lower())
               for w in warns)


def test_floor_enabled_false_refused():
    rules = [_rule("F1", ["**/*.py"], "critical", floor=True)]
    out, _ = user_override.apply(rules, [
        {"rule_id": "F1", "reason": "off", "enabled": False}])
    assert next(r for r in out if r["id"] == "F1")["enabled"] is True   # refused


def test_floor_shadow_by_new_id_refused():
    rules = [_rule("F1", ["**/*.py"], "critical", floor=True)]
    out, warns = user_override.apply(rules, [
        {"rule_id": "USER-X", "reason": "sneaky", "scope": ["src/**/*.py"],
         "severity": "info"}])
    assert all(r["id"] != "USER-X" for r in out)        # new shadow rule not added
    assert any("floor" in w.lower() and ("reject" in w.lower() or "refus" in w.lower())
               for w in warns)


def test_floor_shadow_omitted_severity_refused():
    # a new-id rule with NO severity overlapping a floor rule defaults to the
    # weaker posture (info) → refused (pins the _weakens default)
    rules = [_rule("F1", ["**/*.py"], "critical", floor=True)]
    out, warns = user_override.apply(rules, [
        {"rule_id": "USER-Y", "reason": "x", "scope": ["src/**/*.py"]}])  # no severity
    assert all(r["id"] != "USER-Y" for r in out)
    assert any("floor" in w.lower() for w in warns)


def test_floor_shadow_equal_severity_added():
    # a new-id rule at the SAME (not weaker) severity overlapping a floor is NOT a
    # shadow → added (pins the < vs <= boundary in _weakens)
    rules = [_rule("F1", ["**/*.py"], "critical", floor=True)]
    out, _ = user_override.apply(rules, [
        {"rule_id": "USER-CRIT", "reason": "x", "scope": ["src/**/*.py"],
         "severity": "critical"}])
    added = next((r for r in out if r["id"] == "USER-CRIT"), None)
    assert added is not None
    assert added["severity"] == "critical"   # added at its declared (non-weaker) severity


def test_non_shadow_new_rule_added():
    # a new-id rule with a disjoint scope (not shadowing the floor) is added
    rules = [_rule("F1", ["**/*.py"], "critical", floor=True)]
    out, _ = user_override.apply(rules, [
        {"rule_id": "USER-GO", "reason": "go rule", "scope": ["**/*.go"],
         "severity": "info"}])
    assert any(r["id"] == "USER-GO" for r in out)


# --- conflict-detect (advisory) ----------------------------------------------

def test_conflict_detect_real():
    user = [_rule("U1", ["**/*.py"], "info")]
    std = [_rule("S1", ["src/**/*.py"], "critical")]
    assert user_override.detect_conflicts(user, std)


def test_conflict_detect_false_positive_avoided():
    user = [_rule("U1", ["**/*.py"], "info")]
    std = [_rule("S1", ["**/*.go"], "critical")]
    assert user_override.detect_conflicts(user, std) == []


def test_conflict_detect_missing_severity_not_flagged():
    # a std rule with no severity is not an "opposite severity" conflict
    user = [_rule("U1", ["**/*.py"], "info")]
    std = [{"id": "S1", "type": "rule", "scope": ["**/*.py"], "floor": False,
            "enabled": True, "relates_to_std": []}]   # no severity key
    assert user_override.detect_conflicts(user, std) == []


def test_conflict_detect_detail_names_match_route():
    # matched via shared relates_to_std with DISJOINT scopes → detail says so
    user = [{"id": "U1", "type": "rule", "scope": ["**/*.go"], "severity": "info",
             "floor": False, "enabled": True, "relates_to_std": ["S1"]}]
    std = [_rule("S1", ["**/*.py"], "critical")]
    findings = user_override.detect_conflicts(user, std)
    assert findings and "relates_to_std" in findings[0]["detail"]
    assert "scope overlaps" not in findings[0]["detail"]


# --- consumer integration ----------------------------------------------------

def test_consumer_applies_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_USER_OVERRIDE", str(tmp_path / "ov.yaml"))
    areas = tmp_path / "harness" / "standards" / "areas"
    areas.mkdir(parents=True)
    (areas / "STD-REVIEW-PY.std.yaml").write_text(_OP, encoding="utf-8")
    (tmp_path / "ov.yaml").write_text(_yaml.safe_dump({"overrides": [
        {"rule_id": "STD-REVIEW-PY-RG1-R1", "reason": "n/a here", "enabled": False}]}),
        encoding="utf-8")
    out = rule_view.load_rules_from_tree(tmp_path, ["a.py"])
    assert "STD-REVIEW-PY-RG1-R1" not in out["rules_applied"]   # disabled by override
    assert "STD-REVIEW-PY-RG1-R2" in out["rules_applied"]       # floor stays


def test_consumer_floor_override_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_USER_OVERRIDE", str(tmp_path / "ov.yaml"))
    areas = tmp_path / "harness" / "standards" / "areas"
    areas.mkdir(parents=True)
    (areas / "STD-REVIEW-PY.std.yaml").write_text(_OP, encoding="utf-8")
    (tmp_path / "ov.yaml").write_text(_yaml.safe_dump({"overrides": [
        {"rule_id": "STD-REVIEW-PY-RG1-R2", "reason": "relax", "enabled": False}]}),
        encoding="utf-8")
    out = rule_view.load_rules_from_tree(tmp_path, ["a.py"])
    assert "STD-REVIEW-PY-RG1-R2" in out["rules_applied"]   # floor disable refused


# --- P0: folder-based override loading (layer-b) -----------------------------

def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_load_reads_folder_merges_overrides(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    _write(tmp_path / "docs" / "standards" / "a.std.yaml",
           _yaml.safe_dump({"overrides": [{"rule_id": "A", "reason": "x"}]}))
    _write(tmp_path / "docs" / "standards" / "b.std.yaml",
           _yaml.safe_dump({"overrides": [{"rule_id": "B", "reason": "y"}]}))
    ov = user_override.load(tmp_path)
    assert {o["rule_id"] for o in ov} == {"A", "B"}


def test_load_empty_folder_failsoft(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    (tmp_path / "docs" / "standards").mkdir(parents=True)
    assert user_override.load(tmp_path) == []


def test_load_env_single_file_backcompat(tmp_path, monkeypatch):
    f = tmp_path / "ov.yaml"
    f.write_text(_yaml.safe_dump({"overrides": [{"rule_id": "E", "reason": "z"}]}),
                 encoding="utf-8")
    monkeypatch.setenv("HARNESS_USER_OVERRIDE", str(f))
    assert [o["rule_id"] for o in user_override.load(tmp_path)] == ["E"]


def test_knob_changes_default_dir(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    _write(tmp_path / "harness" / "data" / "standards.yaml",
           _yaml.safe_dump({"user_rules_dir": "rules.d/"}))
    _write(tmp_path / "rules.d" / "x.std.yaml",
           _yaml.safe_dump({"overrides": [{"rule_id": "K", "reason": "k"}]}))
    assert [o["rule_id"] for o in user_override.load(tmp_path)] == ["K"]


def test_legacy_root_fallback_when_folder_absent(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    (tmp_path / "standards.user.yaml").write_text(
        _yaml.safe_dump({"overrides": [{"rule_id": "L", "reason": "legacy"}]}),
        encoding="utf-8")
    assert [o["rule_id"] for o in user_override.load(tmp_path)] == ["L"]


def test_folder_wins_over_legacy_root(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    (tmp_path / "standards.user.yaml").write_text(
        _yaml.safe_dump({"overrides": [{"rule_id": "L", "reason": "legacy"}]}),
        encoding="utf-8")
    _write(tmp_path / "docs" / "standards" / "a.std.yaml",
           _yaml.safe_dump({"overrides": [{"rule_id": "F", "reason": "folder"}]}))
    # folder is populated -> it wins; legacy root is ignored, not merged
    assert [o["rule_id"] for o in user_override.load(tmp_path)] == ["F"]


def test_malformed_file_in_folder_isolated(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_USER_OVERRIDE", raising=False)
    _write(tmp_path / "docs" / "standards" / "good.std.yaml",
           _yaml.safe_dump({"overrides": [{"rule_id": "G", "reason": "ok"}]}))
    _write(tmp_path / "docs" / "standards" / "bad.std.yaml", "overrides: [::: not yaml")
    # the malformed neighbour applies no override but does not break the good file
    assert [o["rule_id"] for o in user_override.load(tmp_path)] == ["G"]
