"""test_port_stack.py — ck full-stack cluster, now collapsed into the single `hs`
plugin.

Faithful port (master mapping): backend-development, frontend-development,
mobile-development, databases, react-best-practices, web-frameworks, tanstack,
better-auth. Post-collapse the per-group plugin dirs are gone: every skill of the
`stack` group lives under hs/skills/<skill>. The group list is derived from
components.yaml ('stack: skills:') so the tests track the real membership. Each
ported skill must pass the structure gate (no blocking finding), carry no stale
`ck:` namespace, and have a STANDARDIZE provenance line. Wiring (marketplace +
component) and plugin presence are checked once.
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

_HS = _REPO / "harness" / "plugins" / "hs" / "skills"
_STANDARDIZE = _REPO / "docs" / "STANDARDIZE.md"


def _group_skills(group):
    """Membership of a ck-port group from components.yaml ('<group>: skills:')."""
    comps = cc.load_components()
    return sorted(comps.get(group, {}).get("skills", []))


def _stack_skills():
    """stack-group skills that actually live under the collapsed hs tree."""
    return [s for s in _group_skills("stack")
            if (_HS / s / "SKILL.md").is_file()]


@pytest.mark.dev_repo
def test_react_best_practices_ported_first():
    assert (_HS / "react-best-practices" / "SKILL.md").is_file(), \
        "first slice: react-best-practices must live under hs/skills/react-best-practices"
    assert "react-best-practices" in _group_skills("stack"), \
        "react-best-practices must remain a member of the stack group in components.yaml"


@pytest.mark.dev_repo
def test_all_ported_stack_skills_gate_clean():
    skills = _stack_skills()
    assert skills, "no stack skills found under hs/skills"
    for s in skills:
        v = css.check_skill(str(_HS / s))
        blocking = [f for f in v["findings"] if f.get("severity") != "advisory"]
        assert blocking == [], "%s gained blocking findings: %s" % (s, blocking)


def test_marketplace_declares_only_hs():
    mp = json.loads((_REPO / "harness" / "plugins" / ".claude-plugin"
                     / "marketplace.json").read_text(encoding="utf-8"))
    # collapse: one plugin remains, named hs; the per-group hs-stack plugin is gone —
    # its skills folded into hs.
    assert len(mp["plugins"]) == 1, mp["plugins"]
    assert mp["plugins"][0]["name"] == "hs"


def test_plugin_presence_clean_after_collapse():
    # no per-group hs-stack plugin remains, and the collapsed hs tree is clean
    probs = vi.plugin_presence_problems(_REPO)
    assert not any("hs-stack" in rel or "hs-stack" in prob
                   for rel, prob in probs), probs


def test_components_keeps_stack_label():
    # the group survives as an install-time label in components.yaml; the legacy
    # 'plugin' key is being dropped (groups are no longer plugins post-collapse).
    comps = cc.load_components()
    assert "stack" in comps, "stack group label dropped"
    assert comps["stack"].get("skills"), "stack group lost its skills"


@pytest.mark.dev_repo  # reads docs/STANDARDIZE.md — dev-only, absent on installs
def test_every_ported_stack_skill_in_standardize():
    std = _STANDARDIZE.read_text(encoding="utf-8")
    for s in _stack_skills():
        assert s in std, "%s missing a STANDARDIZE provenance line" % s


@pytest.mark.dev_repo
def test_no_stale_ck_namespace_in_stack_skills():
    # the port re-brands ck:<skill> -> hs:<skill>; no `ck:` command namespace may
    # survive in any ported stack skill (a bare 'ck' word in prose is fine).
    skills = _stack_skills()
    assert skills, "no stack skills found under hs/skills"
    offenders = []
    for s in skills:
        out = subprocess.run(
            ["grep", "-rEln", r"\bck:[a-z]", str(_HS / s)],
            capture_output=True, text=True).stdout
        offenders += [f for f in out.splitlines()
                      if not f.endswith("test_port_stack.py")]
    assert offenders == [], "stale ck: namespace in: %s" % offenders
