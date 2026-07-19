#!/usr/bin/env python3
"""P4 — promote NO-CLAUDE-RUNTIME to a layer-b floor (and keep HOOK-CLASS advisory).

NO-CLAUDE-RUNTIME is a working grep detector proven zero-false-positive on the
real harness tree, so it graduates to a floor (severity critical, floor true) in
the layer-b folder. To allow that, an override may now declare `floor`. HOOK-CLASS
stays advisory: the grep engine is line-scan fire-on-match, so it cannot express
"a hook is MISSING the HOOK_CLASS constant" (an absence check) without new engine
work — that floor is deferred to the BACKLOG.
"""

import sys
import pytest
from pathlib import Path

import yaml as _yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import rule_view  # noqa: E402
import user_override  # noqa: E402

_FOLDER_FILE = _REPO_ROOT / "docs" / "standards" / "harness-self.std.yaml"


def _folder_by_id():
    data = _yaml.safe_load(_FOLDER_FILE.read_text(encoding="utf-8"))
    return {o["rule_id"]: o for o in data["overrides"]}


# (a) an override may declare floor:true -> the new rule is a floor.
def test_override_can_declare_floor():
    out, warns = user_override.apply([], [
        {"rule_id": "X", "reason": "lock", "floor": True, "severity": "critical",
         "scope": ["**/*.py"]}])
    x = next(r for r in out if r["id"] == "X")
    assert x["floor"] is True and x["severity"] == "critical"
    assert not any("REJECTED" in w or "REFUSED" in w for w in warns)


# (b) the new-id floor-shadow refusal protects only BASE (shipped) floors — a
# repo's own layer-b rules coexist with its own declared floor (same author, not
# a dodge), so a sibling info rule on an overlapping scope is ADDED, not refused.
def test_layerb_floor_does_not_refuse_sibling():
    out, warns = user_override.apply([], [
        {"rule_id": "FLOOR", "reason": "lock", "floor": True, "severity": "critical",
         "scope": ["**/*.py"]},
        {"rule_id": "SIBLING", "reason": "different concern", "severity": "info",
         "scope": ["**/*.py"]}])
    ids = {r["id"] for r in out}
    assert {"FLOOR", "SIBLING"} <= ids            # both coexist
    assert not any("REJECTED" in w or "REFUSED" in w for w in warns)


# a SHIPPED (base) floor still refuses a new-id weaker shadow (the original
# protection, unchanged): a downstream repo cannot dodge a harness floor.
def test_base_floor_still_refuses_shadow():
    base = [{"id": "SHIPPED-FLOOR", "type": "rule", "scope": ["**/*.py"],
             "severity": "critical", "floor": True, "enabled": True,
             "relates_to_std": []}]
    out, warns = user_override.apply(base, [
        {"rule_id": "DODGE", "reason": "dodge", "severity": "info",
         "scope": ["**/*.py"]}])
    assert "DODGE" not in {r["id"] for r in out}
    assert any("DODGE" in w and "REJECTED" in w for w in warns)


# (c) NO-CLAUDE-RUNTIME is promoted in the folder; HOOK-CLASS stays advisory.
@pytest.mark.dev_repo
def test_folder_promotion_state():
    by_id = _folder_by_id()
    nc = by_id["USR-HARNESS-NO-CLAUDE-RUNTIME"]
    assert nc.get("floor") is True and nc.get("severity") == "critical"
    assert isinstance(nc.get("detector"), dict)   # still the grep detector
    hc = by_id["USR-HARNESS-HOOK-CLASS"]
    assert hc.get("severity", "info") == "info" and not hc.get("floor")
    assert hc.get("detector") is None             # absence-check deferred (BACKLOG)


# (d) end to end: the promoted floor materializes through load on the real repo.
@pytest.mark.dev_repo
def test_no_claude_floor_applies_end_to_end():
    out = rule_view.load_rules_from_tree(_REPO_ROOT, ["harness/scripts/x.py"])
    rule = next((r for r in out["rules"]
                 if r.get("id") == "USR-HARNESS-NO-CLAUDE-RUNTIME"), None)
    assert rule is not None and rule.get("floor") is True
    assert rule.get("severity") == "critical"


# (e) the promoted floor cannot be weakened by an override-by-id (reuses the
# existing floor-refusal path on a base set that already holds the floor).
def test_promoted_floor_override_by_id_refused():
    base = [{"id": "USR-HARNESS-NO-CLAUDE-RUNTIME", "type": "rule",
             "scope": ["harness/**/*.py"], "severity": "critical", "floor": True,
             "enabled": True, "relates_to_std": []}]
    out, warns = user_override.apply(base, [
        {"rule_id": "USR-HARNESS-NO-CLAUDE-RUNTIME", "reason": "relax",
         "severity": "info"}])
    rule = next(r for r in out if r["id"] == "USR-HARNESS-NO-CLAUDE-RUNTIME")
    assert rule["severity"] == "critical"          # weakening refused
    assert any("floor" in w.lower() and "REJECTED" in w for w in warns)
