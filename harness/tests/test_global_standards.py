#!/usr/bin/env python3
"""P3 — the language-agnostic disciplines extracted into the shipped std tree.

Four cross-cutting rules (case-insensitive compare, enum fail-closed, path/glob
traversal guard, red->green TDD) go into STD-REVIEW-COMMON; two Python idioms
(atomic write via os.replace, JSONL object guard) go into STD-REVIEW-PY — NOT
COMMON, because their mechanism is Python-specific. All start advisory (info,
no floor) with detector:null (LLM-judged, consistent with the existing COMMON
rules — a "must-do" discipline is not a low-FP grep target).
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import rule_view  # noqa: E402
import standards_graph  # noqa: E402

_COMMON_NEW = {
    "STD-REVIEW-COMMON-RG2-R1",   # case-insensitive + trimmed compare
    "STD-REVIEW-COMMON-RG2-R2",   # enum fail-closed
    "STD-REVIEW-COMMON-RG2-R3",   # path/glob traversal guard
    "STD-REVIEW-COMMON-RG2-R4",   # red->green TDD
}
_PY_NEW = {
    "STD-REVIEW-PY-RG2-R1",       # atomic write (tmp + os.replace)
    "STD-REVIEW-PY-RG2-R2",       # JSONL object guard
}


def _nodes_by_id():
    graph = standards_graph.build_graph(_REPO_ROOT)
    return {n.get("id"): n for n in graph["nodes"] if n.get("type") == "rule"}


# (a) the six new rules exist in the operational tree.
def test_new_rules_present():
    ids = set(_nodes_by_id())
    assert _COMMON_NEW <= ids, _COMMON_NEW - ids
    assert _PY_NEW <= ids, _PY_NEW - ids


# (b) all six are advisory: detector null, severity info, not floor.
def test_new_rules_are_advisory():
    nodes = _nodes_by_id()
    for rid in _COMMON_NEW | _PY_NEW:
        n = nodes[rid]
        assert n.get("detector") is None, (rid, n.get("detector"))
        assert n.get("severity") == "info", (rid, n.get("severity"))
        assert n.get("floor") is False, rid


# (c) scope routing: COMMON rules match any file, PY rules only Python files.
def test_scope_routing():
    py = set(rule_view.load_rules(_REPO_ROOT, scope_intersects=["x.py"])["rules_applied"])
    md = set(rule_view.load_rules(_REPO_ROOT, scope_intersects=["x.md"])["rules_applied"])
    assert _COMMON_NEW <= py and _COMMON_NEW <= md     # cross-cutting -> both
    assert _PY_NEW <= py                                # python -> .py
    assert not (_PY_NEW & md)                           # python -> NOT .md


# (d) the std tree still builds clean (no parse error introduced).
def test_tree_builds_clean():
    graph = standards_graph.build_graph(_REPO_ROOT)
    assert not graph.get("parse_errors"), graph.get("parse_errors")
