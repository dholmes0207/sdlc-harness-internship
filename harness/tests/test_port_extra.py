"""test_port_extra.py — ck misc/content/PM cluster, now collapsed into the `hs` plugin.

Faithful port (master mapping): ask, llms, watzup, copywriting, ghpm, mintlify,
markdown-novel-viewer, cti-expert. Post-collapse every skill lives under the ONE
plugin `hs` (harness/plugins/hs/skills/<skill>) and the `extra` group is an
install-time LABEL (components.yaml), no longer its own `hs-extra` plugin. The
tests stay DYNAMIC over the group's canonical membership (components.yaml
`extra: skills:`): every ported skill must live under hs/skills, pass the structure
gate (no blocking finding), carry no stale `ck:` namespace, and have a STANDARDIZE
provenance line. Wiring (marketplace + component label) and presence checked once.
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

# Post-collapse: ONE plugin `hs` holds every skill; the `extra` group is a label.
_HS = _REPO / "harness" / "plugins" / "hs" / "skills"
_STANDARDIZE = _REPO / "docs" / "STANDARDIZE.md"
_GROUP = "extra"


def _group_members():
    """Canonical `extra` group membership from components.yaml (the label index)."""
    return list(cc.load_components().get(_GROUP, {}).get("skills", []))


def _extra_skills():
    """Group members actually present under the collapsed hs/skills tree."""
    return sorted(s for s in _group_members() if (_HS / s / "SKILL.md").is_file())


@pytest.mark.dev_repo
def test_ask_ported_first():
    assert (_HS / "ask" / "SKILL.md").is_file(), \
        "ask must live under the collapsed hs/skills/ask"
    assert "ask" in _extra_skills()


@pytest.mark.dev_repo
def test_all_ported_extra_skills_live_under_hs():
    # The split premise (hs-extra/skills/<skill>) is gone: assert the inverse —
    # every extra-group skill now resolves under the single hs plugin's skills dir.
    members = _group_members()
    assert members, "no extra-group skills declared in components.yaml"
    for s in members:
        assert (_HS / s / "SKILL.md").is_file(), \
            "%s must live under collapsed hs/skills/%s" % (s, s)


@pytest.mark.dev_repo
def test_all_ported_extra_skills_gate_clean():
    skills = _extra_skills()
    assert skills, "no extra-group skills under hs/skills yet"
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
    assert "hs-extra" not in names  # the per-group sibling plugin is gone


def test_plugin_presence_clean():
    # No install drift: every declared plugin exists on disk, and nothing points
    # at a now-deleted hs-extra sibling dir.
    probs = vi.plugin_presence_problems(_REPO)
    assert probs == [], probs
    assert not any("hs-extra" in rel or "hs-extra" in prob
                   for rel, prob in probs), probs


def test_components_label_for_extra_exists():
    # `extra` survives as an install-time LABEL with its skill list. The `plugin`
    # key may be absent now that groups are labels, not plugins — don't require it.
    comps = cc.load_components()
    assert "extra" in comps, "extra group label missing from components.yaml"
    assert comps["extra"].get("skills"), "extra label must keep its skills list"
    if comps["extra"].get("plugin"):
        assert comps["extra"]["plugin"] == "hs-extra"  # legacy name in transition


@pytest.mark.dev_repo  # reads docs/STANDARDIZE.md — dev-only, absent on installs
def test_every_ported_extra_skill_in_standardize():
    std = _STANDARDIZE.read_text(encoding="utf-8")
    for s in _extra_skills():
        assert s in std, "%s missing a STANDARDIZE provenance line" % s


def test_no_stale_ck_namespace_in_extra_skills():
    # The port re-brands ck:<skill> -> hs:<skill>; no `ck:` command namespace may
    # survive in the ported skill dirs (a bare 'ck' word in prose is fine). Scope
    # the scan to the extra-group skill dirs under the collapsed hs/skills tree.
    skills = _extra_skills()
    if not skills:
        pytest.skip("extra-group skills not present yet")
    dirs = [str(_HS / s) for s in skills]
    out = subprocess.run(
        ["grep", "-rEln", r"\bck:[a-z]", *dirs],
        capture_output=True, text=True).stdout
    offenders = [f for f in out.splitlines() if f.strip()]
    assert offenders == [], "stale ck: namespace in: %s" % offenders
