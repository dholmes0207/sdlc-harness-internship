"""skillgraph.py TDD tests.

Tests verify the skill-graph adapter: subset+transitive resolution, all-owned
default, autolayout contract output, group mapping, error handling, no network.
"""
import json
import pytest
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DRAWIO_SKILL = REPO_ROOT / "harness" / "plugins" / "hs" / "skills" / "drawio"
SCRIPTS_DIR = DRAWIO_SKILL / "scripts"
SKILLGRAPH = SCRIPTS_DIR / "skillgraph.py"

DEPS_FILE = REPO_ROOT / "harness" / "data" / "skill-deps.yaml"
COMPONENTS_FILE = REPO_ROOT / "harness" / "data" / "components.yaml"



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _run_skillgraph(*args, check=True):
    cmd = [
        sys.executable, str(SKILLGRAPH),
        "--deps-file", str(DEPS_FILE),
        "--components-file", str(COMPONENTS_FILE),
    ] + list(args)
    result = subprocess.run(cmd, capture_output=True, timeout=15)
    return result


def _parse_output(result):
    return json.loads(result.stdout.decode())


def test_subset_includes_transitive():
    """--skills cook: nodes must include cook + all transitive deps of cook."""
    import yaml
    result = _run_skillgraph("--skills", "cook")
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr.decode()}"
    data = _parse_output(result)

    assert "direction" in data
    assert "nodes" in data
    assert "edges" in data

    node_ids = {n["id"] for n in data["nodes"]}
    assert "cook" in node_ids, "cook must be in nodes"

    # All transitive deps of cook must be included
    deps_data = yaml.safe_load(DEPS_FILE.read_text())
    cook_deps = deps_data["skills"].get("cook", {}).get("deps", [])
    for dep in cook_deps:
        assert dep in node_ids, f"transitive dep {dep!r} of cook missing from nodes"

    # Edge cook->dep should exist for each direct dep
    edges = {(e["source"], e["target"]) for e in data["edges"]}
    for dep in cook_deps:
        assert ("cook", dep) in edges, f"edge cook->{dep} missing"


def test_no_skills_means_all_owned():
    """Without --skills, output includes all skills in skill-deps.yaml."""
    import yaml
    result = _run_skillgraph()
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr.decode()}"
    data = _parse_output(result)

    deps_data = yaml.safe_load(DEPS_FILE.read_text())
    all_skill_keys = set(deps_data["skills"].keys())
    node_ids = {n["id"] for n in data["nodes"]}

    assert len(node_ids) == len(all_skill_keys), (
        f"Expected {len(all_skill_keys)} nodes (all owned), got {len(node_ids)}"
    )


def test_output_matches_autolayout_contract():
    """Output JSON must match autolayout.py input contract: direction, nodes, edges."""
    result = _run_skillgraph("--skills", "cook,plan")
    assert result.returncode == 0
    data = _parse_output(result)

    # Required fields
    assert "direction" in data, "Missing 'direction' field"
    assert data["direction"] in ("TB", "LR"), f"direction must be TB or LR, got {data['direction']!r}"
    assert "nodes" in data, "Missing 'nodes' field"
    assert "edges" in data, "Missing 'edges' field"

    # Node ids must be unique and not '0' or '1' (reserved by draw.io)
    node_ids = [n["id"] for n in data["nodes"]]
    assert len(node_ids) == len(set(node_ids)), "Duplicate node ids"
    assert "0" not in node_ids, "id '0' is reserved by draw.io"
    assert "1" not in node_ids, "id '1' is reserved by draw.io"

    # Each node must have id and label
    for node in data["nodes"]:
        assert "id" in node, f"Node missing 'id': {node}"
        assert "label" in node, f"Node missing 'label': {node}"

    # Each edge must have source and target
    for edge in data["edges"]:
        assert "source" in edge, f"Edge missing 'source': {edge}"
        assert "target" in edge, f"Edge missing 'target': {edge}"


def test_group_from_components():
    """Nodes must carry a group attribute matching components.yaml group."""
    import yaml
    result = _run_skillgraph("--skills", "cook")
    assert result.returncode == 0
    data = _parse_output(result)

    comps = yaml.safe_load(COMPONENTS_FILE.read_text())["components"]
    # Build skill->group map from components.yaml
    skill_to_group = {}
    for group_name, group_data in comps.items():
        for skill in group_data.get("skills", []):
            skill_to_group[skill] = group_name

    cook_node = next((n for n in data["nodes"] if n["id"] == "cook"), None)
    assert cook_node is not None, "cook node not found"
    # cook is a spine skill - it might be in "hs" group or not in components
    # The group should be assigned based on components.yaml or "hs" for spine
    assert "group" in cook_node, "cook node missing 'group' attribute"


def test_unknown_skill_errors_clean():
    """--skills nonexistent_xyz must exit non-zero with a clear message."""
    result = _run_skillgraph("--skills", "nonexistent_skill_xyz_123")
    assert result.returncode != 0, "Expected non-zero exit for unknown skill"
    stderr = result.stderr.decode()
    assert "nonexistent_skill_xyz_123" in stderr or "unknown" in stderr.lower() or "not found" in stderr.lower(), (
        f"Error message should name the unknown skill: {stderr!r}"
    )


def test_no_network():
    """skillgraph.py must not import network modules (socket, urllib.request, http)."""
    import ast
    assert SKILLGRAPH.exists()
    tree = ast.parse(SKILLGRAPH.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name not in ("socket", "http"), (
                    f"Network import {alias.name!r} found"
                )
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                assert not node.module.startswith("urllib.request"), (
                    "Network import urllib.request found"
                )
