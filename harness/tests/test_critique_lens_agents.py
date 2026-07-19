"""test_critique_lens_agents.py — lens wiring integrity for hs:critique.

Every lens named in critique.yaml must resolve to a real agent file whose
frontmatter `name:` matches the slug — a lens with no backing agent is a broken
fan-out (the skill would spawn an agent that does not exist).

Also pins the two product-thinking lenses adapted from product-spec's critics
(neutral tone, no spec-graph machinery, no 9-level voice) into the
product-bearing artifact types, and keeps them OUT of raw code/diff where
product/market framing has nothing to bite on.
"""
import re
from pathlib import Path

import yaml

_HARNESS = Path(__file__).resolve().parent.parent
_AGENTS = _HARNESS / "plugins" / "hs" / "agents"
_CRITIQUE = _HARNESS / "data" / "critique.yaml"

NEW_LENSES = ("product-value-critic", "market-fit-critic")
PRODUCT_BEARING = ("plan", "decision", "design")
NOT_PRODUCT = ("code", "diff")


def _lenses():
    raw = yaml.safe_load(_CRITIQUE.read_text(encoding="utf-8"))
    return raw.get("lenses", {})


def _agent_name(slug):
    f = _AGENTS / f"{slug}.md"
    if not f.is_file():
        return None
    head = f.read_text(encoding="utf-8")[:2000]
    m = re.search(r"^name:\s*(.+?)\s*$", head, re.MULTILINE)
    return m.group(1).strip() if m else None


def test_every_lens_name_resolves_to_agent():
    names = {n for lst in _lenses().values() for n in lst}
    missing = [n for n in names if _agent_name(n) != n]
    assert not missing, f"lens names without a matching agent file: {missing}"


def test_new_lenses_wired_into_product_bearing_types():
    lenses = _lenses()
    for t in PRODUCT_BEARING:
        for slug in NEW_LENSES:
            assert slug in lenses.get(t, []), f"{slug} not wired into '{t}'"


def test_new_lenses_kept_out_of_code_and_diff():
    lenses = _lenses()
    for t in NOT_PRODUCT:
        for slug in NEW_LENSES:
            assert slug not in lenses.get(t, []), f"{slug} wrongly in '{t}'"
