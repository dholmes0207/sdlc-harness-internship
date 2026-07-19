"""test_backlog_prose_routing.py — skill prose routes deferred work through the
backlog register, not a hand-edit of BACKLOG.md.

Invariant: no skill instruction tells the agent to APPEND/ADD a line or entry to
BACKLOG.md by hand — that mechanism is replaced by `backlog_register.py add`.
Descriptive mentions (the file is an approved root markdown, "do not edit shared
files", taxonomy tables, reading the backlog) are ALLOWED — the rewrite changes
the mechanism, never deletes the workflow step.
"""
import re
from pathlib import Path

import pytest

_SKILLS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "skills"

# Hand-edit-to-ADD phrasings: an imperative to physically append/add a line or
# entry to the BACKLOG.md file. These are what the register replaces.
_HAND_EDIT_ADD = re.compile(
    r"(append|add)\s+(an?\s+|one\s+)?(line|entry|item)\b[^.\n]*\bBACKLOG\.md"
    r"|append[^.\n]*\bto\b[^.\n]*\bBACKLOG\.md",
    re.IGNORECASE,
)

# Files whose deferred-work routing was rewritten to reference the tool. Each
# must now name `backlog_register` so the agent knows the mechanism.
_REWRITTEN = [
    "afk/SKILL.md",
    "bakeoff/SKILL.md",
    "code-review/references/issue-routing.md",
    "cook/SKILL.md",
    "critique/SKILL.md",
    "discover/references/when-to-discover.md",
    "discover/SKILL.md",
    "insights/SKILL.md",
    "plan/references/red-team-gate.md",
    "project-organization/references/docs-vs-plans-vs-code.md",
    "remember/SKILL.md",
    "retro/SKILL.md",
    "skill-creator/references/orchestrator-skills.md",
    "triage/references/defect-repro.md",
    "triage/references/gate-wiring.md",
    "triage/SKILL.md",
    "understand/SKILL.md",
]


def _skill_md_files():
    return sorted(_SKILLS.rglob("*.md"))


def test_no_skill_prose_hand_edits_backlog():
    offenders = []
    for f in _skill_md_files():
        for i, line in enumerate(f.read_text(encoding="utf-8").splitlines(), 1):
            if "backlog_register" in line:
                continue  # the tool mechanism — allowed
            if _HAND_EDIT_ADD.search(line):
                offenders.append("%s:%d: %s" % (f.relative_to(_SKILLS), i,
                                                line.strip()))
    assert not offenders, "hand-edit-add instructions remain:\n" + "\n".join(offenders)


@pytest.mark.parametrize("rel", _REWRITTEN)
@pytest.mark.dev_repo
def test_rewritten_files_reference_the_register(rel):
    text = (_SKILLS / rel).read_text(encoding="utf-8")
    assert "backlog_register" in text, (
        "%s routes deferred work but does not reference backlog_register" % rel)


def test_remember_routes_backlog_through_register():
    text = (_SKILLS / "remember" / "SKILL.md").read_text(encoding="utf-8")
    # the Deferred-work disposition row must point at the register add command
    assert re.search(r"Deferred work.*backlog_register\.py add", text), \
        "hs:remember Deferred-work row must route through backlog_register.py add"
