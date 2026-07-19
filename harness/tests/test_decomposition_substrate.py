"""Decomposition substrate after the plugin collapse.

Phase A originally REGISTERED six themed sibling plugins (flow/think/research/create/
mem/meta) alongside the seven ck-port siblings — 14 plugins total — with default
install enabling only the spine `hs`. The 2.0.0 collapse folded every sibling back
into `hs`: the marketplace now declares ONLY `hs`, all 97 skills live under
hs/skills/, and the themed groups survive as install-time LABELS in components.yaml
(the `plugin:` key is being dropped — groups are labels now, not plugins).

This file keeps the substrate's intent against the collapsed topology:
  * the marketplace is a single spine entry, on disk, loadable;
  * no themed sibling plugin dir survives;
  * every themed group still exists as a component label whose skills resolve under
    hs/skills/;
  * default install still enables only the spine (no sibling plugin is left to gate).
A SYNTHETIC 14-plugin fixture proves the reverse engine still derives the themed
group set the substrate once registered (mirrors the `repo` fixture in
test_migrate_decomposition.py — the live tree is now empty of hs-<g> dirs).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import component_config as cc  # noqa: E402
import migrate_decomposition as md  # noqa: E402

NEW_COMPONENTS = ["flow", "think", "research", "create", "mem", "meta"]
NEW_PLUGINS = ["hs-" + c for c in NEW_COMPONENTS]
THEMED_COMPONENTS = ["uiux", "viz", "devops", "ai", "stack", "integrations", "extra"]
THEMED_PLUGINS = ["hs-uiux", "hs-viz", "hs-devops", "hs-ai", "hs-stack",
                  "hs-integrations", "hs-extra"]
ALL_NONSPINE_PLUGINS = THEMED_PLUGINS + NEW_PLUGINS  # 13
ALL_THEMED_COMPONENTS = NEW_COMPONENTS + THEMED_COMPONENTS  # 13 group labels


def test_marketplace_declares_only_spine_hs():
    # Collapse inverse of "declares all 14 plugins": the marketplace collapses to
    # ONE entry — the spine `hs` — which must exist on disk and be loadable. No
    # sibling plugin (themed or ck-port) survives.
    mk = json.loads((ROOT / "harness/plugins/.claude-plugin/marketplace.json").read_text())
    names = {p["name"] for p in mk["plugins"]}
    assert len(mk["plugins"]) == 1
    assert names == {"hs"}
    for plug in ALL_NONSPINE_PLUGINS:
        assert plug not in names, f"{plug} should be collapsed out of the marketplace"
    base = ROOT / "harness/plugins"
    for p in mk["plugins"]:
        assert (base / p["source"]).resolve().is_dir(), \
            f"{p['name']} source {p['source']} is not a dir"


def test_no_sibling_plugin_dirs_survive():
    # Collapse inverse of "new plugin dirs exist with manifest": after the collapse
    # every themed sibling plugin dir is GONE — its skills moved under hs/skills/.
    base = ROOT / "harness/plugins"
    for plug in ALL_NONSPINE_PLUGINS:
        assert not (base / plug).exists(), f"{plug} dir should be collapsed away"
    # the spine still ships a manifest + skills dir.
    pj = base / "hs" / ".claude-plugin" / "plugin.json"
    assert pj.is_file(), "spine hs missing plugin.json"
    assert json.loads(pj.read_text())["name"] == "hs"
    assert (base / "hs" / "skills").is_dir()


@pytest.mark.dev_repo
def test_themed_groups_survive_as_labels_with_skills_under_spine():
    # Collapse inverse of "components map <comp> -> plugin hs-<comp>": every themed
    # group survives as an install-time LABEL. The `plugin` key is being dropped, so
    # do not require it; instead prove the label's skills now resolve under hs/skills/.
    comps = cc.load_components()
    hs_skills = ROOT / "harness/plugins/hs/skills"
    for comp in NEW_COMPONENTS:
        assert comp in comps, f"themed group label {comp} not declared"
        skills = comps[comp].get("skills", [])
        assert skills, f"group {comp} declares no skills"
        for s in skills:
            assert (hs_skills / s / "SKILL.md").is_file(), \
                f"{comp} skill {s} not collapsed under hs/skills/"
        plugin = comps[comp].get("plugin")
        if plugin:  # tolerated during the drop; must not dangle at a deleted dir
            assert not (ROOT / "harness/plugins" / plugin).exists(), \
                f"label {comp} still points at live sibling {plugin}"


def test_default_install_enables_only_spine():
    # Intent preserved verbatim: a default install enables only the spine. Post-
    # collapse there are no sibling plugins to gate, so any plugin entry that the
    # projector still emits must resolve to a NON-loadable (collapsed) plugin — i.e.
    # nothing besides the spine is wired on. The spine `hs` is not a component, so it
    # never appears in plugin_states and is wired ON by the installer.
    comps = cc.load_components()
    sel = cc.load_policy()
    states = cc.plugin_states(comps, sel)
    assert "hs" not in states
    # any plugin still surfaced by a residual `plugin:` key must be a collapsed-away
    # sibling (its dir gone), so enabling it would load nothing — never the spine.
    for plug in states:
        assert plug != "hs"
        assert not (ROOT / "harness/plugins" / plug).exists(), \
            f"{plug} still has a live dir — collapse incomplete"


# ---- synthetic 14-plugin fixture: the substrate topology is still derivable ----

@pytest.fixture
def synthetic_split_tree(tmp_path: Path) -> Path:
    """A minimal pre-collapse 14-plugin tree (spine + 13 themed siblings), each
    sibling carrying one skill dir. Mirrors the `repo` fixture in
    test_migrate_decomposition.py: the LIVE tree no longer has hs-<g> dirs, so the
    reverse-engine reverse-check must run on a built fixture, not on disk."""
    plug = tmp_path / "harness/plugins"
    (plug / "hs/skills/zzspine").mkdir(parents=True)
    (plug / "hs/skills/zzspine/SKILL.md").write_text("---\nname: hs:zzspine\n---\n")
    for comp in ALL_THEMED_COMPONENTS:
        sd = plug / f"hs-{comp}/skills/zz{comp}"
        sd.mkdir(parents=True)
        (sd / "SKILL.md").write_text(f"---\nname: hs-{comp}:zz{comp}\n---\n")
    return tmp_path


def test_reverse_engine_recovers_themed_groups_from_synthetic_tree(synthetic_split_tree):
    # _invert_non_spine sources the non-spine set from the FILESYSTEM. On the
    # synthetic 14-plugin tree it must recover exactly the 13 themed groups the
    # substrate once registered (and never the spine).
    ns = md._invert_non_spine(synthetic_split_tree)
    assert set(ns.values()) == set(ALL_THEMED_COMPONENTS)
    assert "hs" not in ns.values()
    # each recovered skill belongs to its own group.
    for comp in ALL_THEMED_COMPONENTS:
        assert ns.get(f"zz{comp}") == comp
