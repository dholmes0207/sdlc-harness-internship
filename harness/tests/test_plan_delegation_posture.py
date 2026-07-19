"""test_plan_delegation_posture.py — plan delegate-by-default posture (prose gate).

The plan skill's middle (write plan.md/phases + red-team + sweep) is delegate-by-default
to @planner/@red-teamer on a hard-mode plan, while the interview bookends (understand,
scope-challenge, validate, approval) MUST stay at main — a subagent has no TTY so
AskUserQuestion dies there. plan/SKILL.md must carry no overlong line (the pre-step split
of the 451-char line), which the strict gate requires.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import skill_frontmatter  # noqa: E402
import check_skill_structure as css  # noqa: E402

_PLAN = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills" / "plan"
_SKILL = _PLAN / "SKILL.md"
_PHASE_DECOMP = _PLAN / "references" / "phase-decomposition.md"
_RED_TEAM = _PLAN / "references" / "red-team-gate.md"

MAX_LINE_CHARS = 400


def test_plan_body_points_at_planner_delegation():
    """The body points at delegating phase-writing to @planner."""
    body = skill_frontmatter.body(_SKILL.read_text(encoding="utf-8")).lower()
    assert "delegat" in body, "plan body does not mention delegation"
    assert "@planner" in body or "hs:planner" in body, "plan body does not name @planner"


def test_plan_bookends_stay_at_main():
    """The reference spells out that the interview bookends stay at main (no TTY in a
    subagent)."""
    txt = _PHASE_DECOMP.read_text(encoding="utf-8").lower()
    assert "at main" in txt or "stay at main" in txt, "bookends-at-main not documented"
    assert "no tty" in txt or "no-tty" in txt, "the no-TTY reason is not stated"
    for bookend in ("understand", "scope-challenge", "validate", "approval"):
        assert bookend in txt, "bookend %s not named as main-only" % bookend


def test_plan_skill_no_overlong_line():
    """Overlong-line lock: every line of plan/SKILL.md is within the per-line cap, and the
    body stays within the thin-core cap."""
    text = _SKILL.read_text(encoding="utf-8")
    overlong = [i + 1 for i, ln in enumerate(text.splitlines()) if len(ln) > MAX_LINE_CHARS]
    assert not overlong, "plan/SKILL.md has overlong line(s): %s" % overlong
    body = skill_frontmatter.body(text)
    cap = css.skill_body_cap("plan")
    assert len(body) <= cap, "plan body %d > cap %d" % (len(body), cap)


def test_red_team_gate_spawns_red_teamer():
    """The red-team gate now spawns @red-teamer by default (not just an inline persona)."""
    txt = _RED_TEAM.read_text(encoding="utf-8")
    assert "red-teamer" in txt, "red-team-gate.md does not spawn @red-teamer"
    assert "Task(" in txt or "subagent_type" in txt or "spawn" in txt.lower(), (
        "red-team-gate.md names no spawn mechanism")
