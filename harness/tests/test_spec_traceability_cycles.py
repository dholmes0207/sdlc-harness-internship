"""check_traceability.find_dep_cycles — complete cycle-membership via SCC.

The prior 3-color DFS (white/gray/black) marked a shared descendant BLACK
once the first branch that reached it found its back-edge, so a second
branch reaching the same descendant through a different path never got its
own back-edge re-examined -- an order-dependent under-count on any diamond
shape. `find_dep_cycles` is now built on an iterative Tarjan SCC pass (see
`_scc_tarjan`'s docstring) so membership is complete regardless of DFS visit
order, and `_cycles_through_start` enumerates every elementary cycle inside
each strongly-connected component.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_mods = load_skill_scripts(
    _SPEC_SCRIPTS, ["encoding_utils", "id_grammar", "frontmatter_parser", "spec_graph", "check_traceability"]
)
check_traceability = _mods["check_traceability"]


# ---------------------------------------------------------------------------
# The confirmed repro: a diamond where BOTH branches close a real cycle
# through the shared descendant D. MS-1->[MS-2,MS-3], MS-2->MS-4, MS-3->MS-4,
# MS-4->MS-1. All 4 nodes are mutually reachable (one SCC of size 4), and the
# diamond structurally contains exactly two elementary cycles:
# MS-1->MS-2->MS-4->MS-1 and MS-1->MS-3->MS-4->MS-1.
# ---------------------------------------------------------------------------

def test_find_dep_cycles_diamond_both_branches_emitted():
    adj = {
        "MS-1": ["MS-2", "MS-3"],
        "MS-2": ["MS-4"],
        "MS-3": ["MS-4"],
        "MS-4": ["MS-1"],
    }
    cycles = check_traceability.find_dep_cycles(adj)

    # Membership complete: every node appears in at least one cycle path.
    covered = set()
    for c in cycles:
        covered.update(c)
    assert covered == {"MS-1", "MS-2", "MS-3", "MS-4"}

    # Both elementary cycles are reported separately -- the old walk
    # under-reported the MS-3 branch entirely.
    as_sets = {frozenset(c[:-1]) for c in cycles}
    assert frozenset({"MS-1", "MS-2", "MS-4"}) in as_sets
    assert frozenset({"MS-1", "MS-3", "MS-4"}) in as_sets
    assert len(cycles) == 2

    # Every reported path is a closed path (starts == ends).
    for c in cycles:
        assert c[0] == c[-1]


def test_find_dep_cycles_diamond_scc_all_four_in_one_component():
    adj = {
        "MS-1": ["MS-2", "MS-3"],
        "MS-2": ["MS-4"],
        "MS-3": ["MS-4"],
        "MS-4": ["MS-1"],
    }
    sccs = check_traceability._scc_tarjan(adj)
    big = [s for s in sccs if len(s) > 1]
    assert len(big) == 1
    assert set(big[0]) == {"MS-1", "MS-2", "MS-3", "MS-4"}


# ---------------------------------------------------------------------------
# Preserve existing behavior: a self-loop is a cycle of one.
# ---------------------------------------------------------------------------

def test_find_dep_cycles_self_loop():
    adj = {"MS-1": ["MS-1"]}
    cycles = check_traceability.find_dep_cycles(adj)
    assert cycles == [["MS-1", "MS-1"]]


# ---------------------------------------------------------------------------
# A linear chain (no cycle) yields nothing -- no false positive from the SCC
# rewrite.
# ---------------------------------------------------------------------------

def test_find_dep_cycles_linear_chain_no_cycle():
    adj = {"MS-1": ["MS-2"], "MS-2": ["MS-3"], "MS-3": []}
    assert check_traceability.find_dep_cycles(adj) == []


# ---------------------------------------------------------------------------
# A dangling depends_on target (not itself a key in adj) is skipped, never
# raised on and never reported as a cycle member.
# ---------------------------------------------------------------------------

def test_find_dep_cycles_dangling_target_is_skipped_not_crashed():
    adj = {"MS-1": ["MS-999"]}
    assert check_traceability.find_dep_cycles(adj) == []


# ---------------------------------------------------------------------------
# Iterative, not recursive: a long linear chain must not RecursionError.
# ---------------------------------------------------------------------------

def test_find_dep_cycles_deep_chain_no_recursion_error():
    n = 3000
    adj = {f"N{i}": [f"N{i + 1}"] for i in range(n)}
    adj[f"N{n}"] = []
    assert check_traceability.find_dep_cycles(adj) == []


# ---------------------------------------------------------------------------
# A dense SCC (a complete digraph, worst case for elementary-cycle
# enumeration -- factorial blowup) must not be fully enumerated: it should
# return fast and report SCC membership as a single closed-path entry rather
# than hang on a combinatorial explosion.
# ---------------------------------------------------------------------------

def test_find_dep_cycles_dense_scc_reports_membership_not_full_enumeration():
    import time

    n = 12
    node_names = [f"N{i}" for i in range(n)]
    adj = {a: [b for b in node_names if b != a] for a in node_names}

    started = time.time()
    cycles = check_traceability.find_dep_cycles(adj)
    elapsed = time.time() - started

    assert elapsed < 5.0, "dense SCC enumeration must be bounded, not blow up"
    # One reported entry for the whole dense component -- not one per
    # elementary cycle (a complete digraph on 12 nodes has millions).
    assert len(cycles) == 1
    covered = set(cycles[0])
    assert covered >= set(node_names)


# ---------------------------------------------------------------------------
# A node-COUNT size gate would be density-BLIND -- a sparse 9-node ring
# (exactly one elementary cycle, trivially enumerable) would trip a size gate
# purely on node count and fall to the membership-only marker. The bound must
# be work/density-aware: a sparse ring of a realistic size fully enumerates
# its one real cycle instead of collapsing to the marker.
# ---------------------------------------------------------------------------

def test_find_dep_cycles_sparse_nine_ring_fully_enumerates_not_marker():
    n = 9
    node_names = [f"N{i}" for i in range(n)]
    adj = {node_names[i]: [node_names[(i + 1) % n]] for i in range(n)}

    cycles = check_traceability.find_dep_cycles(adj)

    assert len(cycles) == 1, "a sparse ring has exactly one elementary cycle"
    cycle = cycles[0]
    # Not the dense marker: a real closed path visits every node exactly
    # once before returning to its start, with no "too dense" note tacked on.
    assert not any("too" in str(x).lower() or "dense" in str(x).lower() for x in cycle)
    assert cycle[0] == cycle[-1]
    assert set(cycle[:-1]) == set(node_names)
    assert len(cycle) == n + 1


# ---------------------------------------------------------------------------
# A naive `len(cycles) >= limit` cap would report `stopped_early=True` the
# instant the Nth cycle is found, without checking whether the search had
# actually exhausted at that point. An SCC whose TRUE total elementary-cycle
# count is EXACTLY the cap must report complete enumeration, not "capped".
# ---------------------------------------------------------------------------

def test_find_dep_cycles_exact_cap_count_reports_complete_not_capped(monkeypatch):
    # SCC {A, B, C} with exactly 3 real elementary cycles: A-B-A, B-C-B, and
    # A-B-C-A (edges: A->B, B->A, B->C, C->B, C->A).
    adj = {
        "A": ["B"],
        "B": ["A", "C"],
        "C": ["A", "B"],
    }
    monkeypatch.setattr(check_traceability, "_MAX_CYCLES_PER_SCC", 3)

    cycles = check_traceability.find_dep_cycles(adj)

    # Complete enumeration -- 3 distinct closed paths, not one collapsed
    # dense-marker entry (which would report as a single membership path).
    assert len(cycles) == 3
    as_sets = {frozenset(c[:-1]) for c in cycles}
    assert frozenset({"A", "B"}) in as_sets
    assert frozenset({"B", "C"}) in as_sets
    assert frozenset({"A", "B", "C"}) in as_sets
    for c in cycles:
        assert c[0] == c[-1]
        assert not any("too" in str(x).lower() or "dense" in str(x).lower() for x in c)


# ---------------------------------------------------------------------------
# The genuinely-dense case must still fall to the marker, and fast -- the
# work/step budget (not node count) is what bounds a real combinatorial
# explosion. K15 is deliberately bigger than the old size gate would ever
# have let through unenumerated.
# ---------------------------------------------------------------------------

def test_find_dep_cycles_dense_k15_still_falls_to_marker_quickly():
    import time

    n = 15
    node_names = [f"N{i}" for i in range(n)]
    adj = {a: [b for b in node_names if b != a] for a in node_names}

    started = time.time()
    cycles = check_traceability.find_dep_cycles(adj)
    elapsed = time.time() - started

    assert elapsed < 1.0, "dense K15 must be bounded well under a second"
    assert len(cycles) == 1
    covered = set(cycles[0])
    assert covered >= set(node_names)
