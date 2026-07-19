"""test_standards_delegation_integration.py — cross-cutting invariants for the whole
standards-to-subagent + delegate-by-default change.

Locks the pieces standing together: the SubagentStart directive (P1), the delegation
posture pointers in cook/plan/test (P2-P4), the write-class agents naming the standards
(P5), that no new hs:-skill dependency edge was introduced by the @agent delegation, and
that every changed SKILL body stays within the thin-core cap.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SCRIPTS = _ROOT / "scripts"
_HOOKS = _ROOT / "hooks"
for _p in (_SCRIPTS, _HOOKS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import skill_frontmatter  # noqa: E402
import check_skill_structure as css  # noqa: E402

_SKILLS = _ROOT / "plugins" / "hs" / "skills"
_AGENTS = _ROOT / "plugins" / "hs" / "agents"

# The pre-existing skill-dep edges for the three delegating skills. Delegation uses
# @agent refs (not hs:<skill> routes), so these MUST NOT grow.
EXPECTED_DEPS = {
    "plan": {"cook", "sequential-thinking", "workflow-orchestrate"},
    "cook": {"bakeoff", "plan", "remember", "test"},
    "test": {"code-review", "cook", "debug", "fix"},
}


def test_directive_and_delegation_coexist():
    """P1 directive + P2-P4 posture pointers + P5 agent read-lines all present at once."""
    import subagent_init as si
    txt = si.context_text({"agent_type": "developer"})
    assert "docs/code-standards.md" in txt and "you MUST read" in txt, "P1 directive absent"

    for skill in ("cook", "plan", "test"):
        body = skill_frontmatter.body((_SKILLS / skill / "SKILL.md").read_text(encoding="utf-8")).lower()
        assert "delegat" in body, "%s SKILL missing delegation posture" % skill

    for agent in ("tester", "planner", "debugger"):
        assert "docs/code-standards.md" in (_AGENTS / ("%s.md" % agent)).read_text(encoding="utf-8"), (
            "%s.md missing standards read-line" % agent)


def test_no_new_skill_dep_edges_from_delegation():
    """Delegation added @agent refs only — no new hs:<skill> dependency edge."""
    import yaml
    deps = yaml.safe_load((_ROOT / "data" / "skill-deps.yaml").read_text(encoding="utf-8"))["skills"]
    for skill, expected in EXPECTED_DEPS.items():
        actual = set(deps.get(skill, {}).get("deps", []))
        assert actual == expected, "%s deps changed: %s (expected %s)" % (skill, actual, expected)


def test_all_changed_skills_within_cap():
    """Every changed SKILL body stays within the thin-core char cap."""
    for skill in ("cook", "plan", "test"):
        body = skill_frontmatter.body((_SKILLS / skill / "SKILL.md").read_text(encoding="utf-8"))
        cap = css.skill_body_cap(skill)
        assert len(body) <= cap, "%s body %d > cap %d" % (skill, len(body), cap)
