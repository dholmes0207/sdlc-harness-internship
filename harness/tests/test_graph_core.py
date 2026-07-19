"""Tests for the domain-agnostic graph primitives in graph_core.

graph_core holds the generic graph helpers shared by the standards builder and
its structural checks — closure walk, parent/child adjacency, the expected-child
counter, the iterative dependency-cycle finder, the finding constructor, scalar
coercions, snapshot/diff/changed-node math, and the id-grammar framework. The
behavior mirrors the equivalents in the frozen spec_graph port (same inputs →
same outputs) but the file is independent: it imports nothing from spec_graph
and spec_graph imports nothing from it.
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import graph_core  # noqa: E402


# ---------- closure walk ----------

def test_closure_excludes_start_and_is_cycle_safe():
    # self-edge on A, plus a 3-node cycle A->B->C->A
    adj = {"A": ["A", "B"], "B": ["C"], "C": ["A"]}
    out = graph_core._closure(adj, "A")
    # terminates (no hang on the cycle) and never reports start as its own descendant
    assert "A" not in out
    assert out == {"B", "C"}


# ---------- parent / child adjacency ----------

def test_parents_children_adjacency():
    # edge convention: from=child, to=parent
    graph = {
        "edges": [
            {"from": "S1", "to": "E1", "kind": "rule_group"},
            {"from": "E1", "to": "A1", "kind": "std_area"},
            {"from": "A1", "to": "A1", "kind": "self"},  # self-edge dropped
            {"from": "S2", "to": "E1", "kind": "rule_group"},
        ]
    }
    children = graph_core.children_of(graph)
    parents = graph_core.parents_of(graph)
    assert sorted(children["E1"]) == ["S1", "S2"]
    assert "E1" in children["A1"]
    assert parents["S1"] == ["E1"]
    assert parents["E1"] == ["A1"]
    # self-edge dropped on the parent side: A1 is not its own parent
    assert "A1" not in parents.get("A1", [])


# ---------- expected-child counting with an injected hierarchy map ----------

def test_matching_child_counts_param_map():
    child_type_for_parent = {"arch_goal": "std_area", "std_area": "rule_group"}
    graph = {
        "nodes": [
            {"id": "ARCH-G1", "type": "arch_goal"},
            {"id": "STD-A", "type": "std_area"},
            {"id": "STD-A-RG1", "type": "rule_group"},
            {"id": "STD-A-RG1-R1", "type": "rule"},
        ],
        "edges": [
            {"from": "STD-A", "to": "ARCH-G1"},        # expected std_area←arch_goal
            {"from": "STD-A-RG1", "to": "STD-A"},      # expected rule_group←std_area
            {"from": "STD-A-RG1-R1", "to": "STD-A"},   # WRONG type for std_area, ignored
        ],
    }
    counts = graph_core.matching_child_counts(graph, child_type_for_parent)
    assert counts.get("ARCH-G1") == 1
    assert counts.get("STD-A") == 1  # the rule pointing at std_area does not count


# ---------- iterative dependency-cycle finder ----------

def test_find_dep_cycles_iterative():
    # ~2000-deep linear chain must not raise RecursionError and has no cycle
    chain = {f"N{i}": [f"N{i + 1}"] for i in range(2000)}
    chain["N2000"] = []
    assert graph_core.find_dep_cycles(chain) == []

    # a 3-node back-edge returns the closed path
    cyc = {"A": ["B"], "B": ["C"], "C": ["A"]}
    cycles = graph_core.find_dep_cycles(cyc)
    assert cycles
    path = cycles[0]
    assert path[0] == path[-1]  # closed
    assert set(path) == {"A", "B", "C"}


# ---------- finding constructor + sentinel hygiene ----------

def test_make_finding_sentinel_hygiene():
    node = {"id": "<invalid-id>", "file": "areas/STD-X.md"}
    f = graph_core.make_finding("invalid_id", "error", node,
                                "id <invalid-id> is malformed", ref="x")
    assert f["artifact_id"] is None
    assert "<invalid-id>" not in f["detail"]
    assert "areas/STD-X.md" in f["detail"]
    assert f["context"] == {"ref": "x"}

    good = {"id": "STD-A", "file": "areas/STD-A.md"}
    f2 = graph_core.make_finding("orphan", "error", good, "no parent")
    assert f2["artifact_id"] == "STD-A"
    assert f2["context"] is None


# ---------- scalar coercions ----------

def test_scalar_coercions():
    assert graph_core._scalar_id("STD-A") == "STD-A"
    assert graph_core._scalar_id(None) == "<missing-id>"
    assert graph_core._scalar_id("") == "<missing-id>"
    assert graph_core._scalar_id([1, 2]) == "<invalid-id>"
    assert graph_core._scalar_id({"a": 1}) == "<invalid-id>"

    assert graph_core._scalar_link("STD-A") == "STD-A"
    assert graph_core._scalar_link(["x"]) is None
    assert graph_core._scalar_link(None) is None

    assert graph_core._as_id_list(["B", "A", "A"]) == ["A", "A", "B"]  # no dedup
    assert graph_core._as_id_list(["B", "A"]) == ["A", "B"]
    assert graph_core._as_id_list("PRD-2") == []  # never char-split
    assert graph_core._as_id_list(None) == []
    assert graph_core._as_id_list({"a": 1}) == []
    # mixed list keeps only the strings, sorted
    assert graph_core._as_id_list(["B", 3, "A"]) == ["A", "B"]


# ---------- changed_nodes: unknown-on-one-side is not a change ----------

def test_changed_nodes_unknown_not_change():
    cur = {"nodes": [{"id": "X", "status": "draft", "body_hash": "aaaa"}]}
    # body_hash absent on the baseline → unknown, not a change
    prev = {"nodes": [{"id": "X", "status": "draft"}]}
    assert graph_core.changed_nodes(cur, prev) == []

    # a differing tracked field IS a change
    cur2 = {"nodes": [{"id": "X", "status": "approved", "body_hash": "aaaa"}]}
    prev2 = {"nodes": [{"id": "X", "status": "draft", "body_hash": "aaaa"}]}
    assert graph_core.changed_nodes(cur2, prev2) == ["X"]

    assert "status" in graph_core.CHANGED_FIELDS
    assert "body_hash" in graph_core.CHANGED_FIELDS
    assert "content_hash" in graph_core.CHANGED_FIELDS


# ---------- snapshot writer: dir injected, idempotent ----------

def test_write_snapshot_dir_injected(tmp_path):
    graph = {"generated_at": "20260613T120000Z", "nodes": [{"id": "STD-A"}], "edges": []}
    snap_dir = tmp_path / "snaps"
    p1 = graph_core.write_snapshot(graph, snap_dir)
    p2 = graph_core.write_snapshot(graph, snap_dir)
    assert p1.parent == snap_dir
    assert p1 == p2  # same content → same filename (idempotent)
    assert p1.exists()


# ---------- diff_graphs: added/removed plus injected scalar fields ----------

def test_diff_graphs_added_removed_and_scalar_fields():
    cur = {"nodes": [{"id": "A"}, {"id": "B"}], "meta": {"name": "v2"}}
    base = {"nodes": [{"id": "A"}, {"id": "C"}], "meta": {"name": "v1"}}
    d = graph_core.diff_graphs(cur, base, scalar_fields=("name",), meta_key="meta")
    assert d["added"] == ["B"]
    assert d["removed"] == ["C"]
    assert "name" in d["scalar_changes"]


def test_diff_and_changed_skip_id_less_foreign_nodes():
    # changed_nodes/diff_graphs document foreign/pre-upgrade snapshots as valid input,
    # so a node lacking 'id' (hand-edited or future-format) must be SKIPPED, never crash
    # the delta with a KeyError.
    cur = {"nodes": [{"id": "A"}, {"name": "no-id"}], "meta": {"name": "v2"}}
    base = {"nodes": [{"id": "A"}], "meta": {"name": "v1"}}
    d = graph_core.diff_graphs(cur, base, scalar_fields=("name",), meta_key="meta")
    assert d["added"] == [] and d["removed"] == []   # only A on both sides; id-less skipped
    assert "name" in d["scalar_changes"]
    cur2 = {"nodes": [{"id": "A", "status": "approved"}, {"status": "x"}]}
    prev2 = {"nodes": [{"id": "A", "status": "draft"}]}
    assert graph_core.changed_nodes(cur2, prev2) == ["A"]


# ---------- id-grammar framework ----------

def test_id_grammar_findings():
    import re
    pattern_by_type = {
        "arch_goal": re.compile(r"^ARCH-G[0-9]+$"),
        "std_area": re.compile(r"^STD-[A-Z][A-Z0-9-]{0,15}$"),
    }
    nodes = [
        {"id": "ARCH-G1", "type": "arch_goal", "file": "charter.md"},   # valid
        {"id": "garbage id", "type": "std_area", "file": "areas/x.md"},  # invalid
        {"id": "<missing-id>", "type": "std_area", "file": "areas/y.md"},  # sentinel
    ]
    findings = graph_core.id_grammar_findings(nodes, pattern_by_type)
    checks = [f["check"] for f in findings]
    assert checks.count("invalid_id") == 1  # only the garbage id, sentinel not double-reported
    bad = [f for f in findings if f["check"] == "invalid_id"][0]
    # sentinel hygiene leaves valid garbage id intact on a real id
    assert bad["artifact_id"] in ("garbage id", None)
