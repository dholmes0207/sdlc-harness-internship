"""scout_block_guard — opt-in HARD block on file-tool access into heavy/generated dirs.

core() returns None (pass) or a block reason string (the run_compliance_hook contract).
Reuses scout_heavy_dir_nudge's heavy-dir detection; the gate is the compliance upgrade.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for _p in (_ROOT / "harness" / "scripts", _ROOT / "harness" / "hooks"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
import scout_block_guard as sb  # noqa: E402


def _data(tool, **inp):
    return {"tool_name": tool, "tool_input": inp}


def test_read_into_node_modules_blocks():
    reason = sb.core(_data("Read", file_path="proj/node_modules/x/index.js"))
    assert reason and "node_modules" in reason


def test_read_normal_path_passes():
    assert sb.core(_data("Read", file_path="harness/scripts/catalog.py")) is None


def test_write_into_dist_blocks():
    reason = sb.core(_data("Write", file_path="app/dist/bundle.js"))
    assert reason and "dist" in reason


def test_grep_path_into_venv_blocks():
    reason = sb.core(_data("Grep", pattern="TODO", path=".venv/lib/site.py"))
    assert reason and ".venv" in reason


def test_grep_content_pattern_naming_heavy_dir_does_not_block():
    # Grep's content `pattern` is NOT a path — a regex that merely mentions a heavy
    # dir name must pass (only path/glob fields are treated as paths).
    assert sb.core(_data("Grep", pattern="node_modules", path="src")) is None


def test_non_path_tool_passes():
    # Bash is left to the advisory nudge — a hard block here would brick build commands.
    assert sb.core(_data("Bash", command="ls node_modules")) is None


def test_glob_into_build_blocks():
    reason = sb.core(_data("Glob", pattern="build/**/*.js"))
    assert reason and "build" in reason


def test_bash_cat_into_node_modules_blocks():
    r = sb.core(_data("Bash", command="cat node_modules/pkg/index.js"))
    assert r and "node_modules" in r


def test_bash_head_into_dist_blocks():
    r = sb.core(_data("Bash", command="head -n5 dist/bundle.js"))
    assert r and "dist" in r


def test_bash_build_command_passes():
    # a build/tooling command is never a leading read-command — no false-positive
    assert sb.core(_data("Bash", command="npm install --save lodash")) is None
    assert sb.core(_data("Bash", command="pip install -r requirements.txt")) is None


def test_bash_read_normal_path_passes():
    assert sb.core(_data("Bash", command="cat harness/scripts/catalog.py")) is None


def test_bash_only_leading_command_gated():
    # conservative scope: only the LEADING read-command is checked; a heavy path after
    # a shell operator is not gated (no attempt to parse arbitrary compound commands).
    assert sb.core(_data("Bash", command="echo hi && cat node_modules/x")) is None
