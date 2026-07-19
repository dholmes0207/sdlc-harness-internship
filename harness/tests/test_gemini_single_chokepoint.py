"""Structural invariant (F3): every engine-spawn in the chokepoint module lives
inside partner_call or _canary — the ONE chokepoint made mechanical.

A transport/client is only ever constructed from the single call path
(partner_call) or the setup canary. A later phase that adds a second spawn site
(a fallback that news its own client, a write path that bypasses partner_call)
turns this test RED instead of silently growing a second lane. subprocess.run is
an Attribute call, not a Name, so git/worktree plumbing is not caught here.
"""
import ast
from pathlib import Path

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
_MODULE = _PLUGIN_SCRIPTS / "gemini_companion.py"

_SPAWN_NAMES = {"GeminiPrintTransport", "PrintTransport"}
# The single call path (partner_call) plus the setup canaries — the sanctioned
# reachability probes (one gemini print, one agy print). Any OTHER function
# constructing an engine is a second lane and fails this test.
_ALLOWED_FUNCS = {"partner_call", "_canary", "_canary_agy"}


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


def test_engine_spawn_only_from_chokepoint():
    tree = ast.parse(_MODULE.read_text(encoding="utf-8"))
    sites = _spawn_sites(tree)
    assert sites, "expected at least one engine-spawn site (GeminiPrintTransport/PrintTransport)"
    offenders = [(f, ln) for (f, ln) in sites if f not in _ALLOWED_FUNCS]
    assert not offenders, (
        "engine spawned outside the chokepoint (partner_call/_canary): %s" % offenders)
