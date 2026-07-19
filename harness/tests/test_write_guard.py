"""test_write_guard.py — the tool-mediated config-edit gate, end to end.

Subprocess + real stdin JSON (the compliance contract): PreToolUse on
Write/Edit/MultiEdit must exit 2 with an actionable reason when the target is
a gate config file, exit 0 otherwise.

Honesty scope: this guard sees TOOL-mediated edits only. It cannot see a
Bash redirect or an editor outside the session — those are covered by the
git diff + manifest + pre-push backstop, and the guard's own naming must
never promise more (see the wording invariant).

Self-protection points pinned here: the guard's own switch file is on the
guard list; the enable decision ignores HARNESS_HOOK_CONFIG entirely (an env
var must not be able to switch the guard off); the extra_guarded YAML can
only ADD paths, never remove one.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_GUARD = _HOOKS / "write_guard.py"


def _mk_root(tmp_path: Path) -> Path:
    """Materialize the guarded layout in a temp root."""
    root = tmp_path / "proj"
    for rel in ("harness/hooks", "harness/data", "harness/scripts",
                "harness/install", "plans/260612-0900-x/artifacts", "docs"):
        (root / rel).mkdir(parents=True, exist_ok=True)
    return root


def _run(root: Path, tool: str, target, extra_env=None, payload_extra=None):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_HOOK_CONFIG", None)
    env["HARNESS_ROOT"] = str(root)
    env["HARNESS_STATE_DIR"] = str(root / "harness" / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(root / "logs")
    env["HARNESS_USER"] = "guard-tester"
    for k, v in (extra_env or {}).items():
        env[k] = v
    tool_input = {"file_path": str(target)}
    payload = {"tool_name": tool, "tool_input": tool_input,
               "session_id": "wg-test"}
    if payload_extra:
        payload.update(payload_extra)
    return subprocess.run([sys.executable, str(_GUARD)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env)


def _trace_events(root: Path):
    out = []
    trace = root / "harness" / "state" / "trace"
    if trace.is_dir():
        for f in sorted(trace.glob("trace-*.jsonl")):
            out.extend(json.loads(l)
                       for l in f.read_text(encoding="utf-8").splitlines())
    return out


class TestBlocksGuardedTargets:
    @pytest.mark.parametrize("rel", [
        "harness/data/stage-policy.yaml",
        "harness/data/ownership.yaml",
        "harness/data/task-store.yaml",
        "harness/data/harness-hooks.yaml",
        "harness/data/write-guard.yaml",       # the guard's own switch
        "harness/hooks/gate_stage.py",
        "harness/hooks/write_guard.py",
        "harness/scripts/artifact_check.py",
        "harness/scripts/stage_detector.py",
        "harness/scripts/fs_guard.py",
        "harness/scripts/claims.py",
        "harness/scripts/component_config.py",   # projects component sel onto hook enable
        "harness/data/components.yaml",          # toggling a component can flip a blocking gate
        "harness/data/component-policy.yaml",     # selection source-of-truth
        "harness/scripts/plan_approval.py",
        "harness/scripts/task_store.py",
        "harness/scripts/task_store_http.py",
        "harness/scripts/task_store_github.py",
        "harness/scripts/task_store_gitlab.py",
        "harness/install/git-pre-push-hook.sh",
        "harness/install/hooks-registration.yaml",
        "plans/260612-0900-x/artifacts/plan-approval.json",
        "plans/260612-0900-x/artifacts/plan-approval.yaml",
    ])
    def test_write_into_guard_list_blocks_exit_two(self, tmp_path, rel):
        root = _mk_root(tmp_path)
        proc = _run(root, "Write", root / rel)
        assert proc.returncode == 2, "%s: rc=%s stderr=%s" % (
            rel, proc.returncode, proc.stderr[:200])
        assert rel.split("/")[-1] in proc.stderr  # names the blocked file
        assert "editor" in proc.stderr.lower()    # names the break-glass route

    def test_edit_and_multiedit_same_behavior(self, tmp_path):
        root = _mk_root(tmp_path)
        for tool in ("Edit", "MultiEdit"):
            proc = _run(root, tool, root / "harness/data/stage-policy.yaml")
            assert proc.returncode == 2, tool

    def test_relative_traversal_to_guarded_target_blocks(self, tmp_path):
        root = _mk_root(tmp_path)
        sneaky = root / "docs" / ".." / "harness" / "data" / "stage-policy.yaml"
        proc = _run(root, "Write", sneaky)
        assert proc.returncode == 2

    def test_case_variant_dir_blocks(self, tmp_path):
        # F1: on a case-insensitive FS (macOS, Windows) `harness/HOOKS/gate_stage.py`
        # opens the REAL guarded file; the match is now case-insensitive so the
        # upper-case spelling blocks too (matches privacy_read_guard's read stance).
        root = _mk_root(tmp_path)
        proc = _run(root, "Write", root / "harness/HOOKS/gate_stage.py")
        assert proc.returncode == 2, proc.stderr[:200]

    def test_block_is_traced(self, tmp_path):
        root = _mk_root(tmp_path)
        _run(root, "Write", root / "harness/data/stage-policy.yaml")
        events = _trace_events(root)
        assert any(e["event"] == "gate_block" and "stage-policy.yaml" in str(e.get("target"))
                   for e in events)


class TestAllowsNormalTargets:
    @pytest.mark.parametrize("rel", [
        "docs/notes.md",
        "harness/tests/test_something.py",
        "plans/260612-0900-x/plan.md",
        "plans/260612-0900-x/artifacts/verification.json",
        "src/app.py",
    ])
    def test_normal_write_passes(self, tmp_path, rel):
        root = _mk_root(tmp_path)
        proc = _run(root, "Write", root / rel)
        assert proc.returncode == 0, "%s: %s" % (rel, proc.stderr[:200])
        assert '"continue"' in proc.stdout

    def test_other_tools_pass_untouched(self, tmp_path):
        root = _mk_root(tmp_path)
        proc = _run(root, "Read", root / "harness/data/stage-policy.yaml")
        assert proc.returncode == 0

    def test_missing_file_path_passes(self, tmp_path):
        root = _mk_root(tmp_path)
        env = dict(os.environ)
        env.pop("PYTEST_CURRENT_TEST", None)
        env["HARNESS_ROOT"] = str(root)
        env["HARNESS_STATE_DIR"] = str(root / "harness" / "state")
        env["HARNESS_HOOK_LOG_DIR"] = str(root / "logs")
        proc = subprocess.run(
            [sys.executable, str(_GUARD)],
            input=json.dumps({"tool_name": "Write", "tool_input": {}}),
            capture_output=True, text=True, env=env)
        assert proc.returncode == 0


class TestExtraGuardedAddOnly:
    def test_yaml_can_add_paths(self, tmp_path):
        root = _mk_root(tmp_path)
        (root / "harness/data/write-guard.yaml").write_text(
            "enabled: true\nextra_guarded:\n  - docs/protected.md\n",
            encoding="utf-8")
        proc = _run(root, "Write", root / "docs" / "protected.md")
        assert proc.returncode == 2

    def test_yaml_cannot_remove_builtin_paths(self, tmp_path):
        # There is no subtract key; even a hostile attempt to shadow the
        # list must change nothing.
        root = _mk_root(tmp_path)
        (root / "harness/data/write-guard.yaml").write_text(
            "enabled: true\nguard_list: []\nextra_guarded: []\n"
            "remove: [harness/data/stage-policy.yaml]\n", encoding="utf-8")
        proc = _run(root, "Write", root / "harness/data/stage-policy.yaml")
        assert proc.returncode == 2

    def test_malformed_yaml_fails_closed_on_guarded_target(self, tmp_path):
        root = _mk_root(tmp_path)
        (root / "harness/data/write-guard.yaml").write_text(
            "{not yaml::", encoding="utf-8")
        proc = _run(root, "Write", root / "harness/data/stage-policy.yaml")
        assert proc.returncode == 2


class TestEnablePathAutonomy:
    def test_harness_hook_config_env_cannot_disable_the_guard(self, tmp_path):
        # hook_enabled() reads HARNESS_HOOK_CONFIG; the guard must NOT —
        # otherwise one env var silently turns the config-edit gate off.
        root = _mk_root(tmp_path)
        evil = tmp_path / "evil-hooks.yaml"
        evil.write_text("hooks:\n  write_guard:\n    enabled: false\n",
                        encoding="utf-8")
        proc = _run(root, "Write", root / "harness/data/stage-policy.yaml",
                    extra_env={"HARNESS_HOOK_CONFIG": str(evil)})
        assert proc.returncode == 2

    def test_tracked_switch_file_disables_with_traced_skip(self, tmp_path):
        # The break-glass: write-guard.yaml (tracked, edited OUTSIDE the
        # session — in-session it is itself guarded) flips enabled: false.
        root = _mk_root(tmp_path)
        (root / "harness/data/write-guard.yaml").write_text(
            "enabled: false\n", encoding="utf-8")
        proc = _run(root, "Write", root / "harness/data/stage-policy.yaml")
        assert proc.returncode == 0
        events = _trace_events(root)
        skips = [e for e in events if e["event"] == "gate_skip"]
        assert skips and skips[0].get("note")

    def test_dedicated_env_override_disables_the_guard(self, tmp_path):
        # A DEDICATED, single-purpose override var (its ONLY effect is disarming
        # the tool-cage) MAY disable it — for a single-owner dev repo, scrubbed at
        # push so ship always falls back to the tracked write-guard.yaml. This is
        # DISTINCT from HARNESS_HOOK_CONFIG above, which still cannot: a broad
        # config var must not reach the cage, only this explicit one.
        root = _mk_root(tmp_path)
        override = tmp_path / "dev-write-guard.yaml"
        override.write_text("enabled: false\n", encoding="utf-8")
        proc = _run(root, "Write", root / "harness/data/stage-policy.yaml",
                    extra_env={"HARNESS_WRITE_GUARD_CONFIG": str(override)})
        assert proc.returncode == 0
        events = _trace_events(root)
        skips = [e for e in events if e["event"] == "gate_skip"]
        assert skips and skips[0].get("note")

    def test_dedicated_env_override_absent_file_keeps_guard_on(self, tmp_path):
        # A dangling override path (file missing) must NOT open the gate — it
        # falls back to the tracked switch (enabled) and still blocks fail-closed.
        root = _mk_root(tmp_path)
        proc = _run(root, "Write", root / "harness/data/stage-policy.yaml",
                    extra_env={"HARNESS_WRITE_GUARD_CONFIG":
                               str(tmp_path / "does-not-exist.yaml")})
        assert proc.returncode == 2


# --- H4: MCP blind spot (mcp__<server>__<method> tools) -----------------------

def _run_mcp(root: Path, tool: str, tool_input: dict, extra_env=None):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_HOOK_CONFIG", None)
    env["HARNESS_ROOT"] = str(root)
    env["HARNESS_STATE_DIR"] = str(root / "harness" / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(root / "logs")
    env["HARNESS_USER"] = "guard-tester"
    for k, v in (extra_env or {}).items():
        env[k] = v
    payload = {"tool_name": tool, "tool_input": tool_input, "session_id": "wg-mcp"}
    return subprocess.run([sys.executable, str(_GUARD)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env)


class TestMcpBlindSpot:
    def test_read_shaped_mcp_tool_passes_untouched(self, tmp_path):
        # a devtool `query` (Safe Mode / read-only) is a documented non-issue —
        # this path-containment gate never even looks at its target.
        root = _mk_root(tmp_path)
        proc = _run_mcp(root, "mcp__devtool__query",
                        {"path": str(root / "harness/data/stage-policy.yaml")})
        assert proc.returncode == 0, proc.stderr

    def test_write_shaped_mcp_tool_with_guarded_target_blocks(self, tmp_path):
        root = _mk_root(tmp_path)
        proc = _run_mcp(root, "mcp__devtool__write",
                        {"path": str(root / "harness/data/stage-policy.yaml")})
        assert proc.returncode == 2, proc.stderr
        assert "stage-policy.yaml" in proc.stderr

    def test_write_shaped_mcp_tool_with_normal_target_passes(self, tmp_path):
        root = _mk_root(tmp_path)
        proc = _run_mcp(root, "mcp__devtool__write",
                        {"path": str(root / "docs/notes.md")})
        assert proc.returncode == 0, proc.stderr

    def test_write_shaped_mcp_tool_alternate_path_key_blocks(self, tmp_path):
        # server schemas vary — file_path / target_path / dest all recognized.
        root = _mk_root(tmp_path)
        proc = _run_mcp(root, "mcp__devtool__update",
                        {"target_path": str(root / "harness/hooks/gate_stage.py")})
        assert proc.returncode == 2, proc.stderr

    def test_write_shaped_mcp_tool_no_extractable_path_fails_closed(self, tmp_path):
        # the write target is unknowable — this gate must not silently allow.
        root = _mk_root(tmp_path)
        proc = _run_mcp(root, "mcp__devtool__write", {"sql": "INSERT INTO t VALUES (1)"})
        assert proc.returncode == 2, proc.stderr
        assert "unknowable" in proc.stderr.lower() or "fail" in proc.stderr.lower()

    def test_break_glass_disables_mcp_unresolvable_block_too(self, tmp_path):
        root = _mk_root(tmp_path)
        (root / "harness/data/write-guard.yaml").write_text(
            "enabled: false\n", encoding="utf-8")
        proc = _run_mcp(root, "mcp__devtool__write", {"sql": "INSERT INTO t VALUES (1)"})
        assert proc.returncode == 0, proc.stderr

    def test_non_mcp_non_write_tool_passes(self, tmp_path):
        root = _mk_root(tmp_path)
        proc = _run_mcp(root, "Bash", {"command": "echo hi"})
        assert proc.returncode == 0, proc.stderr
