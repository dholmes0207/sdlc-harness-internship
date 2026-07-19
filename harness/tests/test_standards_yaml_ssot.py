"""Tests for the pure-YAML SSOT standards source format + dual-read loader.

The standards tree's source format moves from `.md`+frontmatter (with the prose
restated in a markdown body) to a pure-YAML SSOT: the whole file is one YAML
mapping and prose lives in `description:`/`rationale:` block scalars. The loader
reads the new `.std.yaml` format AND keeps reading legacy `.md`-frontmatter
(dual-read back-compat), routing by extension. A pure-YAML area must build the
exact same nodes/edges/ids as its `.md` equivalent.
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import standards_graph  # noqa: E402


# ── singletons (shared by both md and yaml trees) ───────────────────────────

_VISION_MD = """---
id: VISION
type: vision
status: approved
owner: arch-team
version: 1.0.0
---

# Engineering Vision

Build systems that are simple, observable, and safe to change.
"""

_CHARTER_MD = """---
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
---

# Architecture Charter
"""

# md-frontmatter area (legacy format).
_AREA_AUTH_MD = """---
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
---

# Authentication Standards

How services authenticate and manage sessions.
"""

# pure-YAML SSOT area — same id/structure, prose in description/rationale fields,
# NO markdown body, NO `---` fence.
_AREA_AUTH_YAML = """id: STD-AUTH
type: std_area
title: "Authentication Standards"
status: approved
owner: security-team
version: 1.0.0
arch_goals: [ARCH-G1]
description: |
  How services authenticate and manage sessions.
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
        rationale: |
          Long-lived sessions widen the theft window.
"""


def _write_singletons(std: Path) -> None:
    (std / "areas").mkdir(parents=True, exist_ok=True)
    (std / "vision.md").write_text(_VISION_MD, encoding="utf-8")
    (std / "charter.md").write_text(_CHARTER_MD, encoding="utf-8")


def _node(graph, node_id):
    for n in graph["nodes"]:
        if n["id"] == node_id:
            return n
    return None


def _structural(graph):
    """The node fields a parity check cares about (ids/type/links/edges) —
    excludes hashes/file/timestamps which legitimately differ by source format."""
    nodes = {
        n["id"]: {
            "type": n["type"],
            "title": n.get("title"),
            "status": n.get("status"),
            "rule_group": n.get("rule_group"),
            "std_area": n.get("std_area"),
            "arch_goals": n.get("arch_goals"),
            "compliance_checks": n.get("compliance_checks"),
            "depends_on": n.get("depends_on"),
        }
        for n in graph["nodes"]
    }
    edges = sorted((e["from"], e["to"], e["kind"]) for e in graph["edges"])
    return nodes, edges


# ── back-compat lock (existing behaviour MUST keep working) ─────────────────

def test_md_fm_area_still_builds(tmp_path):
    std = tmp_path / "harness" / "standards"
    _write_singletons(std)
    (std / "areas" / "STD-AUTH.md").write_text(_AREA_AUTH_MD, encoding="utf-8")
    graph = standards_graph.build_graph(tmp_path)
    assert graph["parse_errors"] == []
    assert _node(graph, "STD-AUTH")["type"] == "std_area"
    assert _node(graph, "STD-AUTH-RG1-R1")["type"] == "rule"


# ── new pure-YAML SSOT behaviour ────────────────────────────────────────────

def test_yaml_ssot_area_builds(tmp_path):
    """A `.std.yaml` area builds the exact same structural graph as its `.md`
    twin (same nodes/edges/ids)."""
    # md tree
    md_root = tmp_path / "md"
    _write_singletons(md_root / "harness" / "standards")
    (md_root / "harness" / "standards" / "areas" / "STD-AUTH.md").write_text(
        _AREA_AUTH_MD, encoding="utf-8")
    md_graph = standards_graph.build_graph(md_root)

    # yaml tree
    y_root = tmp_path / "yaml"
    _write_singletons(y_root / "harness" / "standards")
    (y_root / "harness" / "standards" / "areas" / "STD-AUTH.std.yaml").write_text(
        _AREA_AUTH_YAML, encoding="utf-8")
    y_graph = standards_graph.build_graph(y_root)

    assert y_graph["parse_errors"] == []
    assert _structural(md_graph) == _structural(y_graph)


def test_mixed_dir_dual_read(tmp_path):
    """A directory mixing `.std.yaml` and `.md` areas loads both."""
    std = tmp_path / "harness" / "standards"
    _write_singletons(std)
    (std / "areas" / "STD-AUTH.md").write_text(_AREA_AUTH_MD, encoding="utf-8")
    yaml_area = _AREA_AUTH_YAML.replace("STD-AUTH", "STD-DATA")
    (std / "areas" / "STD-DATA.std.yaml").write_text(yaml_area, encoding="utf-8")
    graph = standards_graph.build_graph(tmp_path)
    assert graph["parse_errors"] == []
    assert _node(graph, "STD-AUTH") is not None  # from .md
    assert _node(graph, "STD-DATA") is not None  # from .std.yaml


def test_same_area_both_forms_dedups_yaml_wins(tmp_path):
    """When `.md` AND `.std.yaml` exist for the SAME area id, the loader must NOT
    emit duplicate nodes/edges — the YAML SSOT form wins (matches "YAML is the
    source of truth"), the legacy `.md` is dropped."""
    std = tmp_path / "harness" / "standards"
    _write_singletons(std)
    (std / "areas" / "STD-AUTH.md").write_text(_AREA_AUTH_MD, encoding="utf-8")
    (std / "areas" / "STD-AUTH.std.yaml").write_text(_AREA_AUTH_YAML, encoding="utf-8")
    graph = standards_graph.build_graph(tmp_path)
    assert graph["parse_errors"] == []
    # exactly one STD-AUTH node (no duplicate), and it is the YAML form (prose
    # comes from the YAML `description:` field, which the .md body lacks)
    auth_nodes = [n for n in graph["nodes"] if n["id"] == "STD-AUTH"]
    assert len(auth_nodes) == 1
    assert "authenticate" in (auth_nodes[0].get("description") or "")
    # no duplicated child nodes/edges either
    rule_ids = [n["id"] for n in graph["nodes"] if n["id"] == "STD-AUTH-RG1-R1"]
    assert len(rule_ids) == 1
    edge_keys = [(e["from"], e["to"], e["kind"]) for e in graph["edges"]]
    assert len(edge_keys) == len(set(edge_keys))


def test_yaml_prose_from_field(tmp_path):
    """Prose (description/rationale) is read from YAML fields — no markdown body
    needed, and it is threaded onto the node for the renderer."""
    std = tmp_path / "harness" / "standards"
    _write_singletons(std)
    (std / "areas" / "STD-AUTH.std.yaml").write_text(_AREA_AUTH_YAML, encoding="utf-8")
    graph = standards_graph.build_graph(tmp_path)
    area = _node(graph, "STD-AUTH")
    rule = _node(graph, "STD-AUTH-RG1-R1")
    assert "authenticate" in (area.get("description") or "")
    assert "theft window" in (rule.get("rationale") or "")


def test_malformed_yaml_failsoft(tmp_path):
    """A malformed `.std.yaml` becomes a parse_error sentinel — build_graph never
    raises (fail-soft never-raise contract preserved)."""
    std = tmp_path / "harness" / "standards"
    _write_singletons(std)
    (std / "areas" / "STD-BAD.std.yaml").write_text(
        "id: STD-BAD\ntype: std_area\n  bad: : indent\n: oops\n", encoding="utf-8")
    graph = standards_graph.build_graph(tmp_path)  # must not raise
    assert any("STD-BAD.std.yaml" in pe["file"] for pe in graph["parse_errors"])


def test_yaml_non_mapping_failsoft(tmp_path):
    """A `.std.yaml` whose top level is a list/scalar (not a mapping) is a
    parse_error, not a crash."""
    std = tmp_path / "harness" / "standards"
    _write_singletons(std)
    (std / "areas" / "STD-LIST.std.yaml").write_text(
        "- just\n- a\n- list\n", encoding="utf-8")
    graph = standards_graph.build_graph(tmp_path)
    assert any("STD-LIST.std.yaml" in pe["file"] for pe in graph["parse_errors"])
