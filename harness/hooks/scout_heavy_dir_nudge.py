#!/usr/bin/env python3
"""scout_heavy_dir_nudge.py — advisory context-economy reminder (nudge class).

A read into a heavy / generated directory (node_modules, dist, .venv, build, …)
burns the model's context budget for little signal. This nudge spots a Read / Grep
/ Glob whose target points into such a dir and prints a one-line reminder to prefer
a narrower path, an excluding glob, or a packed digest (hs:repomix). It NEVER blocks
— "govern but don't brick": a legitimate read into node_modules still goes through.

Default-OFF (the nudge convention): enabling it wires a per-read advisory, a
conscious context-economy-over-latency choice (every Read/Grep/Glob pays one hook
spawn). The binding HOOK_CLASS lives here in code, not in config.
"""
import os
import re
import sys
from pathlib import Path

try:
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HOOKS_DIR, "..", "scripts"))
if _HOOKS_DIR not in sys.path:
    sys.path.append(_HOOKS_DIR)
import hook_runtime  # noqa: E402

HOOK_CLASS = "nudge"
_NAME = Path(__file__).stem

# Heavy / generated dirs that rarely repay the context they cost to read.
_HEAVY = {
    "node_modules", "dist", "build", "out", ".next", ".nuxt", "vendor",
    ".venv", "venv", "target", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".cache", "coverage", ".gradle", ".terraform",
    # "env" intentionally omitted — too short, would false-flag legitimate dirs
}
# Path-ish tool_input fields per read tool. Grep's `pattern` is a CONTENT regex,
# NOT a path — excluded so a regex that merely mentions a heavy-dir name cannot
# false-positive. Glob's `pattern` IS a path glob, so it is read.
_PATH_FIELDS = {
    "Read": ("file_path",),
    "Grep": ("path", "glob"),
    "Glob": ("pattern", "path"),
}
_SEG_RE = re.compile(r"[\\/]+")


def _heavy_hit(target: str):
    """The first heavy-dir path segment in `target`, or None."""
    if not target:
        return None
    for seg in _SEG_RE.split(str(target)):
        if seg in _HEAVY:
            return seg
    return None


def core(data: dict):
    """Return one advisory line iff a Read/Grep/Glob targets a heavy dir. Only
    path-ish fields are read per tool (Grep's content `pattern` is never a path)."""
    if not isinstance(data, dict):
        return None
    fields = _PATH_FIELDS.get(data.get("tool_name"))
    if not fields:
        return None
    inp = data.get("tool_input") or {}
    for field in fields:
        target = inp.get(field) or ""
        hit = _heavy_hit(target)
        if hit:
            return (
                f"context economy: this {data.get('tool_name')} reaches into "
                f"'{hit}' ({target}) — a heavy/generated dir. Prefer a narrower "
                f"path, a glob that excludes it, or `hs:repomix` for a digest. "
                f"(advisory — not blocked)"
            )
    return None


def main() -> int:
    hook_runtime.run_nudge_hook(_NAME, core)
    return 0


if __name__ == "__main__":
    sys.exit(main())
