"""Tests for plan_graph.py — read a plan's machine-readable phase-DAG sidecar
(plan-graph.yaml) and derive cycle / ordering-hazard / parallel-batch findings.

Read-only, advisory, plan-time: it reports, it never edits the plan (red line #4 —
no AI auto-fix after approval). Edge direction is pinned: {from: A, to: B} means
"A runs BEFORE B" (A is B's prerequisite).
"""
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plan_graph as pg  # noqa: E402


def _mk(tmp_path: Path, body: str) -> Path:
    (tmp_path / "plan-graph.yaml").write_text(body, encoding="utf-8")
    return tmp_path


def test_parse_sidecar(tmp_path):
    p = _mk(tmp_path, "edges:\n  - {from: P1, to: P2}\nsubtasks:\n  P2: {files_to_modify: [a.py]}\n")
    g = pg.parse_phase_graph(p)
    assert {"from": "P1", "to": "P2"} in g["edges"]
    assert g["subtasks"]["P2"]["files_to_modify"] == ["a.py"]


def test_default_advisory_returns_zero_when_sidecar_missing(tmp_path):
    # Default mode stays advisory: a missing sidecar prints an error but never
    # blocks (return 0) — validate-time detection, not a gate.
    assert pg._main([str(tmp_path)]) == 0


def test_require_flag_exits_2_when_sidecar_missing(tmp_path):
    # --require turns the missing-sidecar finding into a hard non-zero exit, so a
    # cook preflight can refuse to start on a plan that skipped the sidecar.
    assert pg._main([str(tmp_path), "--require"]) == 2


def test_require_flag_returns_zero_when_sidecar_present(tmp_path):
    _mk(tmp_path, "edges: []\nsubtasks: {}\n")
    assert pg._main([str(tmp_path), "--require"]) == 0


def test_edge_direction_semantics(tmp_path):
    # {from: P1, to: P2} → adjacency P1 -> [P2] ("P1 prereq of P2"), not reversed.
    g = pg.parse_phase_graph(_mk(tmp_path, "edges:\n  - {from: P1, to: P2}\n"))
    adj = pg.build_adj(g)
    assert adj.get("P1") == ["P2"]
    assert "P1" not in adj.get("P2", [])


def test_find_cycles_detects_cycle(tmp_path):
    g = pg.parse_phase_graph(_mk(
        tmp_path, "edges:\n  - {from: P1, to: P2}\n  - {from: P2, to: P1}\n"))
    cycles = pg.find_cycles(g)
    assert cycles, "expected a cycle for P1<->P2"


def test_no_cycle_clean(tmp_path):
    g = pg.parse_phase_graph(_mk(
        tmp_path, "edges:\n  - {from: P1, to: P2}\n  - {from: P2, to: P3}\n"))
    assert pg.find_cycles(g) == []


def test_ordering_hazard_remove_before_create(tmp_path):
    # P1 (prereq) modifies x.py; P2 (depends on P1) creates x.py — P1 would edit a
    # file that does not exist yet. Hazard must name the exact (P1, P2, x.py).
    g = pg.parse_phase_graph(_mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n"
        "subtasks:\n"
        "  P1: {files_to_modify: [x.py]}\n"
        "  P2: {files_to_create: [x.py]}\n"))
    hz = pg.find_ordering_hazards(g)
    assert hz, "expected an ordering hazard"
    joined = " ".join(str(h) for h in hz)
    assert "P1" in joined and "P2" in joined and "x.py" in joined


def test_parallel_batches_diamond(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n  - {from: P1, to: P3}\n"
        "  - {from: P2, to: P4}\n  - {from: P3, to: P4}\n"))
    batches = pg.find_parallel_batches(g)
    assert batches == [["P1"], ["P2", "P3"], ["P4"]]


def test_parallel_batches_linear(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n  - {from: P2, to: P3}\n"))
    batches = pg.find_parallel_batches(g)
    assert batches == [["P1"], ["P2"], ["P3"]]


def test_parallel_conflict_shared_file(tmp_path):
    # P2 ∥ P3 (same batch) both touch code-review/SKILL.md → conflict naming the pair + file.
    g = pg.parse_phase_graph(_mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n  - {from: P1, to: P3}\n"
        "subtasks:\n"
        "  P2: {files_to_modify: [code-review/SKILL.md]}\n"
        "  P3: {files_to_modify: [code-review/SKILL.md]}\n"))
    conflicts = pg.find_parallel_conflicts(g)
    assert conflicts
    joined = " ".join(str(c) for c in conflicts)
    assert "P2" in joined and "P3" in joined and "code-review/SKILL.md" in joined


def test_parallel_no_conflict_disjoint(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n  - {from: P1, to: P3}\n"
        "subtasks:\n"
        "  P2: {files_to_modify: [a.py]}\n"
        "  P3: {files_to_modify: [b.py]}\n"))
    assert pg.find_parallel_conflicts(g) == []


def test_malformed_sidecar_no_crash(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path, "edges: [ {from: P1, to: :: bad yaml\n"))
    # must not raise; a clear error finding instead
    assert g.get("error")


def test_status_not_in_sidecar(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\nsubtasks:\n  P2: {status: done}\n"))
    warnings = pg.lint_no_status(g)
    assert warnings, "a status key in the sidecar must be warned (status lives in plan.md)"


# ---------------------------------------------------------- node_artifacts ---
# A node's per-phase artifact obligation is DECLARATIVE: `post: [name, ...]`.
# Default (unstated) post = ["verification-<node>.json"] — the implicit obligation
# the derive counter already assumed, so old sidecars behave identically. No `pre`
# this round (deferred — schema leaves room, nothing reads it).

def test_node_artifacts_explicit_post(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path,
        "subtasks:\n  P1: {post: [verification-P1.json]}\n"))
    assert pg.node_artifacts(g, "P1") == {"post": ["verification-P1.json"]}


def test_node_artifacts_default_post(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path,
        "subtasks:\n  P1: {files_to_modify: [a.py]}\n"))
    assert pg.node_artifacts(g, "P1") == {"post": ["verification-P1.json"]}


def test_node_artifacts_multi_post(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path,
        "subtasks:\n  P3: {post: [verification-P3.json, review-decision.json]}\n"))
    assert pg.node_artifacts(g, "P3") == {
        "post": ["verification-P3.json", "review-decision.json"]}


def test_node_artifacts_malformed_degrades(tmp_path):
    # post not a list-of-str → default, never raise.
    g = pg.parse_phase_graph(_mk(tmp_path, "subtasks:\n  P1: {post: x}\n"))
    assert pg.node_artifacts(g, "P1") == {"post": ["verification-P1.json"]}


def test_lint_warns_on_malformed_post(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path, "subtasks:\n  P1: {post: 123}\n"))
    warnings = pg.lint_no_status(g)
    assert any("post" in w for w in warnings), "malformed post must warn"
    # parse + lint must not crash, and node_artifacts still degrades safely
    assert pg.node_artifacts(g, "P1") == {"post": ["verification-P1.json"]}


def test_node_artifacts_runtime_default_when_post_absent(tmp_path):
    # node_artifacts is the RUNTIME reader (derive/artifact_check call it on an
    # already-approved graph). It keeps a defensive default so a slipped-through
    # node resolves instead of crashing cook — this is NOT an authoring allowance:
    # the _main gate (test_main_exits_2_on_missing_post) refuses missing post upfront.
    g = pg.parse_phase_graph(_mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n"
        "subtasks:\n  P1: {files_to_modify: [a.py]}\n  P2: {files_to_create: [b.py]}\n"))
    assert pg.node_artifacts(g, "P1") == {"post": ["verification-P1.json"]}
    assert pg.node_artifacts(g, "P2") == {"post": ["verification-P2.json"]}


def test_no_pre_key_read(tmp_path):
    # `pre` is deferred — node_artifacts must NOT surface a "pre" key even if authored.
    g = pg.parse_phase_graph(_mk(tmp_path,
        "subtasks:\n  P1: {pre: [setup.json], post: [verification-P1.json]}\n"))
    out = pg.node_artifacts(g, "P1")
    assert "pre" not in out
    assert out == {"post": ["verification-P1.json"]}


# --- post is MANDATORY at authoring (hardened): the _main gate refuses a
# sidecar whose any node omits an explicit, valid `post`. Hard-fail (exit 2) in BOTH
# advisory and --require modes — a structural contract violation, not a heuristic
# advisory like cycles/conflicts. Old completed plans are out of scope (never re-gated).

def test_find_missing_post_lists_nodes_without_explicit_post(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n"
        "subtasks:\n"
        "  P1: {files_to_modify: [a.py], post: [verification-P1.json]}\n"
        "  P2: {files_to_modify: [b.py]}\n"))               # P2 omits post
    assert pg.find_missing_post(g) == ["P2"]


def test_find_missing_post_flags_malformed_post(tmp_path):
    # present-but-invalid post (not a list-of-str) is also "missing" for the gate.
    g = pg.parse_phase_graph(_mk(tmp_path,
        "subtasks:\n  P1: {post: 123}\n"))
    assert pg.find_missing_post(g) == ["P1"]


def test_find_missing_post_empty_when_all_declared(tmp_path):
    g = pg.parse_phase_graph(_mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n"
        "subtasks:\n"
        "  P1: {post: [verification-P1.json]}\n"
        "  P2: {post: [verification-P2.json]}\n"))
    assert pg.find_missing_post(g) == []


def test_main_exits_2_on_missing_post_advisory_mode(tmp_path, capsys):
    _mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n"
        "subtasks:\n"
        "  P1: {post: [verification-P1.json]}\n"
        "  P2: {files_to_modify: [b.py]}\n")                # P2 omits post
    rc = pg._main([str(tmp_path)])                          # no --require, still hard-fails
    assert rc == 2
    assert "missing-post" in capsys.readouterr().out


def test_main_passes_when_all_post_present(tmp_path):
    _mk(tmp_path,
        "edges:\n  - {from: P1, to: P2}\n"
        "subtasks:\n"
        "  P1: {post: [verification-P1.json]}\n"
        "  P2: {post: [verification-P2.json]}\n")
    assert pg._main([str(tmp_path)]) == 0


def test_main_empty_sidecar_has_no_missing_post(tmp_path):
    # no nodes → nothing to require → not a missing-post failure (stays 0).
    _mk(tmp_path, "edges: []\nsubtasks: {}\n")
    assert pg._main([str(tmp_path)]) == 0
