"""Tests for plannotator_wrapper_guard.py — force the annotate review through the wrapper.

Two entries share one module and one break-glass switch:
  * bash_core  (PreToolUse:Bash)  — blocks a bare `plannotator annotate ...` shell command.
  * skill_core (PreToolUse:Skill) — blocks a Skill(plannotator-annotate) call.

Both redirect to `harness/scripts/plannotator_surface.py annotate`, which injects `--gate`
(the Approve button) and globs the whole phase directory. Scope is annotate ONLY: `review`
has no `--gate` flag in the binary, so gating it would buy tracing not an Approve button.

The core logic is exercised directly (None ⇒ allow, str ⇒ block reason). The compliance
crash path FAILS OPEN by design (each core swallows its own errors → None + the dispatch
rows carry fail_open) so a guard crash never wedges a whole Bash/Skill lane. One subprocess
test locks the standalone main() exit-2 contract via the HARNESS_HOOK_CONFIG seam.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
HOOK_PATH = _HOOKS / "plannotator_wrapper_guard.py"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import plannotator_wrapper_guard as guard  # noqa: E402


def _bash(cmd: str) -> dict:
    return {"tool_name": "Bash", "tool_input": {"command": cmd}}


# --- bash_core: block the bare binary --------------------------------------------------

def test_bash_block_bare_annotate():
    reason = guard.bash_core(_bash("plannotator annotate plan.md"))
    assert reason and "plannotator_surface.py" in reason
    assert "Approve" in reason


def test_bash_allow_bare_review():
    # DEC-E: review has no --gate → gating it buys only tracing, so it is left alone.
    assert guard.bash_core(_bash("plannotator review HEAD~1")) is None


def test_bash_block_annotate_with_gate():
    # DEC-C revised: no `--gate` escape. The wrapper globs the phase DIRECTORY; a bare
    # `annotate x --gate` opens one file only → reviewer can Approve without seeing the
    # other phase files (F5). So a bare annotate is blocked unconditionally.
    assert guard.bash_core(_bash("plannotator annotate plan.md --gate")) is not None


def test_bash_block_absolute_binary():
    assert guard.bash_core(_bash("/usr/local/bin/plannotator annotate x")) is not None


def test_bash_allow_wrapper():
    # token is `plannotator_surface.py`, not `plannotator annotate` (an `_` follows).
    assert guard.bash_core(
        _bash("python3 harness/scripts/plannotator_surface.py annotate plan.md")) is None


def test_bash_allow_absolute_wrapper():
    assert guard.bash_core(
        _bash("python3 /abs/harness/scripts/plannotator_surface.py review x")) is None


def test_bash_allow_quoted_mention():
    assert guard.bash_core(_bash('echo "run plannotator annotate later"')) is None


def test_bash_allow_comment_mention():
    assert guard.bash_core(_bash("ls  # plannotator annotate note")) is None


def test_bash_allow_git_commit_msg():
    assert guard.bash_core(_bash('git commit -m "use plannotator annotate"')) is None


def test_bash_ignore_non_bash():
    assert guard.bash_core({"tool_name": "Read", "tool_input": {"file_path": "x"}}) is None


def test_bash_empty_command():
    assert guard.bash_core(_bash("")) is None
    assert guard.bash_core(_bash("   ")) is None


def test_bash_core_crash_fails_open(monkeypatch):
    # F1: an internal error must return None (fail-open), never raise — a guard crash
    # must not wedge the Bash lane. The dispatch row's fail_open is the second belt.
    def _boom(_data):
        raise RuntimeError("boom")

    monkeypatch.setattr(guard.hook_runtime, "bash_command", _boom)
    assert guard.bash_core(_bash("plannotator annotate x")) is None


# --- standalone main() exit-code contract (compliance wrapper) -------------------------

def _cfg_enabled(tmp_path: Path) -> Path:
    p = tmp_path / "hooks.yaml"
    p.write_text("hooks:\n  plannotator_wrapper_guard: {enabled: true}\n", encoding="utf-8")
    return p


def _run(config: Path, payload: dict):
    env = dict(os.environ)
    env["HARNESS_HOOK_CONFIG"] = str(config)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps(payload), text=True, capture_output=True, env=env,
    )


def test_main_blocks_bare_annotate_exit2(tmp_path):
    p = _run(_cfg_enabled(tmp_path), _bash("plannotator annotate plan.md"))
    assert p.returncode == 2, p.stderr
    assert "plannotator_surface.py" in p.stderr


def test_main_allows_wrapper_exit0(tmp_path):
    p = _run(_cfg_enabled(tmp_path),
             _bash("python3 harness/scripts/plannotator_surface.py annotate plan.md"))
    assert p.returncode == 0, p.stderr


# --- skill_core: block the gated Skill call (annotate only) ----------------------------

def _skill(name: str, field: str = "skill") -> dict:
    return {"tool_name": "Skill", "tool_input": {field: name}}


def test_skill_block_annotate():
    reason = guard.skill_core(_skill("plannotator-annotate"))
    assert reason and "plannotator_surface.py" in reason


def test_skill_pass_review():
    # DEC-E: review has no --gate → not gated.
    assert guard.skill_core(_skill("plannotator-review")) is None


def test_skill_block_with_slash():
    assert guard.skill_core(_skill("/plannotator-annotate")) is not None


def test_skill_pass_hs_plan():
    assert guard.skill_core(_skill("hs:plan")) is None


def test_skill_pass_plannotator_last():
    # DEC-D scope: the other 4 plannotator skills pass.
    assert guard.skill_core(_skill("plannotator-last")) is None


def test_skill_block_name_field():
    # payload carries `name` instead of `skill` → still resolves + blocks.
    assert guard.skill_core(_skill("plannotator-annotate", field="name")) is not None


def test_skill_empty():
    assert guard.skill_core({"tool_name": "Skill", "tool_input": {}}) is None
    assert guard.skill_core({"tool_name": "Skill"}) is None


def test_skill_block_hs_prefixed():
    # the hs: prefix is stripped BEFORE the gated-set check → still blocks (block-side of
    # the strip branch, not just the pass-through case).
    assert guard.skill_core(_skill("hs:plannotator-annotate")) is not None


def test_skill_ignore_non_skill():
    # a Bash payload resolves to no skill slug → None (skill_core only gates Skill payloads).
    assert guard.skill_core(_bash("plannotator annotate x")) is None


def test_skill_ignore_non_skill_tool_with_gated_name():
    # symmetric tool-name guard: a non-Skill payload carrying name=plannotator-annotate
    # must NOT block (skill_core gates only a Skill tool call).
    assert guard.skill_core(
        {"tool_name": "Bash", "tool_input": {"name": "plannotator-annotate"}}) is None


def test_skill_core_crash_fails_open(monkeypatch):
    def _boom(_data):
        raise RuntimeError("boom")

    monkeypatch.setattr(guard, "_skill_slug", _boom)
    assert guard.skill_core(_skill("plannotator-annotate")) is None


# --- break-glass toggle: one switch silences BOTH lanes (F3 single-switch) -------------

_DISPATCH = _HOOKS / "hook_dispatch.py"
_REPO = Path(__file__).resolve().parents[2]


def _cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "hooks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _dispatch(event: str, matcher: str, payload: dict, config: Path, state: Path):
    # Drive the REAL in-process dispatcher — the only surface where both lanes share the
    # one name, so a single enabled:false must silence both (memory: drive the dispatcher,
    # not just the unit). Telemetry off + isolated state keep the run side-effect-free.
    env = dict(os.environ)
    env["HARNESS_HOOK_CONFIG"] = str(config)
    env["HARNESS_STATE_DIR"] = str(state)
    env["HARNESS_TELEMETRY_DISABLED"] = "1"
    env["CLAUDE_PROJECT_DIR"] = str(_REPO)
    return subprocess.run(
        [sys.executable, str(_DISPATCH), event, matcher],
        input=json.dumps(payload), text=True, capture_output=True, env=env)


def test_toggle_default_on_blocks_bare_annotate(tmp_path):
    # No toggle key → compliance default ON → the gate still blocks (the toggle line is
    # break-glass visibility, not a requirement for the gate to run).
    cfg = _cfg(tmp_path, "hooks:\n  some_other_hook: {enabled: true}\n")
    p = _run(cfg, _bash("plannotator annotate plan.md"))
    assert p.returncode == 2, p.stderr


def test_toggle_disable_silences_both_lanes(tmp_path):
    # F3 single-switch: ONE enabled:false silences the Bash AND the Skill lane.
    cfg = _cfg(tmp_path, "hooks:\n  plannotator_wrapper_guard: {enabled: false}\n")
    st = tmp_path / "state"
    pb = _dispatch("PreToolUse", "Bash", _bash("plannotator annotate plan.md"), cfg, st)
    assert pb.returncode == 0, "bash lane must be silenced: %s" % pb.stderr
    ps = _dispatch("PreToolUse", "Skill", _skill("plannotator-annotate"), cfg, st)
    assert ps.returncode == 0, "skill lane must be silenced: %s" % ps.stderr


def test_toggle_enable_blocks_both_lanes(tmp_path):
    cfg = _cfg(tmp_path, "hooks:\n  plannotator_wrapper_guard: {enabled: true}\n")
    st = tmp_path / "state"
    pb = _dispatch("PreToolUse", "Bash", _bash("plannotator annotate plan.md"), cfg, st)
    assert pb.returncode == 2, pb.stderr
    ps = _dispatch("PreToolUse", "Skill", _skill("plannotator-annotate"), cfg, st)
    assert ps.returncode == 2, ps.stderr
