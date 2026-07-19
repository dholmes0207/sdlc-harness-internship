"""Structural invariant (mirrors test_gemini_single_chokepoint.py): every
CcsPrintTransport(...) construction in partner_companion.py lives inside
partner_call — the ONE chokepoint made mechanical. A later phase that adds a
second spawn site (a bypass write path, a stray fallback) turns this test RED
instead of silently growing a second lane.
"""
import ast
from pathlib import Path

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
_MODULE = _PLUGIN_SCRIPTS / "partner_companion.py"

_SPAWN_NAMES = {"CcsPrintTransport"}
_ALLOWED_FUNCS = {"partner_call"}


def _spawn_sites(tree):
    """(enclosing_func_name, lineno) for every _SPAWN_NAMES(...) constructor call."""
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def enclosing_func(node):
        cur = parents.get(node)
        while cur is not None:
            if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return cur.name
            cur = parents.get(cur)
        return None

    sites = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in _SPAWN_NAMES):
            sites.append((enclosing_func(node), node.lineno))
    return sites


def test_transport_spawn_only_from_chokepoint():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    sites = _spawn_sites(tree)
    assert sites, "expected at least one CcsPrintTransport(...) construction site"
    offenders = [(f, ln) for (f, ln) in sites if f not in _ALLOWED_FUNCS]
    assert not offenders, (
        "ccs transport spawned outside the chokepoint (partner_call): %s" % offenders)
