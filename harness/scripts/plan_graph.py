#!/usr/bin/env python3
"""plan_graph.py — read a plan's machine-readable phase-DAG sidecar and derive
advisory findings (cycles, ordering hazards, parallel batches + file conflicts).

Single responsibility: parse the sidecar + delegate cycle detection + derive read-only
findings. It NEVER edits the plan — option D / red line #4: detection only, no AI auto-fix
after approval. The sidecar `plans/<slug>/plan-graph.yaml` carries ONLY edges + per-phase
file ownership, never status (status lives mutable in plan.md's `## Phases`).

Edge direction is pinned: ``{from: A, to: B}`` means "A runs BEFORE B" (A is B's
prerequisite). build_adj maps that semantics explicitly rather than reusing graph_core's
children_of/parents_of, which read the opposite way.

Parallelism is DERIVED, never authored: a parallel batch is a topological level (an
antichain — phases with no edge between them). But an edge alone lies about parallelism in
a file-based harness: the real blocker is usually a shared file, not a logical dependency.
So a safe parallel batch is an antichain AND a pairwise-disjoint file set; find_parallel_
conflicts turns the prose "shared-file" warning into a machine finding.

Each node MUST declare a `post:` artifact obligation. A missing/malformed
`post` is a structural contract violation, not a heuristic advisory: find_missing_post +
the CLI hard-fail (exit 2, both modes) refuse it at validate + cook-preflight time. The
node_artifacts default is only a runtime backstop for an already-approved graph.
"""
import sys
from collections import defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve()
if str(_HERE.parent) not in sys.path:
    sys.path.insert(0, str(_HERE.parent))
import graph_core  # noqa: E402

_SIDECAR = "plan-graph.yaml"


def parse_phase_graph(plan_dir) -> dict:
    """Load the sidecar. Returns the parsed mapping, or ``{"error": <msg>}`` on a
    malformed file — never raises (a planner-facing finding, not a crash)."""
    import yaml
    path = Path(plan_dir) / _SIDECAR
    if not path.is_file():
        return {"error": "no %s in %s" % (_SIDECAR, plan_dir)}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        return {"error": "malformed %s: %s" % (_SIDECAR, exc)}
    if not isinstance(data, dict):
        return {"error": "%s is not a mapping" % _SIDECAR}
    # Normalize: a YAML `edges:` / `subtasks:` with an empty value parses to None,
    # which setdefault would NOT replace — coerce so callers always see list/dict.
    if not isinstance(data.get("edges"), list):
        data["edges"] = []
    if not isinstance(data.get("subtasks"), dict):
        data["subtasks"] = {}
    return data


# ---------------------------------------------------------------- adjacency ---

def _all_nodes(graph: dict) -> set:
    nodes = set(graph.get("subtasks", {}) or {})
    for e in graph.get("edges", []) or []:
        nodes.add(e.get("from"))
        nodes.add(e.get("to"))
    nodes.discard(None)
    return nodes


def build_adj(graph: dict) -> dict:
    """edges → adjacency with the plan's own direction: {from: [to, ...]}.
    Every node is a key (targets get an empty list) so cycle detection sees them."""
    adj = {n: [] for n in _all_nodes(graph)}  # every node is a key (incl. pure targets)
    for e in graph.get("edges", []) or []:
        src, dst = e.get("from"), e.get("to")
        if src is not None and dst is not None:
            adj[src].append(dst)  # src is already a key via _all_nodes
    return adj


def find_cycles(graph: dict) -> list:
    return graph_core.find_dep_cycles(build_adj(graph))


# ------------------------------------------------------------ file ownership ---

def _subtask(graph: dict, phase: str) -> dict:
    """The subtask mapping for a phase, coerced to a dict — a non-dict / scalar value
    from a hand-edited sidecar yields {} instead of raising AttributeError downstream
    (keeps the 'never raises' contract)."""
    st = (graph.get("subtasks", {}) or {}).get(phase)
    return st if isinstance(st, dict) else {}


def _files(graph: dict, phase: str, *keys) -> set:
    st = _subtask(graph, phase)
    out = set()
    for k in keys:
        out |= set(st.get(k, []) or [])
    return out


def _owned(graph: dict, phase: str) -> set:
    return _files(graph, phase, "files_to_create", "files_to_modify", "files_to_delete")


# ------------------------------------------------------- artifact obligation ---
# A node declares its end-of-phase artifact obligation as `post: [name, ...]`.
# This is the SAME obligation the derive counter (derive_plan_completion) already
# assumed implicitly (node -> verification-<node>.json); naming it here makes it a
# single declarative source the counter reads, instead of a hard-coded prefix. No
# `pre` this round (deferred — schema leaves room, nothing reads it yet).

def _str_list(v):
    """v as a list of non-empty str, or [] when v is anything else (scalar, dict,
    a list containing a non-str). Lets a malformed `post` degrade to the default
    rather than raise — same 'never crash on a hand-edited sidecar' contract."""
    if isinstance(v, list) and v and all(isinstance(x, str) and x for x in v):
        return list(v)
    return []


def node_artifacts(graph: dict, node: str) -> dict:
    """The node's declared artifact obligation: ``{"post": [str, ...]}``.

    RUNTIME reader (derive_plan_completion / artifact_check call it on an already
    *approved* graph). It keeps a defensive default = ``["verification-<node>.json"]``
    when `post` is unstated/malformed, so a node that slipped through resolves instead
    of crashing cook. This default is NOT an authoring allowance: `find_missing_post`
    + the `_main` gate refuse a missing/malformed `post` at validate + cook-preflight
    time, so a graph that reaches the runtime always has explicit
    post. `pre` is deferred: this reader never returns a "pre" key even if authored."""
    st = _subtask(graph, node)
    post = _str_list(st.get("post")) or ["verification-%s.json" % node]
    return {"post": post}


def find_missing_post(graph: dict) -> list:
    """Nodes (sorted) that do NOT declare an explicit, valid ``post`` — the MANDATORY
    end-of-phase artifact obligation. "Missing" covers no subtask entry, no
    `post` key, and a malformed `post` (not a non-empty list-of-str). The `_main` gate
    turns a non-empty result into a hard exit-2 refusal; the node_artifacts default
    still backstops the runtime so this is an authoring gate, not a runtime crash."""
    return sorted(n for n in _all_nodes(graph)
                  if not _str_list(_subtask(graph, n).get("post")))


def _created(graph: dict, phase: str) -> set:
    return _files(graph, phase, "files_to_create")


def _modified_or_deleted(graph: dict, phase: str) -> set:
    return _files(graph, phase, "files_to_modify", "files_to_delete")


def find_ordering_hazards(graph: dict) -> list:
    """For each edge A→B, flag a file the prerequisite A modifies/deletes that the
    dependent B newly creates — A would touch a file that does not exist yet."""
    out = []
    for e in graph.get("edges", []) or []:
        a, b = e.get("from"), e.get("to")
        if a is None or b is None:
            continue
        shared = _modified_or_deleted(graph, a) & _created(graph, b)
        for f in sorted(shared):
            out.append({"prereq": a, "dependent": b, "file": f,
                        "msg": "%s modifies/deletes %s that %s only creates later"
                               % (a, f, b)})
    return out


# --------------------------------------------------------------- parallelism ---

def find_parallel_batches(graph: dict) -> list:
    """Topological levels (Kahn). Each level is an antichain — a batch that may run
    in parallel. Returns levels as sorted name lists."""
    adj = build_adj(graph)
    indeg = defaultdict(int)
    for src, dsts in adj.items():
        indeg.setdefault(src, indeg.get(src, 0))
        for d in dsts:
            indeg[d] += 1
    remaining = dict(indeg)
    batches = []
    while remaining:
        level = sorted(n for n, d in remaining.items() if d == 0)
        if not level:  # a cycle remains — stop deriving batches
            if remaining:
                print("plan-graph: cycle detected — %d nodes unreachable: %s" %
                      (len(remaining), ", ".join(sorted(remaining))),
                      file=sys.stderr)
            break
        batches.append(level)
        for n in level:
            del remaining[n]
            for d in adj.get(n, []):
                if d in remaining:
                    remaining[d] -= 1
    return batches


def find_parallel_conflicts(graph: dict) -> list:
    """In any batch of ≥2 phases, flag a file two phases both own — parallel edits
    of a shared path clobber. The machine form of the prose 'shared-file' warning."""
    out = []
    for batch in find_parallel_batches(graph):
        if len(batch) < 2:
            continue
        for i in range(len(batch)):
            for j in range(i + 1, len(batch)):
                a, b = batch[i], batch[j]
                shared = _owned(graph, a) & _owned(graph, b)
                for f in sorted(shared):
                    out.append({"phases": [a, b], "file": f,
                                "msg": "%s ∥ %s both touch %s — serialize or split "
                                       "ownership" % (a, b, f)})
    return out


def lint_no_status(graph: dict) -> list:
    """A status key must not live in the sidecar (status is mutable, owned by plan.md).
    Warn — do not raise — so a stray status is surfaced, not silently hashed."""
    warnings = []
    if "status" in graph:
        warnings.append("top-level 'status' key in %s — status belongs in plan.md" % _SIDECAR)
    for phase, st in (graph.get("subtasks", {}) or {}).items():
        if isinstance(st, dict) and "status" in st:
            warnings.append("subtask %s carries 'status' in %s — status belongs in "
                            "plan.md's `## Phases`" % (phase, _SIDECAR))
        # `post` is the declarative artifact obligation; a malformed value silently
        # degrades to the default in node_artifacts, so surface it here instead.
        if isinstance(st, dict) and "post" in st and not _str_list(st.get("post")):
            warnings.append("subtask %s has a malformed 'post' in %s — expected a "
                            "list of artifact names; using default "
                            "[verification-%s.json]" % (phase, _SIDECAR, phase))
    for e in graph.get("edges", []) or []:
        if isinstance(e, dict) and "status" in e:
            warnings.append("edge %s->%s carries 'status' in %s — status belongs in "
                            "plan.md" % (e.get("from"), e.get("to"), _SIDECAR))
    return warnings


def _main(argv=None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="read a plan's phase-DAG sidecar (advisory)")
    p.add_argument("plan_dir")
    p.add_argument("--require", action="store_true",
                   help="exit 2 if the sidecar is missing (cook preflight gate); "
                        "default stays advisory (exit 0) for validate-time use")
    args = p.parse_args(argv)
    graph = parse_phase_graph(args.plan_dir)
    if graph.get("error"):
        print("error: %s" % graph["error"])
        # The sidecar is a mandatory plan artifact. --require makes its absence a
        # hard cook-preflight block (exit 2); advisory mode never blocks the
        # planner mid-authoring (exit 0).
        return 2 if args.require else 0
    for w in lint_no_status(graph):
        print("status-leak: %s" % w)
    for c in find_cycles(graph):
        print("cycle: %s" % " -> ".join(c))
    for h in find_ordering_hazards(graph):
        print("ordering-hazard: %s" % h["msg"])
    batches = find_parallel_batches(graph)
    print("parallel batches: %s" % batches)
    for c in find_parallel_conflicts(graph):
        print("parallel-conflict: %s" % c["msg"])
    # `post` is MANDATORY per node. Unlike cycles/conflicts (heuristic
    # advisories), a missing `post` is a structural contract violation: HARD-fail (exit 2)
    # in BOTH advisory and --require modes so validate-time + cook-preflight both refuse it.
    missing = find_missing_post(graph)
    for n in missing:
        print("missing-post: node %s must declare 'post:' (required artifact "
              "obligation, e.g. post: [verification-%s.json])" % (n, n))
    return 2 if missing else 0


if __name__ == "__main__":
    raise SystemExit(_main())
