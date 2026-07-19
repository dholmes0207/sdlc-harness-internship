"""Independent-review regressions for hs:spec (never-reviewed feature code).

Locks three defects an independent multi-lens pass (corroborated across engines)
found on TDD-green-only code:
  * build_nodes crashed (uncaught ValueError) on a committed symlink whose target
    escapes the product tree — breaking the always-exit-0 contract of every
    analytical spec CLI that funnels through build_graph.
  * changed_nodes / diff_graphs crashed (KeyError) on a hand-edited / legacy
    snapshot node missing its `id` key.
  * check_consistency accepted a PRD id shaped like an epic (`PRD-AUTH-E1` typed
    `prd`): the pattern matched but the narrowest inferred type disagreed — a
    validate/allocate split-brain.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SPEC_SCRIPTS = ROOT / "harness" / "plugins" / "hs" / "skills" / "spec" / "scripts"
sys.path.insert(0, str(Path(__file__).resolve().parent))
from _spec_skill_import import load_skill_scripts  # noqa: E402

_mods = load_skill_scripts(
    _SPEC_SCRIPTS,
    ["encoding_utils", "frontmatter_parser", "id_grammar", "spec_graph",
     "check_consistency", "render_common", "render_ascii"],
)
spec_graph = _mods["spec_graph"]
check_consistency = _mods["check_consistency"]
render_ascii = _mods["render_ascii"]


def _min_graph(nodes):
    return {
        "version": "1.0", "product": {}, "nodes": nodes, "edges": [],
        "risks": [], "competitors": [], "parse_errors": [], "root_path": ".",
    }


# --- B1: escaping symlink must not crash build_graph -------------------------

def test_build_graph_escaping_symlink_does_not_crash(tmp_path):
    # build_graph takes the REPO ROOT and looks for <root>/docs/product.
    root = tmp_path
    (root / "docs" / "product" / "prds").mkdir(parents=True)
    outside = tmp_path / "outside.md"
    outside.write_text("---\nid: PRD-EVIL\ntype: prd\n---\nbody\n", encoding="utf-8")
    link = root / "docs" / "product" / "prds" / "PRD-EVIL.md"
    link.symlink_to(outside)  # target escapes the product tree
    g = spec_graph.build_graph(root)  # must not raise ValueError
    assert isinstance(g, dict)
    # the escaping symlink is skipped, not followed into the graph
    assert "PRD-EVIL" not in {n.get("id") for n in g["nodes"]}


# --- M1: id-less snapshot node must not crash the diff views -----------------

def test_changed_nodes_id_less_node_does_not_crash():
    cur = _min_graph([{"no_id": 1, "status": "open"}, {"id": "PRD-X", "status": "open"}])
    prev = _min_graph([{"id": "PRD-X", "status": "done"}])
    assert spec_graph.changed_nodes(cur, prev) == ["PRD-X"]


def test_diff_graphs_id_less_node_does_not_crash():
    cur = _min_graph([{"no_id": 1}, {"id": "PRD-X"}])
    base = _min_graph([{"id": "PRD-X"}, {"id": "PRD-Y"}])
    result = spec_graph.diff_graphs(cur, base)  # must not raise KeyError
    assert isinstance(result, dict)
    assert "PRD-Y" in result.get("removed", [])


def test_render_ascii_delta_id_less_node_does_not_crash():
    # render_ascii.delta builds cur_ids/base_ids from raw snapshot nodes; an
    # id-less node (hand-edited / legacy snapshot) must not KeyError -- the
    # same M1 guard its siblings changed_nodes/diff_graphs already carry.
    cur = _min_graph([{"no_id": 1}, {"id": "PRD-X", "type": "prd", "status": "open"}])
    base = _min_graph([{"no_id": 1}, {"id": "PRD-X", "type": "prd", "status": "done"}])
    out = render_ascii.delta(cur, base)  # must not raise KeyError/TypeError
    assert isinstance(out, str)
    assert "PRD-X" in out


# --- M9: PRD id shaped like an epic must be rejected at validate -------------

def test_check_consistency_rejects_prd_id_shaped_like_epic():
    graph = _min_graph([{"id": "PRD-AUTH-E1", "type": "prd", "file": "prds/x.md"}])
    codes = {f["check"] for f in check_consistency.check(graph)}
    assert "invalid_id" in codes


def test_check_consistency_accepts_canonical_prd_id():
    graph = _min_graph([{"id": "PRD-AUTH", "type": "prd", "file": "prds/x.md"}])
    codes = {f["check"] for f in check_consistency.check(graph)}
    assert "invalid_id" not in codes
