#!/usr/bin/env python3
"""derive_plan_completion.py — derive "this plan finished all its phases" from
per-phase evidence snapshots, deterministically and fail-SAFE.

The canonical verification.json is overwritten every phase, so it cannot answer
"are all N phases done?". phase_progress_writer keeps a per-phase copy
(verification-<phase>.json on a PASS). This module counts the DISTINCT phases
with a PASS snapshot whose id is a real plan-graph node, and calls the plan
complete only when that count reaches N (N = number of plan-graph nodes).

Why this shape is safe by construction:
  * complete needs N distinct node-phases — a single phase can never reach N, so
    closing after phase 1 of N is structurally impossible.
  * a missing sidecar, missing snapshots, or a corrupt file all reduce the count
    -> incomplete. Every degradation is an UNDER-count: it can delay a close, it
    can never cause an early one.
  * pure read: no writes, no network. Snapshots are an on-disk derived cache; the
    source of truth stays the committed frontmatter status + canonical verification.

Contract:
    is_complete(plan_dir, root=None) -> bool
    completion_state(plan_dir, root=None) -> dict
        {n_total, passed_phases:set, complete:bool, reason:str}
"""
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
import plan_graph  # noqa: E402

_PASS_VERDICTS = {"PASS", "PASS_WITH_RISK"}
_PREFIX = "verification-"
_SUFFIX = ".json"


def _resolve_plan_dir(plan_dir, root):
    pd = Path(plan_dir)
    if root is not None and not pd.is_absolute():
        pd = Path(root) / pd
    return pd


def _is_verification_post(name: str) -> bool:
    """True for a post artifact named like a verification snapshot
    (verification-<id>.json) — those keep the verdict gate. Any other declared
    artifact (e.g. review-decision.json) is checked for presence only."""
    return name.startswith(_PREFIX) and name.endswith(_SUFFIX) and len(name) > len(_PREFIX) + len(_SUFFIX)


def _node_post_satisfied(art: Path, post_list) -> bool:
    """A node is satisfied when EVERY artifact it declares in `post` is present
    under artifacts/. A verification-*.json post additionally requires verdict in
    {PASS, PASS_WITH_RISK} (the verdict gate is preserved, never lowered to mere
    presence). A corrupt/unreadable verification file fails its node — an
    under-count is the safe direction."""
    for name in post_list:
        f = art / name
        if not f.is_file():
            return False
        if _is_verification_post(name):
            try:
                rec = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return False
            if not isinstance(rec, dict) or rec.get("verdict") not in _PASS_VERDICTS:
                return False
    return True


def completion_state(plan_dir, root=None) -> dict:
    pd = _resolve_plan_dir(plan_dir, root)

    graph = plan_graph.parse_phase_graph(pd)
    if "error" in graph:
        # Sidecar is a MANDATORY plan artifact; its absence is anomalous, not a
        # reason to guess "done". Fail safe -> incomplete.
        return {"n_total": 0, "passed_phases": set(), "complete": False,
                "reason": "no plan-graph (%s)" % graph["error"]}
    nodes = plan_graph._all_nodes(graph)
    n_total = len(nodes)

    # ONE counter, declarative source: each node satisfied iff its declared `post`
    # artifacts are all present (with the verdict gate on verification-*.json).
    # Default post = [verification-<node>.json] so a sidecar that never authored
    # `post` is identical to the old prefix logic.
    art = pd / "artifacts"
    passed = set()
    for node in nodes:
        post = plan_graph.node_artifacts(graph, node)["post"]
        if _node_post_satisfied(art, post):
            passed.add(node)

    complete = n_total > 0 and len(passed) >= n_total
    reason = ("complete (%d/%d)" % (len(passed), n_total) if complete
              else "incomplete (%d/%d phases PASS)" % (len(passed), n_total))
    return {"n_total": n_total, "passed_phases": passed,
            "complete": complete, "reason": reason}


def is_complete(plan_dir, root=None) -> bool:
    return completion_state(plan_dir, root=root)["complete"]


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 1:
        sys.stderr.write("usage: derive_plan_completion.py <plan_dir>\n")
        return 2
    st = completion_state(argv[0])
    st = {**st, "passed_phases": sorted(st["passed_phases"])}
    sys.stdout.write(json.dumps(st) + "\n")
    return 0 if st["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
