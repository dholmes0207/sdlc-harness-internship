"""Tests for the standards-domain graph builder.

standards_graph mirrors the product graph builder but reads the standards tree
under <root>/harness/standards/ and builds the directed graph
rule -> rule-group -> STD-area -> ARCH-goal -> vision. It emits the same JSON
shape as the product builder (nodes/edges/parse_errors/root_path), reserves the
two layering-seam fields on every node, validates the standards id grammar, and
always exits 0. Layout is flat: one areas/STD-<AREA>.md per area, with its
rule-groups and rules declared as frontmatter lists inside the area file (the
same expansion shape product goals use inside brd.md).
"""

import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import standards_graph  # noqa: E402


# ── fixture builder: a valid standards tree in a tmp dir ─────────────────────

_VISION = """---
id: VISION
type: vision
status: approved
owner: arch-team
version: 1.0.0
---

# Engineering Vision

Build systems that are simple, observable, and safe to change.
"""

_STACK = """---
id: STACK
type: stack
status: approved
owner: arch-team
version: 1.0.0
---

# Tech Stack

Python 3.11, Postgres 16, deployed on Kubernetes.
"""

_CHARTER = """---
id: CHARTER
type: charter
status: approved
owner: arch-team
version: 1.0.0
goals:
  - id: ARCH-G1
    title: "Every service is observable"
    status: approved
    owner: arch-team
    metrics: [trace-coverage]
  - id: ARCH-G2
    title: "No undocumented breaking changes"
    status: approved
    owner: arch-team
    metrics: [change-failure-rate]
---

# Architecture Charter

The goals every standard area must address.
"""

_AREA_AUTH = """---
id: STD-AUTH
type: std_area
status: approved
owner: security-team
version: 1.0.0
arch_goals: [ARCH-G1]
rule_groups:
  - id: STD-AUTH-RG1
    title: "Session handling"
    status: approved
    owner: security-team
    rules:
      - id: STD-AUTH-RG1-R1
        title: "Sessions expire after 24h"
        status: approved
        compliance_checks: ["assert session TTL <= 24h"]
      - id: STD-AUTH-RG1-R2
        title: "Tokens are rotated on privilege change"
        status: approved
        depends_on: [STD-AUTH-RG1-R1]
        compliance_checks: ["assert rotation on role change"]
---

# Authentication Standards

How services authenticate and manage sessions.
"""


def _write_valid_tree(root: Path) -> Path:
    std = root / "harness" / "standards"
    (std / "areas").mkdir(parents=True, exist_ok=True)
    (std / "vision.md").write_text(_VISION, encoding="utf-8")
    (std / "STACK.md").write_text(_STACK, encoding="utf-8")
    (std / "charter.md").write_text(_CHARTER, encoding="utf-8")
    (std / "areas" / "STD-AUTH.md").write_text(_AREA_AUTH, encoding="utf-8")
    return root


def _node(graph, node_id):
    for n in graph["nodes"]:
        if n["id"] == node_id:
            return n
    raise AssertionError(f"node {node_id} not in graph: {[n['id'] for n in graph['nodes']]}")


def _edge_kinds(graph, frm):
    return {(e["to"], e["kind"]) for e in graph["edges"] if e["from"] == frm}


# ── tests ────────────────────────────────────────────────────────────────────

def test_builds_renamed_hierarchy(tmp_path):
    root = _write_valid_tree(tmp_path)
    graph = standards_graph.build_graph(root)
    types = {n["type"] for n in graph["nodes"]}
    assert {"stack", "vision", "arch_goal", "std_area", "rule_group", "rule"} <= types
    # edges match the rename map: rule->rule_group, rule_group->std_area, std_area->arch_goal
    assert ("STD-AUTH-RG1", "rule_group") in _edge_kinds(graph, "STD-AUTH-RG1-R1")
    assert ("STD-AUTH", "std_area") in _edge_kinds(graph, "STD-AUTH-RG1")
    assert ("ARCH-G1", "arch_goal") in _edge_kinds(graph, "STD-AUTH")


def test_arch_goal_requires_metric(tmp_path):
    # the builder never judges: an arch_goal with no metrics is still a node, and
    # the metrics field is preserved for the gate / template to enforce.
    root = _write_valid_tree(tmp_path)
    graph = standards_graph.build_graph(root)
    g1 = _node(graph, "ARCH-G1")
    assert g1["metrics"] == ["trace-coverage"]
    assert g1["type"] == "arch_goal"


def test_id_grammar_valid_and_garbage(tmp_path):
    from graph_core import id_grammar_findings
    root = _write_valid_tree(tmp_path)
    graph = standards_graph.build_graph(root)
    # the valid tree yields no invalid_id
    clean = id_grammar_findings(graph["nodes"], standards_graph.ID_PATTERN_BY_TYPE)
    assert clean == []
    # now a garbage rule id surfaces as invalid_id, never a crash
    area = root / "harness" / "standards" / "areas" / "STD-AUTH.md"
    area.write_text(area.read_text(encoding="utf-8").replace(
        "STD-AUTH-RG1-R1", "not a valid id"), encoding="utf-8")
    g2 = standards_graph.build_graph(root)
    findings = id_grammar_findings(g2["nodes"], standards_graph.ID_PATTERN_BY_TYPE)
    assert any(f["check"] == "invalid_id" for f in findings)


def test_node_carries_seam_fields(tmp_path):
    root = _write_valid_tree(tmp_path)
    graph = standards_graph.build_graph(root)
    assert graph["nodes"], "tree must produce nodes"
    for n in graph["nodes"]:
        assert "applies_standards" in n
        assert n["applies_standards"] is None or isinstance(n["applies_standards"], list)
        assert "standards_compliance" in n
        assert isinstance(n["standards_compliance"], dict)


def test_build_graph_always_exit_zero_emits_json(tmp_path):
    root = _write_valid_tree(tmp_path)
    # corrupt one file so there is a parse error
    bad = root / "harness" / "standards" / "areas" / "STD-AUTH.md"
    bad.write_text("---\n: : not: valid: yaml: [\n---\n# broken\n", encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(_SCRIPTS / "standards_graph.py"), "--root", str(root)],
        capture_output=True, text=True, env={**_env(), "HARNESS_ROOT": str(root)})
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert "parse_errors" in out
    assert out["parse_errors"], "malformed area must surface as a parse_error"


def test_missing_standards_dir(tmp_path):
    graph = standards_graph.build_graph(tmp_path)  # no harness/standards/
    assert graph["nodes"] == []
    assert graph["edges"] == []
    assert graph.get("missing_standards_dir") is True


def test_lang_from_scope_table():
    # the per-language grouping helper: first-extension-match wins, no-extension
    # scope → general, non-list / empty → general
    f = standards_graph.lang_from_scope
    assert f(["**/*.py"]) == "python"
    assert f(["**/*.go"]) == "go"
    assert f(["**/*.ts", "**/*.tsx"]) == "typescript"
    assert f(["src/handlers/**/*.py", "**/*.go"]) == "python"   # first match wins
    assert f(["src/**"]) == "general"                            # no extension
    assert f([]) == "general"
    assert f("**/*.py") == "general"                             # not a list
    assert f(None) == "general"

    # extended language mappings (ECC port)
    assert f(["**/*.cpp"]) == "cpp"
    assert f(["**/*.hpp"]) == "cpp"
    assert f(["**/*.cs"]) == "csharp"
    assert f(["**/*.kt"]) == "kotlin"
    assert f(["**/*.swift"]) == "swift"
    assert f(["**/*.php"]) == "php"
    assert f(["**/*.dart"]) == "dart"
    assert f(["**/*.fs"]) == "fsharp"
    assert f(["**/*.ets"]) == "arkts"
    assert f(["**/*.vue"]) == "vue"
    assert f(["**/*.html"]) == "web"
    assert f(["**/*.css"]) == "web"
    assert f(["**/*.pl"]) == "perl"
    assert f(["**/*.pm"]) == "perl"


def test_deeply_nested_yaml_is_parse_error_not_crash(tmp_path):
    # a deeply-nested collection blows PyYAML's recursion limit (RecursionError,
    # not YAMLError); build_graph must surface it as a parse_error, never crash.
    std = tmp_path / "harness" / "standards" / "areas"
    std.mkdir(parents=True)
    (std / "STD-DEEP.std.yaml").write_text(
        "id: STD-DEEP\ntype: std_area\ndescription: " + "[" * 600 + "]" * 600 + "\n",
        encoding="utf-8")
    graph = standards_graph.build_graph(tmp_path)   # must not raise
    assert any("parse error" in (e.get("error") or "").lower()
               for e in graph["parse_errors"])


def test_symlinked_area_escaping_the_tree_is_a_parse_error_not_a_crash(tmp_path):
    # An areas/*.md that is a symlink resolving OUTSIDE the standards tree has no
    # tree-relative path; build_graph must keep its always-exit-0 / never-raise
    # contract and surface the escapee as a parse_error, not crash on ValueError.
    root = _write_valid_tree(tmp_path)
    outside = tmp_path / "outside-area.md"
    outside.write_text(_AREA_AUTH.replace("STD-AUTH", "STD-OUT"), encoding="utf-8")
    link = root / "harness" / "standards" / "areas" / "STD-LINK.md"
    link.symlink_to(outside)
    graph = standards_graph.build_graph(root)  # must NOT raise
    assert any("STD-LINK.md" in pe["file"] for pe in graph["parse_errors"]), (
        "the symlink escaping the tree must surface as a parse_error: %s"
        % graph["parse_errors"])
    # the escapee produced no node (it could not be placed in the tree)
    assert not any("STD-OUT" in n["id"] for n in graph["nodes"])


def test_no_product_only_fields(tmp_path):
    # the standards node must not import the product-only field family. NOTE:
    # `scope` is intentionally NOT banned — it is a legitimate rule-leaf field
    # (the review-time path-glob scope), a different concept from the product
    # brd's project-scope field.
    root = _write_valid_tree(tmp_path)
    graph = standards_graph.build_graph(root)
    banned = {"competitors", "competitive_parity", "risks", "moscow",
              "horizon", "size", "personas", "brd_goals"}
    for n in graph["nodes"]:
        assert not (banned & set(n.keys())), f"{n['id']} carries product-only keys"


def test_snapshot_written_under_standards(tmp_path):
    root = _write_valid_tree(tmp_path)
    graph = standards_graph.build_graph(root)
    p1 = standards_graph.write_snapshot(graph, root)
    p2 = standards_graph.write_snapshot(graph, root)
    assert ".snapshots" in p1.parts
    assert "standards" in p1.parts
    assert p1 == p2  # idempotent


def _env():
    import os
    return dict(os.environ)
