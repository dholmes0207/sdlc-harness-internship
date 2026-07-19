"""The open_plan lifecycle step (flip plan status approved -> in_progress) must be
surfaced in the cook SKILL.md thin-core, not only in a load-on-demand reference drawer.

A load-bearing step buried below the fold gets skipped on small tasks: the gate's
active-plan resolver returns ONLY an in_progress plan, so a skipped flip silently
breaks ship/deploy resolution and auto-close (BL-121, found in real use).
"""
from pathlib import Path

_COOK = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "skills" / "cook"


def _body(skill_md: Path) -> str:
    text = skill_md.read_text(encoding="utf-8")
    if text.startswith("---"):
        text = text.split("---", 2)[-1]  # drop frontmatter
    return text


def test_open_plan_surfaced_in_thin_core():
    body = _body(_COOK / "SKILL.md")
    assert "open_plan.py" in body, "open_plan step must live in the SKILL.md thin-core"
    assert "in_progress" in body, "thin-core must state the approved -> in_progress flip"
