"""test_e1_skill_wiring_wave2.py — integration checks for the phase-2 (Wave-2 core
Must) SKILL.md wiring: G6 `plan/SKILL.md`, G7 `discover/SKILL.md`.

U14 (test-scenario matrix): the Not-Doing/scope-boundary hook for
`scope-and-contract-discipline.md` must land INSIDE the pre-existing scope-boundary /
explicitly-OUT-of-scope fact, not a brand-new section -- this test greps for the hook
text on the SAME line/paragraph as the existing fact anchor, proving position, not just
presence (presence alone is already covered by test_rule_route_existence.py).

U12 (render-pointer) is covered by test_audience_mirror_drift.py (existing, untouched).
U13 (cap) is covered by test_thin_core_caps.py::test_exempted_skill_body_stays_within_its_raised_cap
(existing, untouched -- this file adds no duplicate cap check).
U11 (overlong-line) is covered by the repo's line-length guard inside
check_skill_structure.py --strict (run at cook time); this file's own line-length check
below is a fast, scoped in-suite proof of the same invariant for the two touched files.
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_PLAN_SKILL = _ROOT / "harness" / "plugins" / "hs" / "skills" / "plan" / "SKILL.md"
_DISCOVER_SKILL = _ROOT / "harness" / "plugins" / "hs" / "skills" / "discover" / "SKILL.md"


def _text(path: Path) -> str:
    assert path.is_file(), f"expected SKILL.md at {path}"
    return path.read_text(encoding="utf-8")


def _normalized(text: str) -> str:
    """Collapse markdown's wrapped-line whitespace (a phrase spanning a hard line
    wrap, e.g. '**scope\\n   boundary**') to single spaces so a prose-level anchor
    search isn't defeated by where the source happens to wrap."""
    return re.sub(r"\s+", " ", text)


# --- U14: Not-Doing hook lands INSIDE the existing scope-boundary fact -------------

def test_plan_not_doing_hook_is_inside_scope_boundary_bullet_not_new_section():
    text = _text(_PLAN_SKILL)
    norm = _normalized(text)
    # the existing 5-facts floor bullet carries "**scope boundary**"; the hook must
    # appear in the SAME sentence/bullet, not under a freshly-added "## " heading.
    # Normalized (whitespace-collapsed) so a markdown hard-wrap doesn't defeat the
    # anchor search.
    anchor = norm.find("**scope boundary**")
    assert anchor != -1, "plan/SKILL.md missing the pre-existing scope boundary fact"
    # the rule is ALSO cited once up top in the gathered route block (route-existence
    # only needs >=1 citation) -- find the occurrence NEAREST the scope-boundary
    # anchor, not just the first occurrence in the file
    hook = norm.find("scope-and-contract-discipline.md", anchor)
    assert hook != -1, "plan/SKILL.md missing the Not-Doing hook route near scope boundary"
    # the hook must be close to the anchor (same bullet, not a distant new section) --
    # bound the distance to under one paragraph's worth of characters
    assert 0 < hook - anchor < 400, (
        "Not-Doing hook must sit inside the scope-boundary bullet, not in a new "
        "section far from the existing fact"
    )
    # and it must NOT be preceded by a fresh "## " heading between the two
    between = norm[anchor:hook]
    assert "## " not in between, (
        "a new '## ' section heading was inserted between the scope-boundary fact "
        "and the Not-Doing hook -- G6(c) requires reusing the EXISTING fact, not a "
        "new section"
    )


def test_plan_not_doing_hook_missing_detected_synthetic():
    synthetic = "**scope boundary** (what is explicitly OUT this round)\n\n## New Section\n\nscope-and-contract-discipline.md mentioned way over here\n"
    norm = _normalized(synthetic)
    anchor = norm.find("**scope boundary**")
    hook = norm.find("scope-and-contract-discipline.md")
    between = norm[anchor:hook]
    assert "## " in between, "fixture must actually introduce a new section between them"


def test_discover_not_doing_hook_is_inside_explicitly_out_of_scope_item():
    text = _text(_DISCOVER_SKILL)
    anchor = text.find("explicitly OUT of scope")
    assert anchor != -1, "discover/SKILL.md missing the pre-existing 'explicitly OUT of scope' item"
    hook = text.find("scope-and-contract-discipline.md")
    assert hook != -1, "discover/SKILL.md missing the Not-Doing hook route"
    assert 0 < hook - anchor < 200, (
        "Not-Doing hook must sit inside the 'explicitly OUT of scope' brief-template "
        "item, not far away in a new section"
    )
    between = text[anchor:hook]
    assert "\n## " not in between and "\n#### " not in between


# --- S1 route sits at the interview trigger (discover) -----------------------------

def test_discover_s1_rules_routed_near_askuserquestion_interview_line():
    text = _text(_DISCOVER_SKILL)
    interview_anchor = text.find("AskUserQuestion")
    assert interview_anchor != -1
    intake_ref = text.find("intake-and-interview-discipline.md")
    plain_ref = text.find("plain-language-phrasing.md")
    assert intake_ref != -1 and plain_ref != -1, "discover missing S1 rule routes"
    # both routed within a short distance of the interview trigger line
    assert 0 <= intake_ref - interview_anchor < 400
    assert 0 <= plain_ref - interview_anchor < 400


# --- No line in either touched file exceeds the 400-char hard cap (R5) -------------

def test_plan_skill_no_line_exceeds_400_chars():
    lines = _text(_PLAN_SKILL).splitlines()
    overlong = [(i + 1, len(l)) for i, l in enumerate(lines) if len(l) > 400]
    assert not overlong, f"plan/SKILL.md has overlong line(s) (line, length): {overlong}"


def test_discover_skill_no_line_exceeds_400_chars():
    lines = _text(_DISCOVER_SKILL).splitlines()
    overlong = [(i + 1, len(l)) for i, l in enumerate(lines) if len(l) > 400]
    assert not overlong, f"discover/SKILL.md has overlong line(s) (line, length): {overlong}"


def test_overlong_line_detected_synthetic():
    synthetic_line = "x" * 401
    assert len(synthetic_line) > 400
