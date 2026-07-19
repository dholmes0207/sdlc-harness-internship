"""hs:spec — spec_graph.build_graph shape, edges, cycle-safety, drop-in compat."""

import json
import sys
from pathlib import Path

import pytest

from conftest import VALID  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
# Literal path keeps the stashed-skill collect_ignore coupling working:
# harness/plugins/hs/skills/spec/scripts
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
_SCHEMA = _SPEC_SCRIPTS.parent / "schemas" / "spec-graph.schema.json"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_mods = load_skill_scripts(
    _SPEC_SCRIPTS,
    ["encoding_utils", "frontmatter_parser", "id_grammar", "spec_graph"],
)
spec_graph = _mods["spec_graph"]
id_grammar = _mods["id_grammar"]


def _graph():
    return spec_graph.build_graph(VALID)


def test_build_graph_shape_keys():
    g = _graph()
    for key in ("version", "product", "nodes", "edges", "risks",
                "competitors", "parse_errors", "root_path"):
        assert key in g, "missing graph key: %s" % key


def test_build_graph_full_ladder_nodes():
    g = _graph()
    ids = {n["id"] for n in g["nodes"]}
    assert {"PRODUCT", "VISION", "BRD-G1", "BRD-G2",
            "PRD-AUTH", "PRD-AUTH-E1", "PRD-AUTH-E1-S1"} <= ids
    types = {n["type"] for n in g["nodes"]}
    assert {"product", "vision", "goal", "prd", "epic", "story"} <= types


def test_build_graph_edges_kinds():
    g = _graph()
    edges = {(e["from"], e["to"], e["kind"]) for e in g["edges"]}
    assert ("PRD-AUTH-E1-S1", "PRD-AUTH-E1", "epic") in edges
    assert ("PRD-AUTH-E1", "PRD-AUTH", "prd") in edges
    assert ("PRD-AUTH", "BRD-G1", "brd_goal") in edges


def test_no_parse_errors_on_valid_tree():
    g = _graph()
    assert g["parse_errors"] == []


def test_missing_product_dir_stub(tmp_path):
    g = spec_graph.build_graph(tmp_path)  # no docs/product/
    assert g.get("missing_product_dir") is True
    assert g["nodes"] == []
    assert g["edges"] == []


def test_dangling_parent_link_builds_soft(tmp_path):
    # story.epic points at a non-existent epic -> graph still builds (exit-0 soft),
    # the edge simply names a target with no matching node.
    prod = tmp_path / "docs" / "product"
    (prod / "stories").mkdir(parents=True)
    (prod / "PRODUCT.md").write_text(
        "---\nid: PRODUCT\ntype: product\nstatus: draft\nlang: en\n"
        "version: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# P\n",
        encoding="utf-8")
    (prod / "stories" / "PRD-X-E9-S9.md").write_text(
        "---\nid: PRD-X-E9-S9\ntype: story\nepic: PRD-X-E9\nstatus: draft\n"
        "lang: en\nversion: 0.1.0\ncreated: 2026-05-28\nupdated: 2026-05-28\n---\n# S\n",
        encoding="utf-8")
    g = spec_graph.build_graph(tmp_path)
    node_ids = {n["id"] for n in g["nodes"]}
    assert "PRD-X-E9-S9" in node_ids
    assert "PRD-X-E9" not in node_ids                      # dangling target absent
    edge_targets = {e["to"] for e in g["edges"]}
    assert "PRD-X-E9" in edge_targets                      # edge still emitted


def test_downstream_cycle_safe():
    g = _graph()
    # downstream from a goal reaches the PRD/epic/story wired above it; must not
    # RecursionError even if the graph had a cycle.
    result = spec_graph.downstream(g, "BRD-G1")
    assert isinstance(result, set)


def test_determinism_excluding_timestamp():
    a = _graph()
    b = _graph()
    a.pop("generated_at", None)
    b.pop("generated_at", None)
    # default=str: YAML parses ISO dates to datetime.date, not JSON-native.
    assert (json.dumps(a, sort_keys=True, default=str)
            == json.dumps(b, sort_keys=True, default=str))


def test_graph_matches_schema():
    jsonschema = pytest.importorskip("jsonschema")
    schema = json.loads(_SCHEMA.read_text(encoding="utf-8"))
    jsonschema.validate(instance=_graph(), schema=schema)


# ---------------------------------------------------------------------------
# diff_graphs: a hand-edited baseline snapshot's `product` field is not
# guaranteed to be a mapping — a non-dict value must degrade, not crash.
# ---------------------------------------------------------------------------

def test_diff_graphs_non_dict_baseline_product_does_not_crash():
    current = _graph()
    baseline = dict(current)
    baseline["product"] = "MyApp"  # hand-edited snapshot: a bare string, not a mapping
    result = spec_graph.diff_graphs(current, baseline)
    assert isinstance(result, dict)
    assert "product_changes" in result


def test_diff_graphs_non_dict_current_product_does_not_crash():
    current = dict(_graph())
    current["product"] = 42
    baseline = _graph()
    result = spec_graph.diff_graphs(current, baseline)
    assert isinstance(result, dict)


@pytest.mark.dev_repo
def test_dropin_compat_real_product_tree():
    # Drop-in engine invariant: the live docs/product/ tree (tang-2 orchestrator
    # spec) parses under this engine with no FATAL parse errors and a
    # non-empty node set. Grammar CONFORMANCE of every id is a validation finding
    # (the invalid_id check in check_consistency), NOT a parse-time property —
    # the tree evolves and may legitimately carry an id the validate layer later
    # flags, so asserting per-id grammar here would couple this test to mutable
    # content.
    g = spec_graph.build_graph(ROOT)
    assert g.get("missing_product_dir") is not True
    assert len(g["nodes"]) > 0
    assert g["parse_errors"] == []
    # Every parsed node carries a non-empty id (the engine never emits a null-id node).
    for n in g["nodes"]:
        assert isinstance(n["id"], str) and n["id"], "null id node: %r" % n


def test_build_graph_tolerates_scalar_list_ish_fields(tmp_path):
    # A hand-edited scalar in a list-ish field (goals/risks/competitors/personas)
    # must not crash build_graph — which every spec:* script funnels through.
    (tmp_path / "docs" / "product").mkdir(parents=True)
    (tmp_path / "docs" / "product" / "brd.md").write_text(
        "---\nid: BRD\ngoals: 5\nrisks: oops\ncompetitors: ACME\n---\n# BRD\n",
        encoding="utf-8",
    )
    (tmp_path / "docs" / "product" / "PRODUCT.md").write_text(
        "---\nid: PRODUCT\npersonas: alice\n---\n# P\n", encoding="utf-8",
    )
    g = spec_graph.build_graph(tmp_path)          # must not raise
    assert isinstance(g.get("nodes"), list)
    assert g.get("competitors") == []             # scalar coerced to empty, not char-split


# ---------------------------------------------------------------------------
# _content_fingerprint: a hand-edited mixed-key dict (e.g. `- 1: a\n  b: c`)
# must not crash json.dumps(sort_keys=True) — it cannot compare str<int.
# ---------------------------------------------------------------------------

def test_content_fingerprint_tolerates_mixed_key_dict_in_ac(tmp_path):
    (tmp_path / "docs" / "product" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "product" / "PRODUCT.md").write_text(
        "---\nid: PRODUCT\ntype: product\n---\n# P\n", encoding="utf-8")
    (tmp_path / "docs" / "product" / "stories" / "S1.md").write_text(
        "---\nid: PRD-X-E1-S1\ntype: story\n"
        "acceptance_criteria:\n  - 1: a\n    b: c\n"
        "---\n# S\n",
        encoding="utf-8",
    )
    g = spec_graph.build_graph(tmp_path)          # must not raise
    node = next(n for n in g["nodes"] if n["id"] == "PRD-X-E1-S1")
    assert isinstance(node["content_hash"], str) and len(node["content_hash"]) == 8


def test_content_fingerprint_tolerates_circular_anchor(tmp_path):
    # A YAML anchor cycle (`acceptance_criteria: &a [x, *a]`) yaml-loads to a
    # self-referential list; without the `_seen` guard _stringify_keys recurses
    # forever -> RecursionError crashes build_graph (and thus strict_gate).
    (tmp_path / "docs" / "product" / "stories").mkdir(parents=True)
    (tmp_path / "docs" / "product" / "PRODUCT.md").write_text(
        "---\nid: PRODUCT\ntype: product\n---\n# P\n", encoding="utf-8")
    (tmp_path / "docs" / "product" / "stories" / "S1.md").write_text(
        "---\nid: PRD-X-E1-S1\ntype: story\n"
        "acceptance_criteria: &ac\n  - a\n  - *ac\n"
        "---\n# S\n",
        encoding="utf-8",
    )
    g = spec_graph.build_graph(tmp_path)          # must not raise RecursionError
    node = next(n for n in g["nodes"] if n["id"] == "PRD-X-E1-S1")
    assert isinstance(node["content_hash"], str) and len(node["content_hash"]) == 8


def test_stable_dedup_key_tolerates_circular_reference():
    # A self-referential dict must dedup to a stable marker-bearing key, not
    # RecursionError, so check_risks/check_competitors never crash on it.
    import json as _json
    d = {"x": 1}
    d["self"] = d
    key = spec_graph.stable_dedup_key(d)
    assert "[circular reference]" in key
    _json.loads(key)  # still a valid JSON string


def test_goal_content_fingerprint_tolerates_mixed_key_metrics(tmp_path):
    # Same crash site (_content_fingerprint), reached via a BRD goal's `metrics`
    # instead of a story's `acceptance_criteria` (_node_from_goal, not
    # _node_from_artifact).
    (tmp_path / "docs" / "product").mkdir(parents=True)
    (tmp_path / "docs" / "product" / "brd.md").write_text(
        "---\nid: BRD\ntype: brd\n"
        "goals:\n  - id: BRD-G1\n    title: G\n    metrics:\n      - 1: a\n        b: c\n"
        "---\n# BRD\n",
        encoding="utf-8",
    )
    g = spec_graph.build_graph(tmp_path)          # must not raise
    goal = next(n for n in g["nodes"] if n["id"] == "BRD-G1")
    assert isinstance(goal["content_hash"], str) and len(goal["content_hash"]) == 8


def test_content_fingerprint_stays_deterministic():
    # _stringify_keys must not break the "same input -> same hash" contract.
    a = spec_graph._content_fingerprint(["body text", [{"x": 1, "y": [1, 2]}]])
    b = spec_graph._content_fingerprint(["body text", [{"x": 1, "y": [1, 2]}]])
    assert a == b


def _alias_dag(depth, fan=10):
    """A shared-alias DAG: each level is `fan` references to the SAME lower level
    (what `a: &a [...]` / `b: [*a, *a, ...]` yaml-loads to). NOT a cycle -- valid,
    safe-loadable YAML -- but a per-branch-only cycle guard re-walks the shared
    target once per reference path: O(fan**depth) work from an O(fan*depth) file."""
    level: Any = ["x"]
    for _ in range(depth):
        level = [level] * fan
    return level


def _time_bounded(seconds, fn):
    """Run fn under a real-time itimer; raise TimeoutError (non-OSError, so an
    `except OSError` in the code under test cannot swallow it) if it overruns."""
    import signal

    def _boom(signum, frame):
        raise TimeoutError("exponential blowup: did not finish in bound")

    old = signal.signal(signal.SIGALRM, _boom)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        return fn()
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)


def test_content_fingerprint_no_alias_dag_blowup():
    # A shared YAML alias fanned out across siblings (a DAG, not a cycle) must be
    # walked once total, not once per reference path. A per-branch-only cycle guard
    # is O(fan**depth): a <1KB frontmatter file hangs _content_fingerprint (and thus
    # build_graph -> strict_gate) for minutes. Bound it: fixed code finishes in ms.
    dag = _alias_dag(depth=8)
    h = _time_bounded(5.0, lambda: spec_graph._content_fingerprint([dag]))
    assert isinstance(h, str) and len(h) == 8


def test_stable_dedup_key_no_alias_dag_blowup():
    # Same class in stable_dedup_key._canon (reached via check_risks /
    # check_competitors on a `risks:` / `competitors:` list entry).
    dag = _alias_dag(depth=8)
    key = _time_bounded(5.0, lambda: spec_graph.stable_dedup_key({"criteria": dag}))
    assert isinstance(key, str)


def test_content_fingerprint_shared_alias_equals_expanded():
    # The blowup fix must be EXACT, not a truncating budget: a shared-alias DAG and
    # the byte-equal fully-distinct expansion fingerprint identically (memoization
    # changes only time complexity, never the emitted canonical form).
    leaf = ["x", "x"]
    shared = [leaf, leaf, {"k": leaf}]
    distinct = [["x", "x"], ["x", "x"], {"k": ["x", "x"]}]
    assert (spec_graph._content_fingerprint([shared])
            == spec_graph._content_fingerprint([distinct]))
    assert (spec_graph.stable_dedup_key({"a": shared})
            == spec_graph.stable_dedup_key({"a": distinct}))


def test_write_snapshot_survives_lone_surrogate(tmp_path):
    # write_snapshot hashes + writes the JSON body directly (NOT through the
    # emit_json / write_text_atomic chokepoints), so a lone surrogate in a node's
    # `file` field (a non-UTF-8-named artifact) must be neutralized here too, and
    # the filename hash must match the on-disk bytes it is derived from.
    import hashlib
    graph = {
        "generated_at": "2026-01-01T00:00:00Z",
        "nodes": [{"id": "PRD-X", "type": "prd", "file": "weird_\udcff_name.md"}],
        "edges": [],
    }
    path = spec_graph.write_snapshot(graph, tmp_path)   # must not raise
    raw = path.read_bytes()
    text = raw.decode("utf-8")                          # on-disk bytes are valid UTF-8
    assert not any(0xD800 <= ord(c) <= 0xDFFF for c in text)
    # filename hash suffix must be the sha256 of the bytes actually written
    assert path.stem.endswith(hashlib.sha256(raw).hexdigest()[:8])


def test_spec_graph_snapshot_cli_exits_clean_on_surrogate_filename(tmp_path):
    import os
    import subprocess
    prod = tmp_path / "docs" / "product"
    (prod / "prds").mkdir(parents=True)
    (prod / "PRODUCT.md").write_text("---\nid: PRODUCT\ntype: product\n---\n# P\n",
                                     encoding="utf-8")
    bad = os.fsencode(str(prod / "prds")) + b"/weird_\xff_name.md"
    with open(bad, "wb") as fh:
        fh.write(b"---\nid: PRD-BAD\ntype: prd\ntitle: x\nstatus: draft\n"
                 b"scope: in\nmoscow: must\nhorizon: now\nsize: M\nlang: en\n---\n# x\n")
    script = _SPEC_SCRIPTS / "spec_graph.py"
    out = subprocess.run([sys.executable, str(script), "--root", str(tmp_path),
                          "--snapshot"], capture_output=True)
    assert out.returncode == 0, out.stderr.decode("utf-8", "replace")


# ---------------------------------------------------------------------------
# index_artifacts: two id-less artifacts must not collapse onto the same
# `<missing-id>` sentinel key — both stay represented.
# ---------------------------------------------------------------------------

def test_index_artifacts_keeps_both_idless_artifacts():
    arts = [
        {"ok": True, "file": "stories/a.md", "frontmatter": {"type": "story"}},
        {"ok": True, "file": "stories/b.md", "frontmatter": {"type": "story"}},
    ]
    idx = spec_graph.index_artifacts(arts)
    files = {a["file"] for a in idx.values()}
    assert files == {"stories/a.md", "stories/b.md"}, "one id-less artifact was dropped"


# ---------------------------------------------------------------------------
# matching_child_counts: sentinel-id collision must never misattribute a
# node's type to an unrelated edge and produce a wrong count.
# ---------------------------------------------------------------------------

def test_matching_child_counts_does_not_miscount_on_idless_collision():
    graph = {
        "nodes": [
            {"id": "PRD-AUTH", "type": "prd"},
            # id-less STORY: the actual source of the edge below (a malformed,
            # wrong-type edge on a hand-edited graph).
            {"id": "<missing-id>", "type": "story"},
            # id-less EPIC decoy, unrelated to the edge; inserted last so a naive
            # `{n["id"]: n for n in nodes}` dict comprehension lets it win the
            # collision and mislabel the sentinel key as "epic".
            {"id": "<missing-id>", "type": "epic"},
        ],
        "edges": [
            {"from": "<missing-id>", "to": "PRD-AUTH", "kind": "prd"},
        ],
    }
    counts = spec_graph.matching_child_counts(graph)
    # A PRD's expected child type is "epic"; the edge's real source is a story,
    # so this must NOT count as an epic child no matter which id-less node the
    # sentinel collision happens to resolve to.
    assert counts.get("PRD-AUTH", 0) == 0


def test_make_finding_scrubs_referenced_sentinel_on_valid_carrier():
    # make_finding's documented invariant: the internal sentinel can NEVER reach a
    # PO-facing finding "however the detail was composed" — including as a
    # REFERENCED value (a well-formed carrier whose parent-ref field is a sentinel
    # string), not only when the carrier's OWN id is a sentinel.
    node = {"id": "PRD-AUTH-E1-S1", "type": "story", "file": "stories/s1.md"}
    f = spec_graph.make_finding(
        "dangling_link", "error", node,
        "Story references unknown epic <missing-id>.", ref="<missing-id>")
    blob = json.dumps(f, ensure_ascii=False)
    assert "<missing-id>" not in blob and "<invalid-id>" not in blob
    assert f["artifact_id"] == "PRD-AUTH-E1-S1"   # valid carrier id preserved


def test_make_finding_carrier_sentinel_scrub_unchanged():
    # Regression: the carrier-id scrub (own sentinel -> file path, artifact_id
    # nulled) must be untouched by the referenced-sentinel scrub.
    node = {"id": "<missing-id>", "type": "story", "file": "stories/x.md"}
    f = spec_graph.make_finding("missing_id", "error", node,
                                "Artifact <missing-id> has no id.")
    assert f["artifact_id"] is None
    assert "<missing-id>" not in f["detail"] and "stories/x.md" in f["detail"]


def test_title_from_h1_prefix_id_is_not_truncated():
    # A whole-token match must strip a real self-reference tail, but a node id
    # that is a substring PREFIX of a DIFFERENT id in the tail (PRD-AUTH-E1 vs
    # PRD-AUTH-E10, reachable once siblings hit double digits) must NOT truncate.
    assert spec_graph._title_from_h1(
        "# Checkout redesign — depends on PRD-AUTH-E10", "PRD-AUTH-E1"
    ) == "Checkout redesign — depends on PRD-AUTH-E10"
    assert spec_graph._title_from_h1(
        "# Real Title — PRD-AUTH-E1", "PRD-AUTH-E1"
    ) == "Real Title"


def test_parse_file_non_regular_path_is_parse_error_not_read(tmp_path):
    # A glob match that EXISTS but is not a regular file must surface as a
    # parse_error, never be handed to read_text. A directory is the safe
    # stand-in for the assertion (no hang risk): unguarded, read_text raised
    # IsADirectoryError -> "read error"; guarded, we short-circuit with an
    # explicit non-regular message BEFORE any read.
    frontmatter_parser = _mods["frontmatter_parser"]
    weird = tmp_path / "NOTAFILE.md"
    weird.mkdir()
    result = frontmatter_parser.parse_file(weird)
    assert result["ok"] is False
    assert "not a regular file" in (result["error"] or "")


def test_parse_file_fifo_does_not_block(tmp_path):
    # The real hang case: a FIFO named *.md blocks read_text forever (no writer).
    # The is_file() guard must short-circuit it. SIGALRM bounds the test so a
    # regression FAILS (raises) rather than hanging the whole suite.
    import os
    import signal
    frontmatter_parser = _mods["frontmatter_parser"]
    fifo = tmp_path / "x.md"
    os.mkfifo(fifo)

    class _Blocked(Exception):
        pass

    def _handler(signum, frame):
        raise _Blocked

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, 4)
    try:
        result = frontmatter_parser.parse_file(fifo)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    assert result["ok"] is False
    assert "not a regular file" in (result["error"] or "")


def test_load_artifacts_does_not_hang_on_fifo_artifact(tmp_path):
    # End-to-end: a FIFO named like an epic under docs/product/epics/ must not
    # hang load_artifacts -- the core graph builder every gate/visualizer walks.
    import os
    import signal
    product_dir = tmp_path / "docs" / "product"
    (product_dir / "epics").mkdir(parents=True)
    os.mkfifo(product_dir / "epics" / "PRD-A-E1.md")

    class _Blocked(Exception):
        pass

    def _handler(signum, frame):
        raise _Blocked

    old = signal.signal(signal.SIGALRM, _handler)
    signal.setitimer(signal.ITIMER_REAL, 4)
    try:
        arts = spec_graph.load_artifacts(product_dir)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, old)
    assert any(
        not a.get("ok") and "not a regular file" in (a.get("error") or "")
        for a in arts
    )
