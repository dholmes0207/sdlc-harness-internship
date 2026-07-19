"""test_port_ai.py — ck AI/media cluster, now collapsed into the single `hs` plugin.

Faithful port (master mapping): ai-multimodal, ai-artist, stitch, shader, threejs,
html-video, remotion, media-processing. Post-collapse every skill lives under the
ONE plugin `hs` (harness/plugins/hs/skills/<skill>) and the `ai` group is an
install-time LABEL (components.yaml), no longer its own `hs-ai` plugin. The tests
stay DYNAMIC over the group's canonical membership (components.yaml `ai: skills:`):
every ported skill must live under hs/skills, pass the structure gate (no blocking
finding), carry no stale `ck:` namespace, and have a STANDARDIZE provenance line.
Wiring (marketplace + component label) and plugin presence are checked once.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "harness" / "scripts"))

import check_skill_structure as css  # noqa: E402
import component_config as cc  # noqa: E402
import verify_install as vi  # noqa: E402

# Post-collapse: ONE plugin `hs` holds every skill; the `ai` group is a label.
_HS = _REPO / "harness" / "plugins" / "hs" / "skills"
_STANDARDIZE = _REPO / "docs" / "STANDARDIZE.md"
_GROUP = "ai"


def _group_members():
    """Canonical `ai` group membership from components.yaml (the label index)."""
    return list(cc.load_components().get(_GROUP, {}).get("skills", []))


def _ai_skills():
    """Group members actually present under the collapsed hs/skills tree."""
    return sorted(s for s in _group_members() if (_HS / s / "SKILL.md").is_file())


@pytest.mark.dev_repo
def test_all_ported_ai_skills_live_under_hs():
    # The split premise (hs-ai/skills/<skill>) is gone: assert the inverse — every
    # ai-group skill now resolves under the single hs plugin's skills dir.
    members = _group_members()
    assert members, "no ai-group skills declared in components.yaml"
    for s in members:
        assert (_HS / s / "SKILL.md").is_file(), \
            "%s must live under collapsed hs/skills/%s" % (s, s)


@pytest.mark.dev_repo
def test_all_ported_ai_skills_gate_clean():
    skills = _ai_skills()
    assert skills, "no ai-group skills under hs/skills yet"
    for s in skills:
        v = css.check_skill(str(_HS / s))
        blocking = [f for f in v["findings"] if f.get("severity") != "advisory"]
        assert blocking == [], "%s gained blocking findings: %s" % (s, blocking)


def test_marketplace_declares_only_hs():
    # Collapse: the local marketplace declares exactly ONE plugin, `hs`.
    mp = json.loads((_REPO / "harness" / "plugins" / ".claude-plugin"
                     / "marketplace.json").read_text(encoding="utf-8"))
    plugins = mp["plugins"]
    assert len(plugins) == 1, "marketplace must declare exactly one plugin: %s" \
        % [p.get("name") for p in plugins]
    assert plugins[0]["name"] == "hs"
    names = {p["name"] for p in plugins}
    assert "hs-ai" not in names  # the per-group sibling plugin is gone


def test_plugin_presence_clean():
    # No install drift: every declared plugin exists on disk, and nothing points
    # at a now-deleted hs-ai sibling dir.
    probs = vi.plugin_presence_problems(_REPO)
    assert probs == [], probs
    assert not any("hs-ai" in rel or "hs-ai" in prob for rel, prob in probs), probs


def test_components_label_for_ai_exists():
    # `ai` survives as an install-time LABEL with its skill list. The `plugin` key
    # may be absent now that groups are labels, not plugins — don't require it.
    comps = cc.load_components()
    assert "ai" in comps, "ai group label missing from components.yaml"
    assert comps["ai"].get("skills"), "ai label must keep its skills list"
    if comps["ai"].get("plugin"):
        assert comps["ai"]["plugin"] == "hs-ai"  # legacy name during transition


@pytest.mark.dev_repo  # reads docs/STANDARDIZE.md — dev-only, absent on installs
def test_every_ported_ai_skill_in_standardize():
    std = _STANDARDIZE.read_text(encoding="utf-8")
    for s in _ai_skills():
        assert s in std, "%s missing a STANDARDIZE provenance line" % s


def test_no_stale_ck_namespace_in_ai_skills():
    # The port re-brands ck:<skill> -> hs:<skill>; no `ck:` command namespace may
    # survive in the ported skill dirs (a bare 'ck' word in prose is fine). Scope
    # the scan to the ai-group skill dirs under the collapsed hs/skills tree.
    skills = _ai_skills()
    if not skills:
        pytest.skip("ai-group skills not present yet")
    dirs = [str(_HS / s) for s in skills]
    out = subprocess.run(
        ["grep", "-rEln", r"\bck:[a-z]", *dirs],
        capture_output=True, text=True).stdout
    offenders = [f for f in out.splitlines() if f.strip()]
    assert offenders == [], "stale ck: namespace in: %s" % offenders
