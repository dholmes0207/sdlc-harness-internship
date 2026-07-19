"""Safety-rail constants must be INLINE in SKILL.md, not buried in references.

Rationale (owner decision): safety rails that are needed every time run
stay in SKILL.md body; detail/explanation in references is fine, but the
numeric ceiling, trap, and mandatory-fix must be visible on first read.
"""
from pathlib import Path
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILL_MD = REPO_ROOT / "harness" / "plugins" / "hs" / "skills" / "drawio" / "SKILL.md"

# --- Safety-rail constants that MUST appear inline in SKILL.md body ---

SAFETY_RAILS = {
    "2576": "Claude vision API width/height ceiling (2576px)",
    "--width 2000": "export flag to stay under the 2576 ceiling",
    "repair_png": "mandatory PNG repair after -e export (IEND truncation)",
    "max 2": "self-check round cap (max 2 rounds then show user)",
    "rounds": "review-loop safety valve (5 rounds then suggest desktop)",
    "-e": "IEND truncation trap — -e PNGs need repair_png",
    "400": "vision returns 400 on broken -e PNG",
}

# English section headings that SKILL.md body must contain.
# (Presence check, not blocklist — red-team M3: blocklist fragile.)
ENGLISH_HEADINGS = [
    "## When to use",
    "## Prerequisites",
    "## Workflow",
    "## References",
    "## Boundaries",
    "## Critical safety rails",
]


# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _skill_body() -> str:
    """Return SKILL.md body (frontmatter stripped)."""
    text = SKILL_MD.read_text()
    idx = text.find("---", 4)
    if idx == -1:
        return text
    return text[idx + 3:]


def test_safety_rails_inline():
    """Every safety-rail constant appears in SKILL.md body."""
    assert SKILL_MD.exists(), "SKILL.md not found"
    body = _skill_body()
    missing = []
    for rail, desc in SAFETY_RAILS.items():
        if rail not in body:
            missing.append(f"  {rail!r} ← {desc}")
    assert not missing, (
        "Safety-rail constants missing from SKILL.md body:\n"
        + "\n".join(missing)
        + "\n\nThese must be inline in SKILL.md, not buried in references."
    )
