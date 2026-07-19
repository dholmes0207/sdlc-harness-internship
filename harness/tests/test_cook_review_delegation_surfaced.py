"""Post-cook delegation (Steps 4-6: test / code-review / finalize) must be surfaced in
the cook SKILL.md thin-core, not only in the load-on-demand workflow-steps drawer.

A `--tdd` run stops naturally at the last phase's integration barrier + verification.json;
if the mandatory post-build delegation lives only below the fold, it gets skipped on a
"small" task and the workflow ends with inline self-review (which misses what an
independent reviewer catches) and no finalize sync-back. The thin-core must carry the
MUST-directive + the "Task calls = 0 => INCOMPLETE" backstop, pointing at the full drawer.
"""
from pathlib import Path

_COOK = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "skills" / "cook"


def _body(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[-1]  # drop frontmatter
    return text


def test_post_cook_review_and_finalize_delegation_surfaced():
    body = _body(_COOK / "SKILL.md")
    low = body.lower()
    # Code review after the last phase is a MANDATORY delegation, not inline self-review.
    # Agents are referenced with the @agent convention (bare name, matching the agent .md
    # frontmatter), distinct from skill routes (hs:name).
    assert "@code-reviewer" in body
    assert "do not review code yourself" in low, \
        "thin-core must forbid inline self-review at the code-review step"
    # Test + finalize delegations named in thin-core so they cannot be forgotten.
    assert "@tester" in body, "Step 4 test delegation must be surfaced"
    assert "@docs-manager" in body and "@git-manager" in body, \
        "Step 6 finalize delegations must be surfaced"
    # The hard backstop: a cook that delegated nothing is incomplete.
    assert "incomplete" in low, "thin-core must state the zero-delegation backstop"
    # Must point at the full protocol drawer rather than duplicating it.
    assert "workflow-steps.md" in body
