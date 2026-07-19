"""Deny-policy SSOT: core-immune derivation, fail-closed soft loader, constants."""
import importlib
import json
from pathlib import Path

import pytest
import yaml

import write_deny_policy as wdp
import write_guard

_REPO = Path(__file__).resolve().parent.parent.parent
_SCHEMA = _REPO / "harness" / "schemas" / "write-deny-policy.json"
_DEFAULT_CFG = _REPO / "harness" / "data" / "write-deny-policy.yaml"
_CLAUDE = ".claude/"  # assembled so this file never carries a banned contiguous ref


# --- CORE_IMMUNE derivation (reuse GUARD_LIST, no second hand-rolled list) ---

def test_core_immune_superset_of_guard_list():
    assert set(write_guard.GUARD_LIST) <= set(wdp.CORE_IMMUNE)


def test_core_immune_reflects_guard_list_import():
    """Prove CORE_IMMUNE derives from write_guard.GUARD_LIST at import, not a copy."""
    probe = "harness/scripts/__reflect_probe__.py"
    orig = write_guard.GUARD_LIST
    try:
        write_guard.GUARD_LIST = orig + (probe,)
        importlib.reload(wdp)
        assert probe in wdp.CORE_IMMUNE
    finally:
        write_guard.GUARD_LIST = orig
        importlib.reload(wdp)


def test_core_immune_covers_claude_disarm():
    for p in (_CLAUDE + "settings.json", _CLAUDE + "settings.local.json", _CLAUDE + "hooks/**"):
        assert p in wdp.CORE_IMMUNE


def test_git_in_core_immune():
    assert ".git/**" in wdp.CORE_IMMUNE


def test_secrets_narrow():
    for present in (".env", ".env.local", ".env.prod", ".env.*.local"):
        assert present in wdp.CORE_IMMUNE
    for absent in (".env.example", ".env.sample", ".env.*"):
        assert absent not in wdp.CORE_IMMUNE


# --- tier + event vocabulary (one vocab, imported by P3/P4/P5) ---

def test_event_constants_present():
    assert wdp.EVENT_HARD_BLOCK == "deny_hard_block"
    assert wdp.EVENT_SOFT_HIT == "deny_soft_hit"
    assert wdp.EVENT_WIDEN == "widen_request"


def test_tier_constants_present():
    tiers = (wdp.TIER_CORE, wdp.TIER_HARD_BINARY, wdp.TIER_SOFT, wdp.TIER_ALLOW)
    assert tiers == ("core_immune", "hard_binary", "soft", "allow")
    assert len(set(tiers)) == 4


def test_polarity_constants_present():
    assert wdp.POLARITY_DENY == "deny"
    assert wdp.POLARITY_ALLOW == "allow"


# --- hard-binary constants (author-maintained, not user-editable) ---

def test_hard_binary_consts():
    assert wdp.HARD_BINARY_DENY == ("harness/**",)
    assert wdp.HARD_BINARY_CARVE == ("harness/tests/**",)


def test_state_not_in_carve():
    assert "harness/state/**" not in wdp.HARD_BINARY_CARVE
    assert not any("state" in c for c in wdp.HARD_BINARY_CARVE)


# --- soft loader: ordered rules, fail-closed on malformed, [] on absent (never brick) ---

def test_load_soft_rules_not_list_raises(tmp_path):
    f = tmp_path / "bad.yaml"
    f.write_text("soft_rules: not-a-list\n", encoding="utf-8")
    with pytest.raises(wdp.DenyPolicyError):
        wdp.load_soft_rules(f)


def test_load_soft_rules_bad_polarity_raises(tmp_path):
    f = tmp_path / "bad2.yaml"
    f.write_text("soft_rules:\n  - block: 'docs/**'\n", encoding="utf-8")
    with pytest.raises(wdp.DenyPolicyError):
        wdp.load_soft_rules(f)


def test_load_soft_rules_multi_key_raises(tmp_path):
    f = tmp_path / "bad3.yaml"
    f.write_text("soft_rules:\n  - {deny: 'a/**', allow: 'b/**'}\n", encoding="utf-8")
    with pytest.raises(wdp.DenyPolicyError):
        wdp.load_soft_rules(f)


def test_load_soft_rules_empty_glob_raises(tmp_path):
    f = tmp_path / "bad4.yaml"
    f.write_text("soft_rules:\n  - deny: ''\n", encoding="utf-8")
    with pytest.raises(wdp.DenyPolicyError):
        wdp.load_soft_rules(f)


def test_load_soft_rules_not_mapping_raises(tmp_path):
    f = tmp_path / "bad5.yaml"
    f.write_text("- just\n- a\n- list\n", encoding="utf-8")
    with pytest.raises(wdp.DenyPolicyError):
        wdp.load_soft_rules(f)


def test_load_soft_rules_absent_returns_empty(tmp_path):
    assert wdp.load_soft_rules(tmp_path / "nope.yaml") == []


def test_load_soft_rules_empty_file(tmp_path):
    f = tmp_path / "e.yaml"
    f.write_text("", encoding="utf-8")
    assert wdp.load_soft_rules(f) == []
    f.write_text("{}\n", encoding="utf-8")
    assert wdp.load_soft_rules(f) == []
    f.write_text("soft_rules: []\n", encoding="utf-8")
    assert wdp.load_soft_rules(f) == []


def test_load_soft_rules_ordered():
    """Cross-order preserved: a deny, then an allow-back, then a re-deny."""
    import tempfile
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8") as fh:
        fh.write("soft_rules:\n  - deny: 'docs/**'\n  - allow: 'docs/keep/**'\n"
                 "  - deny: 'docs/keep/secret/**'\n")
        name = fh.name
    assert wdp.load_soft_rules(name) == [
        ("deny", "docs/**"),
        ("allow", "docs/keep/**"),
        ("deny", "docs/keep/secret/**"),
    ]


# --- assemble_policy: pure, immutable, floor-const + ordered soft-rules ---

def test_assemble_policy_shape():
    pol = wdp.assemble_policy([("deny", "docs/**"), ("allow", "docs/keep/**")])
    assert set(write_guard.GUARD_LIST) <= set(pol.core_immune)
    assert pol.hard_binary_deny == ("harness/**",)
    assert pol.hard_binary_carve == ("harness/tests/**",)
    assert pol.soft_rules == (("deny", "docs/**"), ("allow", "docs/keep/**"))


def test_assemble_policy_default_empty():
    pol = wdp.assemble_policy([])
    assert pol.soft_rules == ()


def test_assemble_policy_immutable():
    pol = wdp.assemble_policy([])
    with pytest.raises((AttributeError, TypeError)):
        pol.soft_rules = (("deny", "x"),)


# --- CI-ban discipline + schema ---

def test_no_banned_claude_hooks_literal():
    banned = _CLAUDE + "hooks/"
    src = (_REPO / "harness" / "scripts" / "write_deny_policy.py").read_text(encoding="utf-8")
    assert banned not in src


def test_schema_validates_default_config():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    cfg = yaml.safe_load(_DEFAULT_CFG.read_text(encoding="utf-8")) or {}
    jsonschema.validate(cfg, schema)


def test_schema_validates_ordered_rules():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate({"soft_rules": [{"deny": "docs/**"}, {"allow": "docs/keep/**"}]}, schema)


def test_schema_rejects_non_list():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"soft_rules": "x"}, schema)


def test_schema_rejects_multi_key_rule():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"soft_rules": [{"deny": "a", "allow": "b"}]}, schema)
