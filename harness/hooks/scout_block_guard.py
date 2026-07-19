#!/usr/bin/env python3
"""scout_block_guard.py — opt-in HARD block on file-tool access into heavy/generated dirs.

The advisory sibling scout_heavy_dir_nudge only REMINDS; this is its compliance upgrade:
when a deploy opts in (this gate ships OFF), a Read/Grep/Glob/Write/Edit whose target
points into a heavy/generated dir (node_modules, dist, .venv, build, …) is BLOCKED
(exit 2) instead of nudged — a deliberate context-economy-over-flexibility posture.

Reuses the nudge's heavy-dir detection (one source of truth, DRY). Bash is intentionally
NOT gated here: a hard block would brick legitimate build commands that must touch
node_modules — the advisory nudge covers Bash. Default OFF: the harness ships the
advisory nudge as the default; enabling this gate is the conscious "brick, don't just
govern" choice (harness/data/harness-hooks.yaml: scout_block_guard.enabled).
"""
import os
import sys
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HOOKS_DIR, "..", "scripts"))
if _HOOKS_DIR not in sys.path:
    sys.path.append(_HOOKS_DIR)
import hook_runtime  # noqa: E402
import scout_heavy_dir_nudge as nudge  # reuse _HEAVY / _heavy_hit (DRY)  # noqa: E402

HOOK_CLASS = "compliance"
_HOOK = Path(__file__).stem

# Path-ish fields per BLOCKED tool. A WRITE into a heavy dir is as wrong as a read;
# Grep's content `pattern` stays excluded (only its path/glob are real paths).
_PATH_FIELDS = {
    "Read": ("file_path",),
    "Write": ("file_path",),
    "Edit": ("file_path",),
    "MultiEdit": ("file_path",),
    "Grep": ("path", "glob"),
    "Glob": ("pattern", "path"),
}


# Bare read-commands whose ONLY job is to dump a file's bytes. A build/tooling
# command (npm/pip/make/…) is never one of these, so gating only the leading
# read-command can never false-positive a build that legitimately touches a heavy dir.
_READ_CMDS = {"cat", "head", "tail", "less", "more", "bat", "nl", "od",
              "xxd", "strings"}
_SHELL_OPS = {"&&", "||", "|", ";", "&", ">", ">>", "<", "<<"}


def _bash_heavy_read(command):
    """The heavy-dir segment a LEADING bare read-command targets, or None. Stops at
    the first shell operator — only the simple leading command is gated (conservative:
    no parsing of arbitrary compound commands, no build false-positive)."""
    import shlex
    try:
        toks = shlex.split(command or "")
    except ValueError:
        return None
    if not toks or os.path.basename(toks[0]) not in _READ_CMDS:
        return None
    for arg in toks[1:]:
        if arg in _SHELL_OPS:
            break
        if arg.startswith("-"):
            continue
        hit = nudge._heavy_hit(arg)
        if hit:
            return hit
    return None


def core(data):
    """None => pass; str => block reason (run_compliance_hook contract)."""
    if not isinstance(data, dict):
        return None
    if data.get("tool_name") == "Bash":
        hit = _bash_heavy_read(hook_runtime.bash_command(data))
        if hit:
            return (
                "scout-block: this Bash command reads into '%s' — a heavy/generated "
                "dir. Use `hs:repomix` for a digest or a narrower path. (Opt-in hard "
                "gate; flip scout_block_guard to enabled:false in "
                "harness/data/harness-hooks.yaml to allow heavy-dir access.)" % hit
            )
        return None
    fields = _PATH_FIELDS.get(data.get("tool_name"))
    if not fields:
        return None
    inp = data.get("tool_input") or {}
    for field in fields:
        target = inp.get(field) or ""
        hit = nudge._heavy_hit(target)
        if hit:
            return (
                "scout-block: this %s reaches into '%s' (%s) — a heavy/generated dir. "
                "Reading or writing it burns context for little signal. Use a narrower "
                "path, an excluding glob, or `hs:repomix` for a digest. (This hard gate "
                "is opt-in; flip scout_block_guard to enabled:false in "
                "harness/data/harness-hooks.yaml to allow heavy-dir access.)"
                % (data.get("tool_name"), hit, target)
            )
    return None


def main():
    hook_runtime.compliance_skip_or_run(_HOOK, core, skip_event="scout_block_skip")


if __name__ == "__main__":
    main()
