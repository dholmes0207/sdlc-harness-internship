#!/usr/bin/env python3
"""Tests for component_config.py — the ship-all-but-off projector.

A component is a named bundle (hooks + scripts + skills + data) that ships
always but can be runtime-disabled by writing `enabled: false` into the REAL
enable mechanism (`harness-hooks.yaml` `hooks.<name>.enabled`) for each of its
hooks — never a parallel flag. The projector reads the static declaration
(components.yaml) + the selection (component-policy.yaml) and rewrites the
hooks file deterministically.

SAFETY: every test routes writes through tmp_path. The real harness-hooks.yaml
is never touched (hook_enabled re-reads it live; a stray enabled:false there
would silently disable a live gate).
"""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

import component_config as cc  # noqa: E402


# ---- fixtures -------------------------------------------------------------

COMPONENTS = {
    "rbac": {"hooks": ["agent_rbac_guard"],
             "scripts": ["agent_permissions", "rbac_selfcheck"],
             "data": ["agent-permissions.yaml"], "requires": []},
    "decision-capture": {"hooks": ["nudge_context_inject", "decision_capture_nudge"],
                         "skills": ["remember"], "requires": []},
    "design": {"skills": ["frontend-design"], "hooks": [], "requires": []},
}


def _write_components(tmp_path, comps=COMPONENTS):
    p = tmp_path / "components.yaml"
    import yaml
    p.write_text("# components.yaml — test fixture\n" +
                 yaml.safe_dump({"components": comps}, sort_keys=True),
                 encoding="utf-8")
    return p


def _write_hooks(tmp_path, hooks=None):
    p = tmp_path / "harness-hooks.yaml"
    import yaml
    body = yaml.safe_dump({"hooks": hooks or {}}, sort_keys=True)
    p.write_text("# harness-hooks.yaml — test header (preserve me)\n" + body,
                 encoding="utf-8")
    return p


def _write_policy(tmp_path, selection=None):
    p = tmp_path / "component-policy.yaml"
    import yaml
    p.write_text("# component-policy.yaml — test fixture\n" +
                 yaml.safe_dump({"components": selection or {}}, sort_keys=True),
                 encoding="utf-8")
    return p


# ---- pure projector -------------------------------------------------------

def test_project_writes_enabled_false_for_off_component():
    out = cc.project(COMPONENTS, {"rbac": False}, {})
    assert out["agent_rbac_guard"]["enabled"] is False


def test_project_sets_enabled_true_for_on_component():
    out = cc.project(COMPONENTS, {"rbac": True}, {})
    assert out["agent_rbac_guard"]["enabled"] is True


def test_project_disables_every_hook_of_off_component():
    out = cc.project(COMPONENTS, {"decision-capture": False}, {})
    assert out["nudge_context_inject"]["enabled"] is False
    assert out["decision_capture_nudge"]["enabled"] is False


def test_project_preserves_unrelated_hook_entries():
    out = cc.project(COMPONENTS, {"rbac": False},
                     {"gate_stage": {"mode": "advisory"}})
    # a core hook not owned by any component is left exactly as-is
    assert out["gate_stage"] == {"mode": "advisory"}
    assert out["agent_rbac_guard"]["enabled"] is False


def test_project_preserves_other_keys_on_a_managed_hook():
    out = cc.project(COMPONENTS, {"rbac": False},
                     {"agent_rbac_guard": {"mode": "blocking"}})
    assert out["agent_rbac_guard"]["mode"] == "blocking"
    assert out["agent_rbac_guard"]["enabled"] is False


def test_project_off_to_on_clears_prior_false():
    # toggling a component back on must lift the enabled:false it set before
    off = cc.project(COMPONENTS, {"rbac": False}, {})
    on = cc.project(COMPONENTS, {"rbac": True}, off)
    assert on["agent_rbac_guard"]["enabled"] is True


def test_project_skill_only_component_touches_no_hooks():
    out = cc.project(COMPONENTS, {"design": False}, {})
    assert out == {}  # design declares no hooks → nothing to project


# ---- requires validation --------------------------------------------------

def test_validate_requires_blocks_enabling_with_disabled_dep():
    comps = {"a": {"requires": ["b"]}, "b": {"requires": []}}
    with pytest.raises(cc.ComponentConfigError):
        cc.validate_requires(comps, {"a": True, "b": False})


def test_validate_requires_ok_when_dep_enabled():
    comps = {"a": {"requires": ["b"]}, "b": {"requires": []}}
    cc.validate_requires(comps, {"a": True, "b": True})  # no raise


def test_validate_requires_unknown_dep_raises():
    comps = {"a": {"requires": ["ghost"]}}
    with pytest.raises(cc.ComponentConfigError):
        cc.validate_requires(comps, {"a": True})


# ---- cross-check (tamper / drift detector) --------------------------------

def test_cross_check_flags_hook_on_for_off_component():
    bad = cc.cross_check(COMPONENTS, {"rbac": False},
                         {"agent_rbac_guard": {"enabled": True}})
    assert any("agent_rbac_guard" in v for v in bad)


def test_cross_check_clean_after_project():
    hooks = cc.project(COMPONENTS, {"rbac": False}, {})
    assert cc.cross_check(COMPONENTS, {"rbac": False}, hooks) == []


# ---- resolved selection (missing entry → ship-all default on) -------------

def test_resolved_selection_defaults_missing_to_enabled():
    sel = cc.resolved_selection(COMPONENTS, {"rbac": False})
    assert sel["rbac"] is False
    assert sel["decision-capture"] is True  # not in policy → default on
    assert sel["design"] is True


# ---- set_component integration (temp files only) --------------------------

def test_set_component_disabled_writes_policy_and_projects(tmp_path):
    comps = _write_components(tmp_path)
    hooks = _write_hooks(tmp_path)
    policy = _write_policy(tmp_path)
    state = tmp_path / "install-state.json"

    cc.set_component("rbac", False, components_path=comps, policy_path=policy,
                     hooks_path=hooks, state_path=state)

    import yaml
    pol = yaml.safe_load(policy.read_text())["components"]
    assert pol["rbac"] is False
    hk = yaml.safe_load(hooks.read_text())["hooks"]
    assert hk["agent_rbac_guard"]["enabled"] is False
    st = json.loads(state.read_text())
    assert st["components"]["rbac"]["enabled"] is False


def test_set_component_unknown_raises(tmp_path):
    comps = _write_components(tmp_path)
    hooks = _write_hooks(tmp_path)
    policy = _write_policy(tmp_path)
    with pytest.raises(cc.ComponentConfigError):
        cc.set_component("ghost", False, components_path=comps,
                         policy_path=policy, hooks_path=hooks,
                         state_path=tmp_path / "s.json")


def test_set_component_invalid_dep_writes_nothing(tmp_path):
    # validate-before-write: enabling a component whose dep is off must raise
    # BEFORE any file changes (no partial write).
    comps = _write_components(
        tmp_path, {"a": {"hooks": ["ha"], "requires": ["b"]},
                   "b": {"hooks": ["hb"], "requires": []}})
    hooks = _write_hooks(tmp_path)
    policy = _write_policy(tmp_path, {"a": False, "b": False})
    before_hooks = hooks.read_text()
    before_policy = policy.read_text()
    with pytest.raises(cc.ComponentConfigError):
        cc.set_component("a", True, components_path=comps, policy_path=policy,
                         hooks_path=hooks, state_path=tmp_path / "s.json")
    assert hooks.read_text() == before_hooks
    assert policy.read_text() == before_policy


def test_set_component_preserves_hooks_header(tmp_path):
    comps = _write_components(tmp_path)
    hooks = _write_hooks(tmp_path)
    policy = _write_policy(tmp_path)
    cc.set_component("rbac", False, components_path=comps, policy_path=policy,
                     hooks_path=hooks, state_path=tmp_path / "s.json")
    assert "preserve me" in hooks.read_text()


# ---- loaders --------------------------------------------------------------

# ---- component <-> plugin --------------------------------------------------

def test_plugin_states_maps_only_plugin_components(tmp_path):
    comps = cc.load_components(_write_components(tmp_path, {
        "viz": {"plugin": "hs-viz"},
        "rbac": {"hooks": ["agent_rbac_guard"]}}))
    st = cc.plugin_states(comps, {"viz": True, "rbac": False})
    assert st == {"hs-viz": True}


def test_apply_selection_writes_enabled_plugins(tmp_path):
    cfile = _write_components(tmp_path, {
        "viz": {"plugin": "hs-viz", "skills": ["x"]},
        "rbac": {"hooks": ["agent_rbac_guard"]}})
    settings = tmp_path / "settings.local.json"
    cc.apply_selection({"viz": False, "rbac": True},
                       components_path=cfile,
                       policy_path=_write_policy(tmp_path),
                       hooks_path=_write_hooks(tmp_path),
                       state_path=tmp_path / "s.json",
                       settings_path=settings)
    ep = json.loads(settings.read_text())["enabledPlugins"]
    assert ep == {"hs-viz@hs-local": False}


def test_apply_selection_no_plugin_leaves_settings_untouched(tmp_path):
    cfile = _write_components(tmp_path, {
        "rbac": {"hooks": ["agent_rbac_guard"]}})
    settings = tmp_path / "settings.local.json"
    cc.apply_selection({"rbac": False}, components_path=cfile,
                       policy_path=_write_policy(tmp_path),
                       hooks_path=_write_hooks(tmp_path),
                       state_path=tmp_path / "s.json",
                       settings_path=settings)
    assert not settings.exists()  # no plugin component -> never created


def test_apply_selection_preserves_user_enabled_plugins(tmp_path):
    cfile = _write_components(tmp_path, {"viz": {"plugin": "hs-viz"}})
    settings = tmp_path / "settings.local.json"
    settings.write_text(json.dumps({"enabledPlugins": {"foo@bar": True}}),
                        encoding="utf-8")
    cc.apply_selection({"viz": True}, components_path=cfile,
                       policy_path=_write_policy(tmp_path),
                       hooks_path=_write_hooks(tmp_path),
                       state_path=tmp_path / "s.json",
                       settings_path=settings)
    ep = json.loads(settings.read_text())["enabledPlugins"]
    assert ep == {"foo@bar": True, "hs-viz@hs-local": True}


def test_apply_selection_policy_records_only_deviations(tmp_path):
    # The policy file's contract: "Records ONLY deviations from ship-all" — a
    # component absent is ENABLED (default). A ship-all install (every component
    # True) is NOT a deviation and must leave the policy empty, so the shipped
    # `components: {}` round-trips byte-stable through a default install.
    cfile = _write_components(tmp_path)
    policy = _write_policy(tmp_path)
    cc.apply_selection({"rbac": True, "decision-capture": True, "design": True},
                       components_path=cfile, policy_path=policy,
                       hooks_path=_write_hooks(tmp_path),
                       state_path=tmp_path / "s.json")
    assert cc.load_policy(policy) == {}            # no all-true expansion
    # a real OFF is the only thing recorded
    cc.apply_selection({"rbac": False, "decision-capture": True, "design": True},
                       components_path=cfile, policy_path=policy,
                       hooks_path=_write_hooks(tmp_path),
                       state_path=tmp_path / "s.json")
    assert cc.load_policy(policy) == {"rbac": False}


def test_apply_selection_no_op_leaves_hooks_bytes_stable(tmp_path):
    # A ship-all install over a hooks file that already has every component hook
    # enabled:true is a no-op — it must NOT rewrite (reformat) the file, else the
    # shipped+manifested harness-hooks.yaml drifts its hash on a default install.
    # Fixture is HAND-AUTHORED (body comment + chosen order) — a safe_dump rewrite
    # would strip the comment and reorder, so a byte-equal assert proves skip-on-no-op.
    hooks = tmp_path / "harness-hooks.yaml"
    hooks.write_text(
        "# harness-hooks.yaml — hand authored\n"
        "hooks:\n"
        "  agent_rbac_guard:\n"
        "    enabled: true  # rbac gate\n"
        "  nudge_context_inject:\n"
        "    enabled: true\n"
        "  decision_capture_nudge:\n"
        "    enabled: true\n", encoding="utf-8")
    before = hooks.read_text()
    cc.apply_selection({"rbac": True, "decision-capture": True, "design": True},
                       components_path=_write_components(tmp_path),
                       policy_path=_write_policy(tmp_path), hooks_path=hooks,
                       state_path=tmp_path / "s.json")
    assert hooks.read_text() == before


def test_load_components_malformed_raises(tmp_path):
    p = tmp_path / "components.yaml"
    p.write_text("components: [not, a, mapping]\n", encoding="utf-8")
    with pytest.raises(cc.ComponentConfigError):
        cc.load_components(p)


def test_load_components_reads_shipped_file():
    # the real shipped declaration must parse and expose the rbac component
    comps = cc.load_components()
    assert "rbac" in comps
    assert "agent_rbac_guard" in comps["rbac"]["hooks"]


def test_write_enabled_plugins_filters_phantom_plugins(tmp_path):
    # post-collapse the 13 former group `plugin:` keys are phantoms (their plugin
    # is gone from the marketplace). A toggle must NOT pollute settings with dead
    # `hs-<group>@hs-local:false` keys — only marketplace-declared plugins survive.
    mp = tmp_path / "harness/plugins/.claude-plugin"
    mp.mkdir(parents=True)
    (mp / "marketplace.json").write_text('{"plugins":[{"name":"hs"}]}')
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}")
    cc._write_enabled_plugins(
        str(settings),
        {"hs-think": False, "hs-viz": False, "hs": True},
        marketplace="hs-local")
    ep = json.loads(settings.read_text()).get("enabledPlugins", {})
    assert "hs@hs-local" in ep
    assert "hs-think@hs-local" not in ep
    assert "hs-viz@hs-local" not in ep


def test_write_enabled_plugins_no_marketplace_does_not_filter(tmp_path):
    # when no marketplace can be found (e.g. a bare test fixture), don't silently
    # drop everything — the legacy behavior of writing the given states stands.
    settings = tmp_path / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}")
    cc._write_enabled_plugins(str(settings), {"hs-viz": True}, marketplace="hs-local")
    ep = json.loads(settings.read_text()).get("enabledPlugins", {})
    assert ep.get("hs-viz@hs-local") is True
