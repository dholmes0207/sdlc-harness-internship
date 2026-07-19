"""Drift guard: every declared chain edge must appear in the handoff table.

`harness/data/skill-chains.yaml` is the structured DECLARATION of the SDLC
pipeline: an ordered list of `[src, dst]` skill pairs. `workflow-handoffs.md`
is the human-readable handoff table that is supposed to MATCH those chains
(the file says so in its own header). This guard parses both and asserts that
every declared chain edge is represented by some table row.

Matching is intentionally row-scoped rather than cell-exact: a single row may
document a transitive or composite handoff (e.g. the `hs:plan -> hs:cook`
chain is realised across the numbered rows 1..5, and the composite
`discover/research/triage/cook/predict -> bakeoff` row lists several sources
that each hand off to one target). A declared edge `src -> dst` is therefore
considered covered when a single table row mentions BOTH `src` and `dst` as
namespaced skill tokens, OR when the pair is reachable by chaining rows that
each mention a shared skill token (transitive coverage). This mirrors the
existing `test_handoff_deps_drift.py` philosophy: the most structured signal
available is the namespaced `hs:`/`hs-group:` reference, and a bare word is
ignored.
"""

import re
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
CHAINS_PATH = REPO_ROOT / "harness" / "data" / "skill-chains.yaml"
TABLE_PATH = REPO_ROOT / "harness" / "rules" / "workflow-handoffs.md"

# Matches a namespaced skill reference: hs:<name> or hs-<group>:<name>.
_REF = re.compile(r"\b(hs(?:-[a-z]+)?:[a-z][a-z0-9-]+)")


def _declared_edges():
    data = yaml.safe_load(CHAINS_PATH.read_text(encoding="utf-8"))
    edges = []
    for pair in data["chains"]:
        src, dst = pair[0], pair[1]
        edges.append((src, dst))
    return edges


def _row_token_sets():
    """Return a list of token sets, one per table row that mentions skills."""
    rows = []
    for line in TABLE_PATH.read_text(encoding="utf-8").splitlines():
        if not line.lstrip().startswith("|"):
            continue
        tokens = set(_REF.findall(line))
        if tokens:
            rows.append(tokens)
    return rows


def _reachable(src, dst, rows):
    """True if dst is reachable from src by chaining rows sharing skill tokens.

    Each row that mentions both ends of a hop counts; transitive coverage lets
    a numbered chain (1..5) cover a direct declared edge like plan -> cook.
    """
    # Build an adjacency over skill tokens: two tokens are linked if some row
    # lists both. Then a declared edge is covered iff src can reach dst.
    if src == dst:
        return True
    seen = {src}
    frontier = [src]
    while frontier:
        cur = frontier.pop()
        for tokens in rows:
            if cur in tokens:
                for nxt in tokens:
                    if nxt not in seen:
                        if nxt == dst:
                            return True
                        seen.add(nxt)
                        frontier.append(nxt)
    return dst in seen


def test_declared_edges_are_non_trivial():
    """Guard against a vacuous always-pass: real chains must exist."""
    edges = _declared_edges()
    assert len(edges) >= 15, "expected a substantial set of declared chain edges"


def test_table_rows_mention_skills():
    """Guard: the table parse actually finds namespaced skill rows."""
    rows = _row_token_sets()
    assert len(rows) >= 10, "expected many handoff rows mentioning skills"


def test_every_declared_chain_edge_is_in_the_table():
    """For each src->dst chain, the table must cover the handoff."""
    rows = _row_token_sets()
    missing = []
    for src, dst in _declared_edges():
        if not _reachable(src, dst, rows):
            missing.append(f"{src} -> {dst}")
    assert not missing, "declared chain edges absent from workflow-handoffs.md: " + ", ".join(missing)
