"""test_rule_route_existence.py — Wave-1 route-existence guard (F3, phase-1 of
OUTER-HARNESS-E1).

Two directions, both via `extract_rule_refs` (F2b, `_rule_ref_util.py`) -- NEVER a
raw substring match (red-team M2: raw vs fence-stripped extraction diverging lets a
route-line hidden in a code fence pass here and only fail later at CS-10/phase-3):

  (i)  each `E1_RULES` file is cited (fence-stripped) in >=1 SKILL.md body under
       `harness/plugins/hs/skills/*/SKILL.md`;
  (ii) every `harness/rules/*.md` reference cited (fence-stripped) in any SKILL.md
       body points to a file that actually exists.

Because F3 and the phase-3 CS-10 extension share the SAME extractor, a route-line
written as prose is visible to both and one written only inside a fence is invisible
to both -- there is exactly one extractor to keep honest, not two to drift apart
(K11, red-team M2 PROVEN).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RULES = _ROOT / "harness" / "rules"
_SKILLS = _ROOT / "harness" / "plugins" / "hs" / "skills"

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
from _rule_ref_util import extract_rule_refs  # noqa: E402

# Same E1 scope as the law-line guard (test_law_line_guard.E1_RULES) -- kept as its
# own list here (not imported) so this file's RED/GREEN state is self-contained.
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


def _all_skill_bodies() -> dict:
    return {
        p: p.read_text(encoding="utf-8")
        for p in sorted(_SKILLS.glob("*/SKILL.md"))
    }


def _cited_rule_refs() -> dict:
    """{ref_string: {citing SKILL.md paths}} across the whole skill tree, fence-stripped."""
    citations: dict = {}
    for path, body in _all_skill_bodies().items():
        for ref in extract_rule_refs(body):
            citations.setdefault(ref, set()).add(path)
    return citations


# --- Direction (i): every E1 rule is cited by >=1 SKILL.md body --------------------

@pytest.mark.parametrize("rule_path", E1_RULES, ids=lambda p: p.name)
def test_e1_rule_is_cited_by_at_least_one_skill(rule_path):
    assert rule_path.is_file(), f"E1 rule missing (write it as part of this phase): {rule_path}"
    citations = _cited_rule_refs()
    ref_key = f"harness/rules/{rule_path.name}"
    assert ref_key in citations, (
        f"{rule_path.name} is not cited (fence-stripped) by any SKILL.md body -- "
        f"add a prose route-line OUTSIDE any code fence"
    )


# --- Direction (ii): every cited harness/rules/*.md ref resolves to a real file ---

def test_every_cited_rule_ref_resolves_to_a_real_file():
    citations = _cited_rule_refs()
    broken = []
    for ref, citing_paths in citations.items():
        rel = ref[len("harness/rules/"):]
        if not (_RULES / rel).is_file():
            broken.append((ref, sorted(str(p) for p in citing_paths)))
    assert not broken, f"broken harness/rules/*.md reference(s): {broken}"


# --- T7: a broken ref would be caught (proves the check, not just current state) ---

def test_t7_broken_ref_is_detected_synthetic():
    body = "route: harness/rules/ghost-rule-that-does-not-exist.md\n"
    refs = extract_rule_refs(body)
    assert refs, "extractor found no ref in the fixture body"
    ref = next(iter(refs))
    rel = ref[len("harness/rules/"):]
    assert not (_RULES / rel).is_file(), "fixture must name a rule that does not exist"


# --- T7b: a fenced route-line is invisible to direction (i) ------------------------

def test_t7b_fenced_route_line_is_invisible_to_extraction():
    fenced_body = (
        "# fixture skill\n\n"
        "```text\n"
        "See harness/rules/port-layering-and-capability-assignment.md\n"
        "```\n"
    )
    refs = extract_rule_refs(fenced_body)
    assert "harness/rules/port-layering-and-capability-assignment.md" not in refs, (
        "a route-line inside a code fence must not satisfy direction (i) -- this "
        "is what forces cook to place the real route-line as prose"
    )


def test_t6_unfenced_route_line_is_visible_to_extraction():
    prose_body = (
        "# fixture skill\n\n"
        "See harness/rules/port-layering-and-capability-assignment.md for the tiers.\n"
    )
    refs = extract_rule_refs(prose_body)
    assert "harness/rules/port-layering-and-capability-assignment.md" in refs
