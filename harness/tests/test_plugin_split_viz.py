"""test_plugin_split_viz.py — the viz cluster after the plugin collapse.

Originally the first real skill split moved five viz skills (excalidraw, mermaidjs,
graphify, tech-graph, preview) OUT of core `hs` into a sibling plugin `hs-viz`. The
2.0.0 plugin collapse REVERSED every sibling split: all 97 skills now live under the
single spine plugin `hs` and the invoke form is `hs:<skill>` again. This file keeps
the same intent — the viz cluster is structurally sound, gate-clean, wired through
ONE marketplace entry, documented, and carries no stale `hs-viz:` prefix anywhere.
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "harness" / "scripts"))

import check_skill_structure as css  # noqa: E402
import verify_install as vi  # noqa: E402

VIZ = ["excalidraw", "mermaidjs", "graphify", "tech-graph", "preview", "drawio"]
_HS = _REPO / "harness" / "plugins" / "hs" / "skills"
_HSVIZ = _REPO / "harness" / "plugins" / "hs-viz" / "skills"  # collapsed away


@pytest.mark.dev_repo
def test_viz_skills_live_under_spine_hs():
    # Collapse inverse of "moved to hs-viz": every viz skill now lives under
    # hs/skills/<skill> and the hs-viz sibling plugin is gone.
    for s in VIZ:
        assert (_HS / s / "SKILL.md").is_file(), "%s not under hs/skills" % s
        assert not (_HSVIZ / s).exists(), "%s still under hs-viz (not collapsed)" % s


def test_viz_skills_stay_gate_clean():
    # the collapse must not introduce a BLOCKING structural finding (advisory is fine —
    # these skills were PASS_WITH_RISK/advisory before too).
    for s in VIZ:
        v = css.check_skill(str(_HS / s))
        blocking = [f for f in v["findings"] if f.get("severity") != "advisory"]
        assert blocking == [], "%s gained blocking findings: %s" % (s, blocking)


def test_marketplace_declares_only_spine_hs():
    # Collapse inverse of "marketplace declares hs-viz": the local marketplace
    # now declares ONLY the spine plugin `hs` (len == 1, name == "hs"); no
    # hs-viz (or any other sibling) survives.
    import json
    mp = json.loads((_REPO / "harness" / "plugins" / ".claude-plugin"
                     / "marketplace.json").read_text(encoding="utf-8"))
    names = {p["name"] for p in mp["plugins"]}
    assert len(mp["plugins"]) == 1
    assert names == {"hs"}
    assert "hs-viz" not in names


def test_plugin_presence_clean_after_collapse():
    # No marketplace entry may dangle. Post-collapse there is exactly one (hs),
    # and in particular nothing referencing the deleted hs-viz dir.
    probs = vi.plugin_presence_problems(_REPO)
    assert not any("hs-viz" in rel or "hs-viz" in prob for rel, prob in probs), probs
    assert probs == [], probs


@pytest.mark.dev_repo
def test_components_keeps_viz_as_label():
    # Collapse inverse of "components maps viz -> plugin hs-viz": `viz` survives
    # as an install-time logical label whose skill list is intact and whose skills
    # now resolve under the spine hs/skills/. The `plugin` key is being dropped
    # (groups are labels now, not plugins); a residual `plugin: hs-viz` is tolerated
    # mid-drop but its sibling dir must already be collapsed away (it must NOT still
    # be a live plugin dir).
    import component_config as cc
    comps = cc.load_components()
    assert "viz" in comps, "viz label missing from components"
    assert set(comps["viz"].get("skills", [])) == set(VIZ)
    for s in VIZ:
        assert (_HS / s / "SKILL.md").is_file(), "%s not under hs/skills" % s
    plugin = comps["viz"].get("plugin")
    if plugin:  # residual key tolerated during the drop, but must not be live
        assert not (_REPO / "harness" / "plugins" / plugin).exists(), \
            "viz label still points at a live sibling plugin dir %r" % plugin


def test_zero_stale_hs_viz_prefix_refs():
    # Collapse inverse of "0 ref prefix cu": after the collapse NO `hs-viz:<viz>`
    # (slash or bare) and no `hs-viz/skills/<viz>` path may survive under harness/
    # or docs/ — every reference must read the bare hs: form again.
    # (Provenance rows in docs/STANDARDIZE.md cite the old hs-viz paths as the
    # historical Dest; those are excluded as documented port history.)
    alt = "|".join(re.escape(v) for v in VIZ)
    pat = re.compile(r"hs-viz[:/](?:skills/)?(?:%s)\b" % alt)
    offenders = []
    for base in (_REPO / "harness", _REPO / "docs"):
        out = subprocess.run(
            ["grep", "-rEln",
             r"hs-viz[:/](skills/)?(excalidraw|mermaidjs|graphify|tech-graph|preview)",
             "--include=*.md", "--include=*.yaml", "--include=*.yml", "--include=*.py",
             str(base)],
            capture_output=True, text=True).stdout
        for f in out.splitlines():
            fp = Path(f)
            # the test tree deliberately exercises BOTH the old hs-viz: form and the
            # collapsed hs: form (reverse-engine + catalog fixtures), so test files
            # are not "stale production refs" — exclude harness/tests/ wholesale.
            if "harness/tests/" in f or "harness\\tests\\" in f:
                continue
            if fp.name == "STANDARDIZE.md":
                continue  # documented port provenance, not a live ref
            text = fp.read_text(encoding="utf-8")
            if pat.search(text):
                offenders.append(f)
    assert offenders == [], "stale hs-viz: prefix in: %s" % offenders
