"""Contract for the four existing-skill edits in the AK v2 delta.

advise gains a grill/pressure-test scope (description + a behaving body block),
skill-creator gains a portability & third-party-safety reference (dev-id
stripped) and links it, prompt's --for=research task class routes to an enriched
Research Brief template, and watzup points conversation-state handoffs at
hs:handoff. The prompt test asserts the ROUTE, not a bare phrase match, so an
orphaned enrichment in an unreachable file fails.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "harness" / "plugins" / "hs" / "skills"


def _read(rel):
    return (SKILLS_DIR / rel).read_text(encoding="utf-8")


def _description(text):
    """Return the frontmatter description value (handles quoted one-liners)."""
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text
    for line in block.splitlines():
        if line.strip().startswith("description:"):
            val = line.split(":", 1)[1].strip()
            return val.strip('"').strip("'")
    return ""


def test_advise_description_grill():
    desc = _description(_read("advise/SKILL.md")).lower()
    assert ("pressure-test" in desc) or ("grill" in desc), \
        "advise description must advertise the grill/pressure-test scope"
    assert "existing plan" in desc, \
        "advise description must name pressure-testing an existing plan"


def test_advise_body_grill_mode():
    body = _read("advise/SKILL.md").lower()
    assert "grill mode" in body, "advise body must define a grill mode"
    assert "existing plan" in body, \
        "grill mode must target an existing plan/design, not a raw idea"


def test_skill_creator_reference_exists():
    rel = "skill-creator/references/skill-ecosystem-portability-and-safety.md"
    path = SKILLS_DIR / rel
    assert path.is_file(), "missing portability & safety reference"
    text = path.read_text(encoding="utf-8")
    for banned in ("davidondrej", "ce70edaa"):
        assert banned not in text, \
            "reference still leaks upstream dev-id %r" % banned
    for section in ("Why Use a Skill", "Cross-Runtime Portability",
                    "Third-Party Skill Safety"):
        assert section in text, "reference missing section %r" % section


def test_skill_creator_links_reference():
    text = _read("skill-creator/SKILL.md")
    assert "skill-ecosystem-portability-and-safety.md" in text, \
        "skill-creator SKILL.md must link the new reference drawer"


def test_prompt_research_enriched():
    skill = _read("prompt/SKILL.md")
    tmpl = _read("prompt/references/templates-h-m.md")
    # ROUTE (anti-orphan): one line ties --for=research to the Research Brief
    # template AND names the file that holds it.
    route_line = next(
        (ln for ln in skill.splitlines()
         if "--for=research" in ln and "Research Brief" in ln
         and "templates-h-m.md" in ln),
        None,
    )
    assert route_line is not None, \
        "prompt Step 5 must route --for=research to the Research Brief template in templates-h-m.md"
    # The template block actually carries the brief structure.
    low = tmpl.lower()
    assert "research brief" in low, "templates-h-m.md missing the Research Brief block"
    for marker in ("source hierarchy", "gap round", "decision relevance"):
        assert marker in low, "Research Brief template missing %r" % marker


def test_watzup_points_handoff():
    text = _read("watzup/SKILL.md")
    assert "hs:handoff" in text, \
        "watzup must point conversation-state handoffs at hs:handoff"
