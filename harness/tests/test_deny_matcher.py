"""deny_matcher.evaluate: containment -> core short-circuit -> hard-binary
deny+carve -> soft ordered last-match-wins. Pure + deterministic + symlink-safe."""
import os
from pathlib import Path

import pytest

import deny_matcher as dm
import write_deny_policy as wdp


def _policy(soft_rules=()):
    return wdp.assemble_policy(list(soft_rules))


def _ev(root, target, soft_rules=()):
    return dm.evaluate(target, _policy(soft_rules), [str(root)])


# --- core-immune short-circuit (VL-2: no allow reaches core) ---

def test_core_immune_beats_soft_allow(tmp_path):
    d = _ev(tmp_path, tmp_path / "harness/hooks/agent_rbac_guard.py",
            [("allow", "harness/**")])
    assert d.tier == wdp.TIER_CORE
    assert d.is_hard


def test_core_immune_beats_allow_star(tmp_path):
    d = _ev(tmp_path, tmp_path / "harness/hooks/agent_rbac_guard.py", [("allow", "**")])
    assert d.tier == wdp.TIER_CORE


def test_core_via_guard_list(tmp_path):
    d = _ev(tmp_path, tmp_path / "harness/data/agent-permissions.yaml")
    assert d.tier == wdp.TIER_CORE


def test_git_immune(tmp_path):
    assert _ev(tmp_path, tmp_path / ".git/config").tier == wdp.TIER_CORE


def test_secrets_immune(tmp_path):
    assert _ev(tmp_path, tmp_path / ".env").tier == wdp.TIER_CORE
    assert _ev(tmp_path, tmp_path / "config/.env.prod").tier == wdp.TIER_CORE


def test_env_template_not_immune(tmp_path):
    # narrow secrets: a template is NOT core; with no soft rule it lands ALLOW
    assert _ev(tmp_path, tmp_path / ".env.example").tier == wdp.TIER_ALLOW


def test_claude_disarm_immune(tmp_path):
    d = _ev(tmp_path, tmp_path / ".claude/settings.local.json")
    assert d.tier == wdp.TIER_CORE


# --- hard-binary deny + carve (carve ONLY tests, state NOT carved) ---

def test_hard_binary_deny(tmp_path):
    d = _ev(tmp_path, tmp_path / "harness/plugins/hs/x.py")
    assert d.tier == wdp.TIER_HARD_BINARY
    assert d.is_hard


def test_hard_binary_carve_tests(tmp_path):
    assert _ev(tmp_path, tmp_path / "harness/tests/test_x.py").tier == wdp.TIER_ALLOW


def test_state_not_carved(tmp_path):
    d = _ev(tmp_path, tmp_path / "harness/state/trace/x.jsonl")
    assert d.tier == wdp.TIER_HARD_BINARY


# --- C3: carve membership decided on FINAL resolved realpath ---

def test_carve_membership_on_realpath(tmp_path):
    (tmp_path / "harness" / "hooks").mkdir(parents=True)
    (tmp_path / "harness" / "hooks" / "agent_rbac_guard.py").write_text("x", encoding="utf-8")
    (tmp_path / "harness" / "tests").mkdir(parents=True)
    os.symlink("../hooks", tmp_path / "harness" / "tests" / "sneak")
    d = _ev(tmp_path, tmp_path / "harness/tests/sneak/agent_rbac_guard.py")
    assert d.tier == wdp.TIER_CORE  # resolves to harness/hooks/*.py, not the tests carve


# --- soft ordered last-match-wins ---

def test_soft_deny_hit(tmp_path):
    d = _ev(tmp_path, tmp_path / "docs/notes.md", [("deny", "docs/**")])
    assert d.tier == wdp.TIER_SOFT
    assert d.matched_rule == "docs/**"
    assert not d.is_hard


def test_soft_last_match_allow_wins(tmp_path):
    d = _ev(tmp_path, tmp_path / "docs/keep/a.md",
            [("deny", "docs/**"), ("allow", "docs/keep/**")])
    assert d.tier == wdp.TIER_ALLOW


def test_soft_last_match_deny_wins(tmp_path):
    d = _ev(tmp_path, tmp_path / "docs/secret/a.md",
            [("allow", "docs/**"), ("deny", "docs/secret/**")])
    assert d.tier == wdp.TIER_SOFT
    assert d.matched_rule == "docs/secret/**"


def test_plain_source_allowed(tmp_path):
    assert _ev(tmp_path, tmp_path / "src/app.tsx").tier == wdp.TIER_ALLOW


# --- containment: symlink escape + unresolved ---

def test_symlink_escape_contained(tmp_path):
    outside = tmp_path.parent / "outside_root"
    outside.mkdir(exist_ok=True)
    (outside / "hooks").mkdir(exist_ok=True)
    root = tmp_path / "repo"
    root.mkdir()
    os.symlink(outside, root / "escape")
    d = dm.evaluate(root / "escape" / "hooks" / "x.py", _policy(), [str(root)])
    assert d.tier == dm.TIER_UNRESOLVED  # resolves outside root, not spoofed as core


def test_unresolved_tier(tmp_path):
    other = tmp_path.parent / "elsewhere"
    other.mkdir(exist_ok=True)
    d = dm.evaluate(other / "x.py", _policy(), [str(tmp_path / "repo_root")])
    assert d.tier == dm.TIER_UNRESOLVED
    assert d.matched_rule is None
    assert not d.is_hard


def test_no_protected_roots_unresolved(tmp_path):
    assert dm.evaluate(tmp_path / "x.py", _policy(), []).tier == dm.TIER_UNRESOLVED


# --- purity + determinism ---

def test_evaluate_deterministic(tmp_path):
    target = tmp_path / "docs/secret/a.md"
    rules = [("allow", "docs/**"), ("deny", "docs/secret/**")]
    first = _ev(tmp_path, target, rules)
    for _ in range(100):
        d = _ev(tmp_path, target, rules)
        assert (d.tier, d.matched_rule) == (first.tier, first.matched_rule)


def test_matched_rule_reported_for_core(tmp_path):
    d = _ev(tmp_path, tmp_path / "harness/hooks/x.py")
    assert d.tier == wdp.TIER_CORE
    assert d.matched_rule is not None
