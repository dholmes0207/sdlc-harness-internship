"""Journal-authority contract: the journal is session history, durable decisions
belong in the DEC ledger — and the journal home moved docs/journals -> plans/journals.

The move test greps each live file WHOLE (not by line number) so a site the
plan's line list missed is still caught. Two lock tests pin the deliberate
non-edits: the governance DEC rationale (GUARD_LIST, historical) and the inert
vendored gitignore both keep their docs/journals string.
"""

from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

# Live sites whose journal path is a real reference and must move.
MOVED_FILES = [
    "harness/rules/terminal-voice.md",
    "harness/plugins/hs/agents/journal-writer.md",
    "harness/plugins/hs/skills/plan/references/archive-workflow.md",
    "harness/plugins/hs/skills/project-organization/references/markdown-body-templates.md",
    "harness/plugins/hs/skills/project-organization/SKILL.md",
    "harness/plugins/hs/skills/journal/SKILL.md",
    "harness/plugins/hs/skills/journal/references/entry-format.md",
]

# The journal != decision-ledger boundary must appear here.
BOUNDARY_FILES = [
    "harness/plugins/hs/skills/journal/SKILL.md",
    "harness/plugins/hs/skills/journal/references/entry-format.md",
    "harness/plugins/hs/agents/journal-writer.md",
]


def _read(rel):
    return (REPO_ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", MOVED_FILES)
def test_journal_move_no_docs_journals(rel):
    assert "docs/journals" not in _read(rel), \
        "%s still references the old docs/journals home" % rel


@pytest.mark.parametrize("rel", MOVED_FILES)
def test_journal_move_has_plans_journals(rel):
    assert "plans/journals" in _read(rel), \
        "%s should point at the new plans/journals home" % rel


@pytest.mark.parametrize("rel", BOUNDARY_FILES)
def test_journal_boundary_prose(rel):
    text = _read(rel)
    low = text.lower()
    assert "DEC ledger" in text, \
        "%s must state durable decisions go to the DEC ledger" % rel
    assert "decision" in low, "%s boundary must mention decisions" % rel
    assert ("not" in low) or ("never" in low), \
        "%s boundary must say the journal does NOT decide" % rel


def test_governance_sites_untouched():
    # DEC rationale on GUARD_LIST is a point-in-time record; keep it historical.
    assert "docs/journals" in _read("docs/decisions.yaml"), \
        "governance SSOT rationale must keep its historical docs/journals reference"
    assert "docs/journals" in _read("docs/decisions.md"), \
        "rendered governance ledger must keep its historical docs/journals reference"


def test_gitignore_inert_untouched():
    # Vendored Next.js scaffold gitignore scoped to use-mcp/scripts/ — inert.
    rel = "harness/plugins/hs/skills/use-mcp/scripts/.gitignore"
    assert "docs/journals/*" in _read(rel), \
        "inert vendored gitignore must be left as-is (not a live journal reference)"
