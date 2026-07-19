#!/usr/bin/env python3
"""graph_core — domain-agnostic graph primitives shared by every artifact graph.

These helpers carry zero domain logic: the closure walk, the parent/child
adjacency build, the expected-child counter, the iterative dependency-cycle
finder, the finding-record constructor (with sentinel hygiene), the scalar
coercions, the snapshot/diff/changed-node delta math, and a reusable id-grammar
validation framework. They behave identically to the equivalents that the
verbatim product-spec port carries inline, but this module is INDEPENDENT — it
imports nothing from that port and that port imports nothing from here, so a
later change to either side cannot ripple into the other.

Two functions are parameterized so one definition serves more than one domain:
`matching_child_counts` takes the expected-child hierarchy map, and
`write_snapshot`/`diff_graphs` take the snapshot dir / scalar-field list instead
of closing over a single domain's module constants.
"""

import datetime as dt
import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Set


# ── adjacency ────────────────────────────────────────────────────────────────

def _closure(adj: Dict[str, List[str]], start: str) -> Set[str]:
    """Transitive closure of `start` over an adjacency map via an iterative stack.

    Excludes `start` itself so a self-edge (start->start) or a cycle that loops
    back to start never reports a node as its own descendant. Iterative, so a
    long chain or a cycle terminates without recursion."""
    out: Set[str] = set()
    stack = list(adj.get(start, []))
    while stack:
        n = stack.pop()
        if n in out:
            continue
        out.add(n)
        stack.extend(adj.get(n, []))
    return out - {start}


def children_of(graph: Dict[str, Any]) -> Dict[str, List[str]]:
    """parent id -> list of child ids (the forward adjacency). Edge convention:
    `to` is the parent, `from` is the child (see each builder's build_edges)."""
    out: Dict[str, List[str]] = defaultdict(list)
    for e in graph["edges"]:
        out[str(e["to"])].append(str(e["from"]))
    return out


def parents_of(graph: Dict[str, Any]) -> Dict[str, List[str]]:
    """child id -> list of distinct parent ids, in edge order, str-coerced.

    Self-edges (id == id) are dropped — a node is never its own tree parent,
    which also neutralizes a self/cyclic-parent hang in any client walk."""
    out: Dict[str, List[str]] = defaultdict(list)
    for e in graph["edges"]:
        child, par = str(e["from"]), str(e["to"])
        if par != child and par not in out[child]:
            out[child].append(par)
    return dict(out)


def matching_child_counts(graph: Dict[str, Any],
                          child_type_for_parent: Dict[str, str]) -> Dict[str, int]:
    """For each parent-type node id, count inbound edges whose SOURCE node is of
    the EXPECTED child type, per the injected `child_type_for_parent` map.

    Counting only expected-type children means a stray wrong-type edge (a
    malformed graph where a rule points straight at an std_area) does not mask a
    real gap. The hierarchy map is a PARAMETER (not a module constant) so one
    definition serves the product and standards domains alike."""
    nodes_by_id = {n["id"]: n for n in graph["nodes"]}
    counts: Dict[str, int] = defaultdict(int)
    for e in graph["edges"]:
        src_type = nodes_by_id.get(e["from"], {}).get("type")
        tgt_type = nodes_by_id.get(e["to"], {}).get("type")
        if tgt_type in child_type_for_parent and child_type_for_parent[tgt_type] == src_type:
            counts[e["to"]] += 1
    return dict(counts)


def find_dep_cycles(adj: Dict[str, List[str]]) -> List[List[str]]:
    """Return every dependency cycle in `adj` as a closed path.

    Iterative 3-color DFS (white/gray/black) over an explicit stack — NOT
    recursion — so a long linear chain cannot raise RecursionError. Sorted
    iteration makes the output deterministic. A back-edge to a GRAY node yields
    the cycle path including the repeated closing node, e.g. ``["A","B","A"]``.
    A target absent from `adj` (a dangling dependency) is skipped — it can never
    be on a cycle, and the dangling report is owned elsewhere."""
    WHITE, GRAY, BLACK = 0, 1, 2
    color: Dict[str, int] = {}
    cycles: List[List[str]] = []
    for root in sorted(adj):
        if color.get(root, WHITE) != WHITE:
            continue
        color[root] = GRAY
        path = [root]
        stack = [(root, iter(sorted(adj.get(root, []))))]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nbr in it:
                if nbr not in adj:        # dangling target → owned by the dangling check
                    continue
                c = color.get(nbr, WHITE)
                if c == GRAY:             # back-edge → cycle
                    cycles.append(path[path.index(nbr):] + [nbr])
                elif c == WHITE:
                    color[nbr] = GRAY
                    path.append(nbr)
                    stack.append((nbr, iter(sorted(adj.get(nbr, [])))))
                    advanced = True
                    break
            if not advanced:             # node exhausted → backtrack
                color[node] = BLACK
                path.pop()
                stack.pop()
    return cycles


# ── content fingerprint ───────────────────────────────────────────────────────

def _content_fingerprint(parts: List[Any]) -> str:
    """sha256 (first 8 hex) over a canonical JSON of `parts`. Deterministic: same
    parts → same hash. List order is significant (reordering a list IS a content
    change); only dict keys are sorted for stability. The generic content-hash
    primitive each domain folds its own field selection into."""
    canon = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()[:8]


# ── findings ───────────────────────────────────────────────────────────────

# The two sentinel strings _scalar_id() emits for absent/malformed ids. Callers
# that present valid ids subtract these so an internal sentinel never reaches a
# user-facing finding. Single authoritative home — import, never re-literal.
ID_SENTINELS = ("<missing-id>", "<invalid-id>")


def make_finding(check_id: str, severity: str, node: Dict[str, Any],
                 detail: str, **context) -> Dict[str, Any]:
    """The single home for the finding-record constructor.

    Sentinel hygiene: when the node's id is an internal absent/malformed sentinel
    (`<missing-id>`/`<invalid-id>`), `artifact_id` is nulled and any occurrence of
    that sentinel inside the (caller-interpolated) `detail` is rewritten to the
    file path — so the internal sentinel can NEVER reach a user-facing finding."""
    nid = node.get("id")
    if nid in ID_SENTINELS:
        label = node.get("file") or "(unknown file)"
        detail = detail.replace(nid, label)
        artifact_id = None
    else:
        artifact_id = nid
    return {
        "check": check_id,
        "severity": severity,
        "artifact_id": artifact_id,
        "file": node.get("file"),
        "detail": detail,
        "context": context or None,
    }


# ── scalar coercions ──────────────────────────────────────────────────────────

def _scalar_id(v: Any) -> str:
    """Coerce a frontmatter `id` to a hashable str so it can NEVER raise when used
    as a dict key / set element. Absent → `<missing-id>`; a non-string
    (list/dict/int from a hand-edit) → `<invalid-id>`, which then fails the
    id-grammar regex and surfaces as `invalid_id` instead of crashing the gate."""
    if isinstance(v, str):
        return v or "<missing-id>"
    if v is None:
        return "<missing-id>"
    return "<invalid-id>"


def _scalar_link(v: Any) -> Optional[str]:
    """Coerce a scalar parent link to str|None at the single source. A non-string
    (list/dict from malformed YAML) → None, so build_edges and every parent
    lookup never hash an unhashable value; the now-missing parent surfaces as an
    orphan/dangling finding (fail-soft) rather than crashing the gate."""
    return v if isinstance(v, str) else None


def _as_id_list(v: Any) -> List[str]:
    """Coerce a frontmatter `depends_on` into a sorted list of id strings.

    A non-list (a bare scalar / None / mapping from malformed YAML) yields [] —
    so a build never raises on a mixed-type sorted() and never silently splits a
    bare string into characters. A wrong-artifact-type placement is surfaced
    separately by the structural checks."""
    if not isinstance(v, list):
        return []
    return sorted(x for x in v if isinstance(x, str))


# ── delta / snapshot ──────────────────────────────────────────────────────────

# The single authoritative tuple of node fields whose change between two
# snapshots makes a node "changed" for delta/impact purposes. `body_hash` is the
# body-content signal; `content_hash` additionally covers fields the body hash
# cannot see; the rest are frontmatter facts.
CHANGED_FIELDS = ("status", "scope", "moscow", "horizon", "size", "body_hash", "content_hash")


def _nodes_by_id(snapshot: Dict[str, Any]) -> Dict[Any, Any]:
    """Index a snapshot's nodes by id, SKIPPING any entry that is not a dict or has no
    'id'. The snapshot-diff functions document foreign / pre-upgrade / hand-edited
    snapshots as valid input, so an id-less or malformed node must degrade gracefully,
    never crash the delta with a KeyError."""
    return {n["id"]: n for n in (snapshot.get("nodes") or [])
            if isinstance(n, dict) and n.get("id") is not None}


def changed_nodes(current: Dict[str, Any], previous: Dict[str, Any]) -> List[str]:
    """Node ids present in BOTH snapshots whose any CHANGED_FIELDS value differs.

    A field counts as changed only when it is PRESENT on both sides and the
    values differ. A field ABSENT on one side (e.g. a hash a pre-upgrade snapshot
    predates) is treated as UNKNOWN, not a change — so the first post-upgrade
    delta does not mark every node as changed. Returns ids sorted."""
    cur = _nodes_by_id(current)
    prev = _nodes_by_id(previous)
    out: List[str] = []
    for nid in cur.keys() & prev.keys():
        c, p = cur[nid], prev[nid]
        for field in CHANGED_FIELDS:
            if field not in c or field not in p:
                continue  # unknown on one side → not a change signal
            if c[field] != p[field]:
                out.append(nid)
                break
    return sorted(out)


def diff_graphs(current: Dict[str, Any], baseline: Dict[str, Any],
                scalar_fields: tuple = (), meta_key: str = "meta") -> Dict[str, Any]:
    """Structural diff between two graph snapshots: added/removed node ids plus the
    domain scalar fields (under `graph[meta_key]`) that changed.

    The set-math is generic; the per-domain `scalar_fields` and `meta_key` are
    PARAMETERS instead of a hard-coded product field list, so one definition
    serves any domain. A domain passing `scalar_fields=()` gets added/removed
    only."""
    cur_ids = set(_nodes_by_id(current))
    base_ids = set(_nodes_by_id(baseline))
    cur_m = current.get(meta_key) or {}
    base_m = baseline.get(meta_key) or {}
    scalar_changes: List[str] = []
    for field in scalar_fields:
        if cur_m.get(field) != base_m.get(field):
            scalar_changes.append(field)
    return {
        "added": sorted(cur_ids - base_ids),
        "removed": sorted(base_ids - cur_ids),
        "scalar_changes": scalar_changes,
    }


def write_snapshot(graph: Dict[str, Any], snap_dir: Path) -> Path:
    """Persist a graph snapshot under the INJECTED `snap_dir` as `<ISO>-<hash>.json`.

    Filename is derived from `graph["generated_at"]` plus the first 8 hex digits
    of the SHA-256 of the JSON body. The hash suffix prevents two snapshots taken
    in the same second from silently overwriting each other while keeping the
    filename deterministic (same content → same hash → same path). The target dir
    is a PARAMETER (not a hard-coded product path) so one definition serves any
    domain."""
    snap_dir = Path(snap_dir)
    snap_dir.mkdir(parents=True, exist_ok=True)
    generated_at = graph.get("generated_at") or _now()
    body = json.dumps({"snapshot_at": generated_at, **graph}, indent=2,
                      ensure_ascii=False, default=str)
    content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:8]
    ts = generated_at.replace("-", "").replace(":", "")
    path = snap_dir / f"{ts}-{content_hash}.json"
    path.write_text(body, encoding="utf-8")
    return path


# ── id-grammar framework ──────────────────────────────────────────────────────

def id_grammar_findings(nodes: List[Dict[str, Any]],
                        pattern_by_type: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Validate each node's id against `pattern_by_type[node.type]`.

    Emits one `invalid_id` finding (via make_finding) when a node's id fails the
    regex for its type. A node whose id is one of the absent/malformed sentinels
    is skipped here so it is NOT double-reported — make_finding's hygiene already
    nulls the sentinel for any finding that names it. A type with no registered
    pattern is left unvalidated (the caller owns which types carry a grammar)."""
    findings: List[Dict[str, Any]] = []
    for n in nodes:
        nid = n.get("id")
        if nid in ID_SENTINELS:
            continue
        pattern = pattern_by_type.get(n.get("type"))
        if pattern is None:
            continue
        if not pattern.match(nid or ""):
            findings.append(make_finding(
                "invalid_id", "error", n,
                f"id {nid!r} does not match the {n.get('type')} grammar "
                f"({pattern.pattern}).",
                expected=pattern.pattern,
            ))
    return findings


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z"
