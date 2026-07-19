"""test_derive_plan_completion.py — P2: derive "plan done" from per-phase
evidence snapshots vs the plan-graph node count.

complete iff the count of DISTINCT phases with a PASS snapshot whose id is a
real plan-graph node >= N (N = number of plan-graph nodes). Pure read, fails
SAFE: a missing sidecar or missing snapshots is incomplete, never complete. The
canonical verification.json (no -<phase> suffix) is never counted as a phase.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import derive_plan_completion as dpc  # noqa: E402


def _seed(plan_dir, nodes=("P1", "P2", "P3"), graph=True):
    (plan_dir / "artifacts").mkdir(parents=True)
    if graph:
        edges = "\n".join(
            "  - {from: %s, to: %s}" % (a, b)
            for a, b in zip(nodes, nodes[1:])) or "  []"
        (plan_dir / "plan-graph.yaml").write_text("edges:\n" + edges + "\n",
                                                  encoding="utf-8")


def _snap(plan_dir, phase, verdict="PASS"):
    rec = {"stage": "cook", "plan": plan_dir.name, "actor": "user:x",
           "ts": "2026-06-28T00:00:00+00:00",
           "checks": [{"name": "unit", "status": "PASS"}],
           "verdict": verdict, "phase": phase}
    (plan_dir / "artifacts" / ("verification-%s.json" % phase)).write_text(
        json.dumps(rec), encoding="utf-8")


def test_all_phases_pass_complete(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p)
    for ph in ("P1", "P2", "P3"):
        _snap(p, ph)
    assert dpc.is_complete(p) is True


def test_partial_incomplete(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p)
    _snap(p, "P1"); _snap(p, "P2")
    st = dpc.completion_state(p)
    assert st["complete"] is False
    assert st["n_total"] == 3
    assert len(st["passed_phases"]) == 2


def test_pass_with_risk_counts(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p)
    _snap(p, "P1"); _snap(p, "P2"); _snap(p, "P3", verdict="PASS_WITH_RISK")
    assert dpc.is_complete(p) is True


def test_blocked_file_ignored(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p)
    _snap(p, "P1"); _snap(p, "P2"); _snap(p, "P3", verdict="BLOCKED")
    assert dpc.is_complete(p) is False


def test_missing_snapshots_incomplete(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p)
    assert dpc.is_complete(p) is False


def test_missing_plangraph_incomplete(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, graph=False)
    for ph in ("P1", "P2", "P3"):
        _snap(p, ph)
    st = dpc.completion_state(p)
    assert st["complete"] is False
    assert "plan-graph" in st["reason"]


def test_phase_not_in_graph_ignored(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p)
    _snap(p, "P1"); _snap(p, "P2"); _snap(p, "PX")  # PX not a node
    st = dpc.completion_state(p)
    assert st["complete"] is False
    assert "PX" not in st["passed_phases"]
    assert len(st["passed_phases"]) == 2


def test_canonical_verification_not_counted(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p)
    # only the canonical verification.json present (no -<phase> snapshots)
    rec = {"stage": "cook", "plan": p.name, "actor": "user:x",
           "ts": "2026-06-28T00:00:00+00:00",
           "checks": [{"name": "unit", "status": "PASS"}],
           "verdict": "PASS", "phase": "P1"}
    (p / "artifacts" / "verification.json").write_text(json.dumps(rec),
                                                       encoding="utf-8")
    st = dpc.completion_state(p)
    assert st["passed_phases"] == set()
    assert st["complete"] is False


def test_corrupt_file_skipped(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p)
    _snap(p, "P1"); _snap(p, "P2"); _snap(p, "P3")
    (p / "artifacts" / "verification-junk.json").write_text("{not json",
                                                            encoding="utf-8")
    assert dpc.is_complete(p) is True  # corrupt one skipped, 3 good remain


def test_more_passed_than_n_still_complete(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, nodes=("P1", "P2"))  # N = 2
    _snap(p, "P1"); _snap(p, "P2")
    assert dpc.is_complete(p) is True


def test_completion_state_shape(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p)
    _snap(p, "P1")
    st = dpc.completion_state(p)
    assert set(st) >= {"n_total", "passed_phases", "complete", "reason"}
    assert isinstance(st["passed_phases"], set)
    assert isinstance(st["complete"], bool)


def test_phase_from_filename_when_field_absent(tmp_path):
    """A snapshot whose content omits `phase` still counts via its filename."""
    p = tmp_path / "plans" / "x"
    _seed(p)
    for ph in ("P1", "P2"):
        _snap(p, ph)
    rec = {"stage": "cook", "plan": p.name, "actor": "user:x",
           "ts": "2026-06-28T00:00:00+00:00",
           "checks": [{"name": "unit", "status": "PASS"}], "verdict": "PASS"}
    (p / "artifacts" / "verification-P3.json").write_text(json.dumps(rec),
                                                          encoding="utf-8")
    assert dpc.is_complete(p) is True


# ---------------------------------------------------- P2: declarative post ---
# The counter now reads each node's declared `post` (via plan_graph.node_artifacts)
# instead of a hard-coded verification- prefix. Default post = [verification-<node>.json]
# so plans that never authored `post` behave identically (back-compat). A post that
# names a non-verification artifact (review-decision.json) is checked for presence
# only; a verification-*.json post keeps the verdict gate.

def _seed_graph(plan_dir, yaml_body):
    (plan_dir / "artifacts").mkdir(parents=True)
    (plan_dir / "plan-graph.yaml").write_text(yaml_body, encoding="utf-8")


def _write(plan_dir, name, obj):
    (plan_dir / "artifacts" / name).write_text(json.dumps(obj), encoding="utf-8")


def test_default_post_back_compat(tmp_path):
    # No `post` declared anywhere → default [verification-<node>.json]; a PASS
    # snapshot completes exactly as the old prefix logic did.
    p = tmp_path / "plans" / "x"
    _seed_graph(p, "subtasks:\n  P1: {files_to_modify: [a.py]}\n")
    _snap(p, "P1")
    assert dpc.is_complete(p) is True


def test_present_but_fail_not_complete(tmp_path):
    # default-post node whose verification snapshot is FAIL → verdict gate keeps it
    # incomplete (not lowered to mere presence).
    p = tmp_path / "plans" / "x"
    _seed_graph(p, "subtasks:\n  P1: {files_to_modify: [a.py]}\n")
    _snap(p, "P1", verdict="FAIL")
    st = dpc.completion_state(p)
    assert st["complete"] is False
    assert st["passed_phases"] == set()


def test_multi_post_requires_all(tmp_path):
    # node declares two posts; missing the second → not complete; add it → complete.
    p = tmp_path / "plans" / "x"
    _seed_graph(p, "subtasks:\n  P1: {post: [verification-P1.json, review-decision.json]}\n")
    _snap(p, "P1")  # only verification-P1.json present
    assert dpc.is_complete(p) is False
    _write(p, "review-decision.json", {"verdict": "PASS"})
    assert dpc.is_complete(p) is True


def test_non_verification_post_presence_only(tmp_path):
    # a non-verification post (no verdict field) counts on presence alone.
    p = tmp_path / "plans" / "x"
    _seed_graph(p, "subtasks:\n  P1: {post: [review-decision.json]}\n")
    assert dpc.is_complete(p) is False
    _write(p, "review-decision.json", {"some": "payload"})  # no verdict
    assert dpc.is_complete(p) is True


def test_malformed_graph_incomplete_safe(tmp_path):
    p = tmp_path / "plans" / "x"
    (p / "artifacts").mkdir(parents=True)
    (p / "plan-graph.yaml").write_text("edges: [ {from: P1, to: :: bad\n",
                                       encoding="utf-8")
    st = dpc.completion_state(p)
    assert st["complete"] is False
    assert "plan-graph" in st["reason"]


def test_n_total_from_nodes(tmp_path):
    # N is the node count, not the snapshot-file count: extra verification files for
    # non-nodes do not change N.
    p = tmp_path / "plans" / "x"
    _seed(p, nodes=("P1", "P2"))
    _snap(p, "P1"); _snap(p, "P2"); _snap(p, "PX")  # PX not a node
    st = dpc.completion_state(p)
    assert st["n_total"] == 2
    assert st["complete"] is True
