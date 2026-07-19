"""test_law_line_guard.py — Wave-1 law-line guard (F2, phase-1 of OUTER-HARNESS-E1).

A "law-line" (K6) is a line of actual normative directive in a rule file: non-blank,
outside any code-fence, not a table row (does not start with `|`), and outside the
`## Examples` section. `count_mechanisms` is a MECHANICAL PROXY (not a semantic
count) for how many distinct mechanisms a rule documents — it counts `### ` headings
in the law-body; gameable by an empty `### ` heading or under-counted by a rule using
`**bold**` instead, accepted in-scope because every E1 rule is authored with
`### `-per-mechanism by construction.

Cap: <=60 law-lines by default, <=90 when the rule documents >=4 mechanisms.

Scope discipline (R3, load-bearing): the guard checks ONLY the `E1_RULES` list below
-- never repo-wide. `harness/rules/agent-operational-discipline.md` is 114 lines /
5 mechanisms, already over the 90-line ceiling; a repo-wide sweep would red it on
day one for work this phase never touched (see `test_guard_scope_excludes_...`).
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RULES = _ROOT / "harness" / "rules"

# The net-new rules this phase (+ phase-2's core-Must wave) introduce. Deliberately
# NOT repo-wide (R3).
E1_RULES = [
    _RULES / "port-layering-and-capability-assignment.md",
    _RULES / "architectural-constraints.md",
    _RULES / "intake-and-interview-discipline.md",
    _RULES / "plain-language-phrasing.md",
    _RULES / "testability-triad.md",
    _RULES / "scope-and-contract-discipline.md",
    _RULES / "plan-quality-goodhart-premortem.md",
    _RULES / "scoring-rigor-contract.md",
]

_FENCE_MARK_RE = re.compile(r"^(```|~~~)")
_H2_RE = re.compile(r"^## ")
_EXAMPLES_H2_RE = re.compile(r"^## Examples\b")


def _effective_lines(text: str) -> list:
    """Lines outside any code-fence (``` OR ~~~, including a fence indented under a
    list item) and outside a `## Examples` section (excluded only up to the NEXT
    `## ` heading -- never to EOF, or a law section written after Examples would be
    silently under-counted and a too-long rule could slip past the cap)."""
    out = []
    in_fence = False
    in_examples = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if _FENCE_MARK_RE.match(stripped):
            in_fence = not in_fence
            continue  # the fence delimiter line itself is markup, never a law-line
        if in_fence:
            continue
        if _H2_RE.match(stripped):
            if _EXAMPLES_H2_RE.match(stripped):
                in_examples = True
                continue  # the "## Examples" heading line itself is part of the section
            in_examples = False  # any OTHER "## " heading ends the exclusion
        if in_examples:
            continue
        out.append(raw)
    return out


def count_law_lines(text: str) -> int:
    """Non-blank lines outside code-fence, not a `|` table row, outside Examples."""
    n = 0
    for line in _effective_lines(text):
        s = line.strip()
        if not s or s.startswith("|"):
            continue
        n += 1
    return n


def count_mechanisms(text: str) -> int:
    """Count of `### ` headings in the law-body -- mechanical proxy, see module
    docstring."""
    return sum(1 for line in _effective_lines(text) if line.strip().startswith("### "))


def _cap_for(text: str) -> int:
    return 90 if count_mechanisms(text) >= 4 else 60


# --- T1-T4c: pure-function behavior on synthetic fixtures --------------------------

def test_t1_short_rule_under_cap():
    text = "# Rule\n\nA law line.\nAnother law line.\n"
    assert count_law_lines(text) <= 60


def test_t2_70_lines_5_mechanisms_opens_90_tier():
    body_lines = ["# Rule", ""]
    for i in range(5):
        body_lines.append(f"### mechanism {i}")
        body_lines.extend(f"law line {i}-{j}" for j in range(13))
    text = "\n".join(body_lines)
    lines = count_law_lines(text)
    mechanisms = count_mechanisms(text)
    assert mechanisms >= 4
    assert lines <= 90, f"expected PASS under the 90-line tier, got {lines}"


def test_t3_70_lines_2_mechanisms_stays_capped_at_60():
    body_lines = ["# Rule", ""]
    for i in range(2):
        body_lines.append(f"### mechanism {i}")
        body_lines.extend(f"law line {i}-{j}" for j in range(34))
    text = "\n".join(body_lines)
    lines = count_law_lines(text)
    mechanisms = count_mechanisms(text)
    assert mechanisms < 4
    assert lines > 60, "fixture must actually exceed 60 to exercise the FAIL path"


def test_t4_fence_table_examples_excluded():
    text = (
        "# Rule\n\n"
        "law one\nlaw two\n"
        "```text\n" + "\n".join(f"fenced {i}" for i in range(10)) + "\n```\n"
        "| a | b |\n| --- | --- |\n" + "\n".join(f"| r{i} | v{i} |" for i in range(6)) + "\n"
        "## Examples\n" + "\n".join(f"example {i}" for i in range(5)) + "\n"
    )
    # "# Rule" + "law one" + "law two" are real law-lines; fence/table/Examples excluded
    assert count_law_lines(text) == 3


def test_t4b_tilde_fence_indented_under_list_not_counted():
    text = (
        "# Rule\n\n"
        "law one\n"
        "- an item\n"
        "  ~~~python\n"
        + "\n".join(f"  code {i}" for i in range(10))
        + "\n  ~~~\n"
        "law two\n"
    )
    assert count_law_lines(text) == 4  # "# Rule" + "law one" + "- an item" + "law two"


def test_t4c_examples_excludes_only_to_next_h2_not_eof():
    text = (
        "# Rule\n\n"
        "## Examples\n"
        "excluded example line 1\n"
        "excluded example line 2\n"
        "## More Law\n"
        + "\n".join(f"law after examples {i}" for i in range(5))
        + "\n"
    )
    # "# Rule" + "## More Law" heading + the 5 lines after it (not to EOF)
    assert count_law_lines(text) == 7


# --- T12: guard scope stays E1-only (R3) --------------------------------------------

def test_t12_guard_scope_excludes_pre_existing_rules():
    names = {p.name for p in E1_RULES}
    assert "agent-operational-discipline.md" not in names, (
        "guard scope must stay the explicit E1_RULES list -- a repo-wide sweep "
        "would red agent-operational-discipline.md (114 lines/5 mechanisms) on day one"
    )


# --- Real E1 rule files: RED until F4/F5 land, GREEN after -------------------------

@pytest.mark.parametrize("path", E1_RULES, ids=lambda p: p.name)
def test_e1_rule_within_law_line_budget(path):
    assert path.is_file(), f"E1 rule missing (write it as part of this phase): {path}"
    text = path.read_text(encoding="utf-8")
    lines = count_law_lines(text)
    mechanisms = count_mechanisms(text)
    cap = 90 if mechanisms >= 4 else 60
    assert lines <= cap, (
        f"{path.name}: {lines} law-lines exceeds cap {cap} (mechanisms={mechanisms})"
    )
