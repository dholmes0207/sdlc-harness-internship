#!/usr/bin/env python3
"""Dogfood: the harness authors its own review rules at <repo>/standards.user.yaml
and becomes the first consumer of the unified standards system.

These tests lock the override layer + grep-detector contract on the repo's own
standards.user.yaml: every entry is well-formed, the user rules apply at review
time for a matching diff, and the grep detectors carry a `# learn:`
negative-lookahead whitelist so they raise ZERO false positives on the real
harness tree.
"""

import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.dev_repo

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import mechanical_runner  # noqa: E402
import rule_view  # noqa: E402
import user_override  # noqa: E402

_USER_YAML = _REPO_ROOT / "docs" / "standards" / "harness-self.std.yaml"


def _load_yaml(path):
    import yaml
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _applied_user_rules():
    """The USR- rules as the override layer materializes them (apply over an
    empty operational set isolates the user-authored additions)."""
    rules, _warn = user_override.apply([], user_override.load(_REPO_ROOT))
    return rules


# 1 — the folder file exists, parses, and every entry is well-formed.
def test_user_yaml_exists_and_every_entry_wellformed():
    assert _USER_YAML.is_file(), "docs/standards/harness-self.std.yaml must exist (dogfood)"
    data = _load_yaml(_USER_YAML)
    assert isinstance(data, dict) and isinstance(data.get("overrides"), list)
    overrides = data["overrides"]
    assert overrides, "at least one dogfood rule expected"
    for ov in overrides:
        rid = ov.get("rule_id")
        assert isinstance(rid, str) and rid.startswith("USR-HARNESS-"), \
            "dogfood rules use the USR-HARNESS- prefix: %r" % rid
        assert isinstance(ov.get("reason"), str) and ov["reason"].strip(), \
            "every override carries a non-empty reason: %r" % rid
        # a floor entry must be critical (the promoted no-claude rule); every
        # other entry stays advisory info.
        if ov.get("floor"):
            assert ov.get("severity") == "critical", \
                "a floor rule is critical: %r" % rid
        else:
            assert ov.get("severity", "info") == "info", \
                "non-floor dogfood rules stay at info: %r" % rid


# 2 — the override layer loads and applies without raising.
def test_override_loads_and_applies_cleanly():
    overrides = user_override.load(_REPO_ROOT)
    assert overrides, "load() reads the dogfood overrides"
    rules, warnings = user_override.apply([], overrides)
    ids = {r.get("id") for r in rules}
    assert any(i.startswith("USR-HARNESS-") for i in ids)
    # adding new user rules is loud but never a refusal here (no floor shadow).
    assert not any("REJECTED" in w or "REFUSED" in w for w in warnings), warnings


# 3 — a USR rule applies at review time for a diff its scope matches.
def test_usr_rule_applies_for_matching_diff():
    res = rule_view.load_rules_from_tree(_REPO_ROOT, ["harness/scripts/foo.py"])
    applied = set(res["rules_applied"])
    assert any(r.startswith("USR-HARNESS-") for r in applied), \
        "a USR rule scoped to harness/**/*.py must apply: %s" % sorted(applied)


def _grep_rules():
    return [r for r in _applied_user_rules()
            if isinstance(r.get("detector"), dict)
            and r["detector"].get("type") == "grep"]


# 4 — the NO-CLAUDE-RUNTIME grep detector: fires on a real ref, skips a
# `# learn:` citation and ordinary prose (negative-lookahead whitelist).
def test_no_claude_runtime_grep_lookahead(tmp_path):
    grep_rules = _grep_rules()
    rule = next((r for r in grep_rules
                 if r.get("id") == "USR-HARNESS-NO-CLAUDE-RUNTIME"), None)
    assert rule is not None, "NO-CLAUDE-RUNTIME must be a grep rule"

    # The banned literal is ASSEMBLED so THIS test file never itself contains
    # it (same discipline as the CI bug-class invariant) — otherwise the
    # real-tree scan in the next test would flag this fixture.
    ref = ".claude/" + "skills/foo"
    target = tmp_path / "harness" / "scripts" / "planted.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        'cfg = "%s"\n' % ref                       # line 1 — banned ref
        + '# learn: %s example\n' % ref            # line 2 — whitelisted
        + 'x = 1  # ordinary code\n',              # line 3 — clean
        encoding="utf-8")

    findings = mechanical_runner.run_grep_detectors(
        [rule], ["harness/scripts/planted.py"], root=str(tmp_path))
    lines = {f["line"] for f in findings}
    assert lines == {1}, "only the un-whitelisted ref fires: %s" % findings


# 5 — F4: every dogfood grep detector raises ZERO findings on the real tree.
def test_dogfood_grep_zero_false_positive_on_real_tree():
    grep_rules = _grep_rules()
    assert grep_rules, "at least one grep dogfood rule expected"
    changed = [str(p.relative_to(_REPO_ROOT))
               for p in (_REPO_ROOT / "harness").rglob("*.py")]
    findings = mechanical_runner.run_grep_detectors(
        grep_rules, changed, root=str(_REPO_ROOT))
    assert findings == [], \
        "dogfood grep rules must be clean on the real harness tree: %s" % findings[:10]


# 6 — F5: the override folder never reddens the std-tree grammar gate (it lives
# under docs/standards/, NOT harness/standards/, so the grammar checker does not
# scan it).
def test_user_yaml_outside_grammar_tree():
    std_tree = _REPO_ROOT / "harness" / "standards"
    assert std_tree not in _USER_YAML.parents
    assert _USER_YAML.parent == _REPO_ROOT / "docs" / "standards"
