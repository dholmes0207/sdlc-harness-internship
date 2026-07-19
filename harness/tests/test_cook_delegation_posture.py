"""test_cook_delegation_posture.py — cook delegate-by-default posture (prose gate).

Cook's per-phase implement loop is delegate-by-default (@developer) when the plan is
`mode: hard`, with `--in-place` as the manual override that wins over the plan mode.
This is a PROSE posture (not a hard runtime gate): the checks assert the SKILL surfaces
the override flag, the body points at the delegation posture reference, the resolution
order is documented, and the body stays under the thin-core cap.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import skill_frontmatter  # noqa: E402
import check_skill_structure as css  # noqa: E402

_SKILLS = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills"
_COOK = _SKILLS / "cook" / "SKILL.md"
_PER_PHASE = _SKILLS / "cook" / "references" / "per-phase-tdd.md"
_PATTERNS = _SKILLS / "cook" / "references" / "subagent-patterns.md"


def test_cook_argument_hint_has_in_place():
    """The manual opt-out flag is discoverable in the argument-hint."""
    fm = skill_frontmatter.frontmatter(_COOK.read_text(encoding="utf-8"))
    assert "--in-place" in (fm.get("argument-hint") or ""), "argument-hint missing --in-place"


def test_cook_body_points_at_delegation_posture():
    """The body carries a pointer to the delegation posture (a thin line, detail in
    the reference)."""
    body = skill_frontmatter.body(_COOK.read_text(encoding="utf-8")).lower()
    assert "delegat" in body and "per-phase-tdd.md" in body, (
        "body does not point at the delegation posture reference")
    assert "--in-place" in body, "body does not name the --in-place override"


def test_cook_body_within_cap():
    """The cook body stays within its thin-core cap — the documented spine-orchestrator
    override in data/thin-core-caps.yaml, not the general cap (single source of truth via
    check_skill_structure.skill_body_cap)."""
    body = skill_frontmatter.body(_COOK.read_text(encoding="utf-8"))
    cap = css.skill_body_cap("cook")
    assert len(body) <= cap, "cook body %d > cap %d" % (len(body), cap)


def test_per_phase_tdd_ref_documents_d3():
    """The reference documents the resolution order, reading mode from a deterministic
    SOURCE — not a `--hard`/`--fast` flag (cook has none): the `--in-place` flag wins,
    then the plan frontmatter `mode: fast` (inline) / `mode: hard` (delegate), then the
    `plan-graph.yaml` fallback assessment when no mode is stamped."""
    txt = _PER_PHASE.read_text(encoding="utf-8")
    for token in ("--in-place", "mode: fast", "mode: hard", "plan-graph.yaml"):
        assert token in txt, "per-phase-tdd.md missing resolution branch %s" % token


def test_subagent_patterns_has_sequential_developer():
    """The sequential per-phase @developer delegate section exists with a full
    delegation-context snippet."""
    txt = _PATTERNS.read_text(encoding="utf-8")
    assert "Sequential Per-Phase Delegate" in txt, (
        "subagent-patterns.md missing the sequential per-phase delegate section")
    assert "hs:developer" in txt
