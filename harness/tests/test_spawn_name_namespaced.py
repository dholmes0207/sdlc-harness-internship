"""Contract: every reference to an advisory/worker agent-TYPE in the doc surface
the model reads carries the `hs:` plugin prefix.

The Agent tool registry only exposes namespaced types (`hs:code-reviewer`); a bare
`code-reviewer` passed as `subagent_type` resolves to nothing ("Agent type not
found"). So any backticked catalog name (`` `researcher` ``) or bare
`subagent_type="researcher"` in a skill/agent/rule doc is a latent spawn defect —
the fix is the prefix, and adding it never changes prose meaning.

Exempt by construction (NOT type references):
  - file paths — `harness/plugins/hs/agents/code-reviewer.md` never contains the
    substring `` `code-reviewer` `` (the name there is not itself backtick-wrapped);
  - agent-definition frontmatter `name: code-reviewer` — not backticked, so unmatched.
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]

# Real subagent_type stems — the file stems under harness/plugins/hs/agents/.
_CATALOG = [
    "brainstormer", "code-reviewer", "code-simplifier", "critique-consolidator",
    "debugger", "decision-reconciler", "developer", "docs-manager", "git-manager",
    "independent-revalidator", "journal-writer", "market-fit-critic", "planner",
    "product-value-critic", "project-manager", "red-teamer", "researcher",
    "tester", "ui-ux-designer", "workflow-orchestrator",
]

# The doc surface the model consults when deciding a subagent_type.
_DIRS = [
    _ROOT / "harness" / "plugins" / "hs" / "skills",
    _ROOT / "harness" / "plugins" / "hs" / "agents",
    _ROOT / "harness" / "rules",
]


def _docs():
    docs = []
    for d in _DIRS:
        docs.extend(sorted(d.rglob("*.md")))
    return docs


def _bare_type_hits(text):
    """Backticked bare catalog names + bare subagent_type= literals, with line no."""
    hits = []
    for i, line in enumerate(text.splitlines(), 1):
        for name in _CATALOG:
            if f"`{name}`" in line:
                hits.append((i, f"`{name}`"))
            if f'subagent_type="{name}"' in line or f"subagent_type='{name}'" in line:
                hits.append((i, f'subagent_type="{name}"'))
    return hits


@pytest.mark.dev_repo
@pytest.mark.parametrize("doc", _docs(), ids=lambda p: str(p.relative_to(_ROOT)))
def test_no_bare_advisory_spawn(doc):
    hits = _bare_type_hits(doc.read_text(encoding="utf-8"))
    assert not hits, (
        f"{doc.relative_to(_ROOT)}: bare agent-type reference(s) need the hs: prefix: {hits}"
    )
