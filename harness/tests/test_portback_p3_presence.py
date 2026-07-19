"""test_portback_p3_presence.py — HIGH-value dropped prose restored into the harness.

These are pure-prose reference ports (copy -> re-brand -> wire). The suite cannot
judge prose quality, so it locks the structural facts: the files exist, the host
SKILL.md actually wires the new references, and the re-brand left no ClaudeKit
brand token or dead `.claude/skills|hooks/` path behind (red-team F1 landmine).

The brand-token scan uses word-boundary anchors on purpose: a raw `ck-` substring
would false-fire on legitimate English ("check-ins") and compound agent names
("fullstack-developer"). Only `\\bck:` / `\\bck-` are brand prefixes.
"""
import re
from pathlib import Path

import pytest  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]
_SKILLS = _REPO / "harness" / "plugins" / "hs" / "skills"

_FIX_WORKFLOWS = [
    "workflow-ci", "workflow-types", "workflow-ui", "workflow-logs",
    "workflow-quick", "workflow-standard", "workflow-deep",
]
_PORTED = (
    [_SKILLS / "fix" / "references" / f"{n}.md" for n in _FIX_WORKFLOWS]
    + [
        _SKILLS / "fix" / "references" / "skill-activation-matrix.md",
        _SKILLS / "team" / "references" / "agent-teams-official-docs.md",
        _SKILLS / "team" / "references" / "agent-teams-examples-and-best-practices.md",
        _SKILLS / "team" / "references" / "agent-teams-controls-and-modes.md",
        _SKILLS / "code-review" / "references" / "feedback-reception.md",
        _SKILLS / "security-scan" / "references" / "vulnerability-patterns.md",
        _SKILLS / "project-organization" / "references" / "markdown-body-templates.md",
        _SKILLS / "project-organization" / "references" / "directory-patterns.md",
    ]
)


@pytest.mark.dev_repo
def test_all_ported_files_exist():
    missing = [str(p.relative_to(_REPO)) for p in _PORTED if not p.exists()]
    assert not missing, "ported reference files missing:\n" + "\n".join(missing)


def test_fix_skill_wires_each_workflow_reference():
    body = (_SKILLS / "fix" / "SKILL.md").read_text(encoding="utf-8")
    unwired = [n for n in _FIX_WORKFLOWS if f"{n}.md" not in body]
    assert not unwired, "fix/SKILL.md does not reference: " + ", ".join(unwired)


def test_fix_skill_has_anti_rationalization_table():
    body = (_SKILLS / "fix" / "SKILL.md").read_text(encoding="utf-8")
    assert "Anti-Rationalization" in body
    assert "Seeing symptoms" in body, "anti-rationalization row marker missing"


def test_scan_dimensions_references_vulnerability_patterns():
    sd = _SKILLS / "security-scan" / "references" / "scan-dimensions.md"
    assert "vulnerability-patterns.md" in sd.read_text(encoding="utf-8")


# Assemble the dead-tree literals from parts so THIS guard file does not itself
# trip the no-claudekit-tree invariant (which bans the literal under harness/).
_DEAD = "." + "claude" + "/"
_BRAND = [
    re.compile(r"\bck:"),
    re.compile(r"\bck-"),
    re.compile(re.escape(_DEAD + "skills/")),
    re.compile(re.escape(_DEAD + "hooks/")),
]


def test_ported_set_is_rebranded_clean():
    offenders = []
    for p in _PORTED:
        if not p.exists():
            continue
        text = p.read_text(encoding="utf-8")
        for pat in _BRAND:
            for m in pat.finditer(text):
                # allow a `# learn:` citation line to carry a path (CI fence convention)
                line = text[text.rfind("\n", 0, m.start()) + 1: text.find("\n", m.start())]
                if line.lstrip().startswith("# learn:"):
                    continue
                offenders.append(f"{p.relative_to(_REPO)}: {m.group(0)!r} in {line.strip()[:80]!r}")
    assert not offenders, "brand/dead-path tokens survived the port:\n" + "\n".join(offenders)
