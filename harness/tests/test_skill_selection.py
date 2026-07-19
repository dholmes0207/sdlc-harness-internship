"""Install-time per-skill selection (collapse to one hs plugin).

After the collapse every skill lives under harness/plugins/hs/skills. There is no
per-plugin toggle left, so a fresh install picks skills at the dir level: a group
expands to its skills, deps auto-tick, and the spine core is always present. The
deselected skills are OMITTED at copy — the only disable that works for plugin
skills on this CC (disable-model-invocation is unsupported, per the mode-A probe).
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import skill_selection as ss  # noqa: E402
import skill_deps  # noqa: E402


@pytest.mark.dev_repo
def test_all_skills_scans_the_collapsed_tree():
    skills = ss.all_skills(ROOT)
    # 112 INVOKABLE skills (a dir with SKILL.md) + `spec` (hs:spec, the PO-facing
    # product spec skill) = 113 + `shape` (hs:shape, the BA-facing bridge skill:
    # a fresh SKILL.md added this phase) = 114. The universe in skill-deps.yaml
    # also counts `common` (ai-group api-key helpers) and `_docslib` (docs-ssot
    # shared lib) — both resource dirs with NO SKILL.md — not invokable skills,
    # so they never enter the selectable set.
    # Intentional snapshot: bump this when a skill is added/removed. +2 = `advise`
    # (hs:advise, interview-driven advisory) + `issue-to-plan` (audited issue→plan);
    # +1 = `fable-thinking` (hs:fable-thinking, the ported Fable reasoning protocol);
    # +4 = the ported deep-swe/folder-context/handoff/interview-docs skill bodies
    # (all four ship a SKILL.md, so all four are selectable regardless of ship-off).
    assert len(skills) == 122, "expected 122 invokable skills under hs/skills"
    assert "plan" in skills and "excalidraw" in skills and "ai-multimodal" in skills
    assert "common" not in skills  # resource dir, not a skill
    assert "_docslib" not in skills  # resource dir, not a skill


def test_resource_dir_common_is_never_omitted():
    # `common` carries no SKILL.md, so it is not selectable and not omittable — it
    # always ships, keeping the ai-group scripts that import it intact even when
    # only a subset of skills is installed.
    enabled = ss.resolve_enabled(source_root=ROOT, groups=["viz"])
    assert "common" not in ss.omitted(ROOT, enabled)


@pytest.mark.dev_repo
def test_no_args_default_omits_default_off():
    # A fresh install with no explicit selection now ships all skills MINUS the
    # default-off catalog (skill-defaults.yaml), not ship-all. The floor and the
    # interview keep-list survive; the opt-in clusters do not.
    enabled = ss.resolve_enabled(source_root=ROOT)
    off = ss.load_defaults(ROOT)
    assert off, "the default-off catalog must load"
    assert enabled == ss.all_skills(ROOT) - off
    assert "excalidraw" not in enabled and "ai-multimodal" not in enabled  # off
    assert set(skill_deps.core_immutable()) <= enabled                     # floor on
    assert "scenario" in enabled and "remember" in enabled                 # keep-list on


def test_defaults_file_missing_falls_back_to_ship_all_with_warning(tmp_path, capsys):
    # A source tree with no skill-defaults.yaml must not brick the installer: default
    # selection falls back to ship-all and warns, rather than shipping an empty set.
    src = tmp_path / "src"
    skills = src / "harness" / "plugins" / "hs" / "skills"
    for name in ("plan", "use", "shopify"):
        (skills / name).mkdir(parents=True)
        (skills / name / "SKILL.md").write_text(
            "---\nname: hs:%s\ndescription: x\n---\n# %s\n" % (name, name), encoding="utf-8")
    (src / "harness" / "data").mkdir(parents=True)
    (src / "harness" / "data" / "skill-deps.yaml").write_text(
        "core_immutable: [plan, use]\nskills:\n  plan: {deps: []}\n  use: {deps: []}\n"
        "  shopify: {deps: []}\n", encoding="utf-8")
    enabled = ss.resolve_enabled(source_root=src)  # no skill-defaults.yaml present
    assert enabled == ss.all_skills(src)           # ship-all fallback
    assert "WARN" in capsys.readouterr().err


def test_floor_does_not_pull_dep_closure():
    # A spine-only install (empty explicit selection) is EXACTLY the 16-skill floor —
    # the floor is unioned bare, never expanded, so a floor skill's own deps (e.g.
    # cook -> bakeoff) are not dragged in and no opt-in cluster resurrects itself (#23).
    enabled = ss.resolve_enabled(source_root=ROOT, skills=[], groups=[])
    assert enabled == set(skill_deps.core_immutable())
    assert "bakeoff" not in enabled       # a cook dep, but not floor
    assert "excalidraw" not in enabled     # a viz opt-in


@pytest.mark.dev_repo
def test_user_picked_skill_still_autoticks_deps():
    # An explicitly-picked skill DOES pull its transitive deps (auto-tick from the
    # user seed is preserved — only the floor is exempt from closure).
    enabled = ss.resolve_enabled(source_root=ROOT, skills=["graphify"])
    assert "graphify" in enabled
    assert {"drawio", "mermaidjs"} <= enabled  # graphify's declared deps


@pytest.mark.dev_repo
def test_group_expands_to_its_skills_plus_core():
    enabled = ss.resolve_enabled(source_root=ROOT, groups=["viz"])
    # viz members present
    assert {"excalidraw", "mermaidjs", "graphify", "preview", "tech-graph"} <= enabled
    # the spine core is always present even when only one group is picked
    assert set(skill_deps.core_immutable()) <= enabled


def test_spine_only_install_does_not_drag_the_viz_cluster():
    # a spine-only (no skills, no groups) install must NOT force-install the opt-in
    # viz cluster via the `setup` spine skill's old illustrative excalidraw ref — that
    # defeats the per-skill opt-in. (Legit spine routes — critique/research/voice —
    # may still come along; only the dropped viz-cluster drag is asserted gone.)
    enabled = ss.resolve_enabled(source_root=ROOT, skills=[], groups=[])
    for opt_in in ("excalidraw", "mermaidjs", "preview", "tech-graph"):
        assert opt_in not in enabled, "%s force-installed into a spine-only install" % opt_in


def test_core_immutable_never_omitted():
    # even selecting a single non-core skill, no core skill is omitted
    enabled = ss.resolve_enabled(source_root=ROOT, skills=["excalidraw"])
    omitted = ss.omitted(ROOT, enabled)
    assert not (set(skill_deps.core_immutable()) & omitted), \
        "a core-immutable spine skill was omitted"
    # Guard the floor SIZE too: a membership-only check cannot catch a skill
    # accidentally dropped from core_immutable in skill-deps.yaml. The floor is the
    # 13 spine + the off-skill proxy trio (use/find-skills/cleanup) = 16.
    assert len(skill_deps.core_immutable()) == 16, \
        "core_immutable floor must hold exactly 16 skills"


@pytest.mark.dev_repo
def test_omitted_is_complement_of_enabled():
    enabled = ss.resolve_enabled(source_root=ROOT, groups=["viz"])
    omitted = ss.omitted(ROOT, enabled)
    assert enabled.isdisjoint(omitted)
    assert enabled | omitted == ss.all_skills(ROOT)
    # a clearly-unrelated skill (no dep path from viz/core) is omitted
    assert "shopify" in omitted


def test_unknown_group_is_an_error():
    import pytest
    with pytest.raises(ss.SelectionError):
        ss.resolve_enabled(source_root=ROOT, groups=["nonsuch"])


def test_add_skills_empty_equals_recommended_default():
    """The interactive 'recommended + clusters' path with ZERO clusters picked must
    resolve to the SAME set as the plain recommended default — the recommended set
    is a fixed baseline, never re-dep-closed (off-as-dep is valid)."""
    default = ss.resolve_enabled(source_root=ROOT)
    add_none = ss.resolve_enabled(source_root=ROOT, add_skills=[])
    assert add_none == default
    assert len(add_none) == len(default)


@pytest.mark.dev_repo
def test_add_skills_adds_only_the_picked_cluster_closure():
    """Picking a cluster adds that cluster's skills (+ their own deps) on top of the
    recommended baseline — but does NOT drag in the recommended set's stashed deps
    (repomix/contract-test/web-testing), which only the buggy full-closure pulled."""
    default = ss.resolve_enabled(source_root=ROOT)
    with_viz = ss.resolve_enabled(source_root=ROOT, add_skills=["excalidraw", "mermaidjs"])
    assert "excalidraw" in with_viz and "mermaidjs" in with_viz
    assert default <= with_viz                       # baseline preserved exactly
    # the recommended set's own off-deps stay stashed unless a picked cluster needs them
    assert "repomix" not in (with_viz - default) or "repomix" in ss.skill_deps.resolve(
        {"excalidraw", "mermaidjs"}, ROOT / "harness/data/skill-deps.yaml")
