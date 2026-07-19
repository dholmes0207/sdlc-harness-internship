"""test_dispatch_matcher_shell_safe.py — a dispatcher matcher with a `|` MUST be
shell-quoted in its wired command.

Claude Code runs each hook command through a shell (`sh -c "<command>"`). The
dispatcher takes the matcher as a positional arg — `hook_dispatch.py PreToolUse
Read|Write|Edit|MultiEdit|Glob|Grep|Bash`. Unquoted, the shell reads every `|`
as a PIPE: it runs `hook_dispatch.py PreToolUse Read` and pipes its output into
`Write`, `Edit`, … as if they were commands (`sh: Write: not found`). The
dispatcher then only ever sees matcher `Read`, so the whole group's gates
(write_guard, agent_rbac, scout_block, explore_model, privacy_read) are SILENTLY
skipped — a gate bypass, not just noise.

These guard both layers: the source registration (hooks-registration.yaml) and,
the real catch, each command driven through an actual `sh -c` the way CC drives
it — a static shlex check does NOT reproduce the bug because shlex never treats
`|` as a pipe.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parents[2]
_REGISTRATION = _ROOT / "harness" / "install" / "hooks-registration.yaml"

_DISPATCH_RE = re.compile(r"hook_dispatch\.py\s+([A-Za-z]+)(?:\s+(\S+))?")


def _dispatch_commands():
    reg = yaml.safe_load(_REGISTRATION.read_text(encoding="utf-8")) or {}
    for entry in (reg.get("hooks") or []):
        if not isinstance(entry, dict):
            continue
        cmd = entry.get("command", "")
        if "hook_dispatch.py" in cmd:
            yield cmd


def test_piped_matcher_is_quoted_in_registration():
    # Static guard: any dispatch command whose matcher token carries a `|` must
    # wrap that token in quotes, or the shell splits it into a pipeline.
    offenders = []
    for cmd in _dispatch_commands():
        m = _DISPATCH_RE.search(cmd)
        if not m:
            continue
        matcher = m.group(2)
        if matcher and "|" in matcher and not (matcher.startswith(('"', "'"))):
            offenders.append(cmd)
    assert not offenders, (
        "dispatch matcher with `|` not shell-quoted (shell will read it as a pipe):\n"
        + "\n".join(offenders))


def _render(cmd: str, root: Path) -> str:
    return (cmd.replace("$HARNESS_PY", sys.executable)
               .replace("$HARNESS_ROOT", str(root)))


@pytest.mark.parametrize("cmd", list(_dispatch_commands()))
def test_command_survives_real_shell(cmd):
    # The real catch: drive each command through `sh -c` exactly as Claude Code
    # does. A shell-mangled command surfaces as `sh: <Tool>: not found` on stderr
    # or the dispatcher's own "no dispatch group" warning (it saw a truncated
    # matcher). Neither may appear; the process must exit 0 (continue) or 2 (block).
    m = _DISPATCH_RE.search(cmd)
    event = m.group(1)
    payload = {"PreToolUse": {"tool_name": "Bash", "tool_input": {"command": "ls"}},
               "PostToolUse": {"tool_name": "Write", "tool_input": {"file_path": "/tmp/x"}}}
    stdin = json.dumps(payload.get(event, {"session_id": "shell-safe"}))
    env = dict(os.environ, HARNESS_PY=sys.executable, HARNESS_ROOT=str(_ROOT))
    rendered = _render(cmd, _ROOT)
    r = subprocess.run(["sh", "-c", rendered], input=stdin, text=True,
                       capture_output=True, env=env)
    assert "not found" not in r.stderr, (
        "shell mangled the command (unquoted matcher pipe?):\n%s\n%s" % (cmd, r.stderr))
    assert "no dispatch group" not in r.stderr, (
        "dispatcher saw a truncated matcher — the shell split the pipe:\n%s\n%s"
        % (cmd, r.stderr))
    assert r.returncode in (0, 2), "unexpected exit %d for %s\n%s" % (
        r.returncode, cmd, r.stderr)
