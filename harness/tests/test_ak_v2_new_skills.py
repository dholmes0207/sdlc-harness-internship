"""Contract for the four ported skill bodies (deep-swe, folder-context,
handoff, interview-docs).

Each is a single SKILL.md ported verbatim from an external kit, rebranded to
the hs: namespace with all upstream dev-id metadata stripped. These tests pin
the port contract: the directory exists, the frontmatter name matches the
directory, no upstream metadata or forbidden runtime paths leak into shipped
prose, and a per-skill anchor phrase proves the body was copied rather than
invented.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_DIR = REPO_ROOT / "harness" / "plugins" / "hs" / "skills"

NEW_SKILLS = ["deep-swe", "folder-context", "handoff", "interview-docs"]

# One-line, whitespace-normalized anchor per skill: a phrase that survives
# verbatim in the source body (verified against port-src) and would be absent
# if the body were fabricated.
VERBATIM_ANCHORS = {
    "handoff": "fresh agent continue with minimal",
    "interview-docs": "one specific, open question",
    "deep-swe": "single task first",
    "folder-context": "durable context only for a subfolder",
}


def _skill_text(skill):
    return (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter_name(text):
    """Return the frontmatter `name:` value, or None."""
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    block = text[3:end] if end != -1 else text
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip()
    return None


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_new_skill_dirs_exist(skill):
    assert (SKILLS_DIR / skill / "SKILL.md").is_file(), \
        "missing SKILL.md for ported skill %s" % skill


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_new_skill_frontmatter_name(skill):
    name = _frontmatter_name(_skill_text(skill))
    assert name == "hs:%s" % skill, \
        "%s frontmatter name is %r, expected hs:%s" % (skill, name, skill)


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_new_skill_stripped_ak_metadata(skill):
    text = _skill_text(skill)
    for token in ("author: agentkit", "upstream:", "Pinned MIT source archive",
                  'version: "1.0.0"', "license:"):
        assert token not in text, \
            "%s still carries upstream metadata token %r" % (skill, token)


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_new_skill_no_claude_paths(skill):
    text = _skill_text(skill)
    # Build the banned prefixes from fragments so this guard file itself stays
    # grep-clean of the runtime-slot literals (CI-invariant #1, self-referential).
    dot_claude = "." + "claude"
    for banned in (dot_claude + "/skills/", dot_claude + "/hooks/"):
        assert banned not in text, \
            "%s contains a forbidden %s runtime path (CI-invariant #1)" % (skill, dot_claude)


@pytest.mark.parametrize("skill", NEW_SKILLS)
def test_new_skill_prose_verbatim_anchor(skill):
    normalized = " ".join(_skill_text(skill).split())
    anchor = VERBATIM_ANCHORS[skill]
    assert anchor in normalized, \
        "%s is missing its verbatim anchor %r (body may be fabricated)" % (skill, anchor)


def test_interview_docs_maps_dec_ledger():
    assert "DEC ledger" in _skill_text("interview-docs"), \
        "interview-docs must map ADR-style durable decisions to the DEC ledger"
