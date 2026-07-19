"""The scout heavy-dir context-economy nudge (advisory, fail-open, shipped ON).

A Read/Grep/Glob into a heavy/generated dir (node_modules, dist, .venv, …) burns
context for little signal. This nudge spots it and prints a one-line reminder; it
never blocks ("govern but don't brick"). Shipped ON in harness-hooks.yaml (dogfood
default), still fail-open and never blocking.
"""
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
HOOKS = ROOT / "harness" / "hooks"
sys.path.insert(0, str(HOOKS))
import scout_heavy_dir_nudge as shd  # noqa: E402


def test_read_into_node_modules_nudges():
    msg = shd.core({"tool_name": "Read",
                    "tool_input": {"file_path": "pkg/node_modules/lib/x.js"}})
    assert msg and "node_modules" in msg
    assert "not blocked" in msg.lower()  # advisory, never blocks


def test_grep_into_dist_nudges():
    msg = shd.core({"tool_name": "Grep",
                    "tool_input": {"path": "frontend/dist"}})
    assert msg and "dist" in msg


def test_glob_pattern_into_venv_nudges():
    msg = shd.core({"tool_name": "Glob",
                    "tool_input": {"pattern": ".venv/**/*.py"}})
    assert msg and ".venv" in msg


def test_normal_read_is_silent():
    assert shd.core({"tool_name": "Read",
                     "tool_input": {"file_path": "harness/scripts/install.py"}}) is None


def test_non_read_tool_is_silent():
    # the nudge only watches Read/Grep/Glob — a Bash into node_modules is out of scope
    assert shd.core({"tool_name": "Bash",
                     "tool_input": {"command": "ls node_modules"}}) is None


def test_registered_but_default_off():
    # migrated into the in-process dispatcher (PreToolUse:Read|Grep|Glob): it fires as a
    # core of hook_dispatch.py, registered in hook-dispatch.yaml rather than its own command.
    # Kept WIRED (this registration assert) but shipped OFF (below).
    disp = yaml.safe_load((ROOT / "harness/data/hook-dispatch.yaml").read_text())
    assert any(c.get("module") == "scout_heavy_dir_nudge"
               for grp in disp["groups"].values() for c in grp)
    cfg = yaml.safe_load((ROOT / "harness/data/harness-hooks.yaml").read_text())
    entry = (cfg.get("hooks") or {}).get("scout_heavy_dir_nudge") or {}
    # Shipped OFF: its advisory routes to a stderr-only channel (model:false,user:false)
    # that reaches no reader, so it must not pay a hook spawn on every Read/Grep/Glob.
    assert entry.get("enabled") is False


def test_main_is_fail_open_on_garbage_stdin(tmp_path):
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(yaml.safe_dump(
        {"hooks": {"scout_heavy_dir_nudge": {"enabled": True}}}))
    env = {"PATH": "/usr/bin:/bin", "HARNESS_HOOK_CONFIG": str(cfg),
           "CLAUDE_PROJECT_DIR": str(tmp_path)}
    proc = subprocess.run(
        [sys.executable, str(HOOKS / "scout_heavy_dir_nudge.py")],
        input="this is not json", capture_output=True, text=True, env=env)
    assert proc.returncode == 0


def test_grep_content_pattern_is_not_a_path():
    # Grep's `pattern` is a content regex; a regex mentioning a heavy dir name is
    # NOT a read into it and must stay silent (no false-positive).
    assert shd.core({"tool_name": "Grep",
                     "tool_input": {"pattern": "import .* from node_modules"}}) is None


def test_grep_path_still_nudges():
    assert shd.core({"tool_name": "Grep",
                     "tool_input": {"pattern": "TODO", "path": "web/dist"}})
