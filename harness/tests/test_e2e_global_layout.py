"""test_e2e_global_layout.py — the two-zone guard end to end under a REAL global
layout (bin≠project), driven as SUBPROCESSES with stdin JSON (no import cheating).

This is the ONLY place the two-zone branch actually runs: the dogfood suite and
run_vertical_slice are bin==project (self-host), which collapses to the legacy
single-root path and leaves the whole-bin catch-all inert. Here the shared binary
is the real repo ($HARNESS_BIN_ROOT) and the project is a fresh temp dir
($CLAUDE_PROJECT_DIR) — so:

  - a foreign tool-Write into ${bin}/** BLOCKS (bin-lane GUARD_LIST hit AND the
    whole-bin catch-all for a non-guarded bin path — red-team F1), and
  - a legit write into the project's own .harness/ PASSES,

composing phase-2 (write_guard) and phase-3 (agent_rbac_guard isolation floor).
"""
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS = _REPO_ROOT / "harness" / "hooks"


def _run(hook_name, payload, bin_root, project_dir, extra_env=None):
    """Invoke a hook as Claude Code would: subprocess + stdin JSON, with a global
    env (HARNESS_BIN_ROOT≠CLAUDE_PROJECT_DIR). Returns the CompletedProcess."""
    import os
    env = dict(os.environ)
    # Scrub self-host / test seams so the layout is unambiguously global.
    for k in ("HARNESS_ROOT", "HARNESS_STATE_DIR", "HARNESS_DATA_ROOT",
              "HARNESS_HOOK_LOG_DIR", "PYTEST_CURRENT_TEST"):
        env.pop(k, None)
    env["HARNESS_BIN_ROOT"] = str(bin_root)
    env["CLAUDE_PROJECT_DIR"] = str(project_dir)
    # DELIBERATELY do NOT set HARNESS_STATE_DIR — the installer never wires it, so
    # setting it here would be phantom coverage that hides where state actually
    # lands. hook_runtime._state_dir() must resolve it to the project's .harness/.
    for k, v in (extra_env or {}).items():
        env[k] = v
    return subprocess.run(
        [sys.executable, str(_HOOKS / hook_name)],
        input=json.dumps(payload), capture_output=True, text=True, env=env)


def _write_payload(file_path, tool="Write", agent_type=None):
    p = {"session_id": "e2e-global", "tool_name": tool,
         "tool_input": {"file_path": str(file_path), "content": "x"}}
    if agent_type is not None:
        p["agent_type"] = agent_type
    return p


def _project(tmp_path):
    proj = tmp_path / "proj"
    (proj / ".harness" / "state" / "telemetry").mkdir(parents=True)
    return proj


class TestWriteGuardTwoZone:
    def test_bin_lane_hook_write_blocks(self, tmp_path):
        proj = _project(tmp_path)
        # a GUARD_LIST bin-lane path (harness/hooks/*.py) under the shared binary
        r = _run("write_guard.py",
                 _write_payload(_REPO_ROOT / "harness" / "hooks" / "write_guard.py"),
                 _REPO_ROOT, proj)
        assert r.returncode == 2, r.stderr

    def test_whole_bin_catchall_blocks_non_guarded_path(self, tmp_path):
        proj = _project(tmp_path)
        # NOT on GUARD_LIST, but under ${bin}/** → whole-bin catch-all (F1) blocks
        r = _run("write_guard.py",
                 _write_payload(_REPO_ROOT / ".claude" / "settings.json"),
                 _REPO_ROOT, proj)
        assert r.returncode == 2, r.stderr

    def test_project_data_write_passes(self, tmp_path):
        proj = _project(tmp_path)
        # a legit write into the project's own .harness/ data home
        r = _run("write_guard.py",
                 _write_payload(proj / ".harness" / "state" / "telemetry" / "e.jsonl"),
                 _REPO_ROOT, proj)
        assert r.returncode == 0, r.stderr

    def test_bin_write_passes_when_self_host_collapse(self, tmp_path):
        # sanity: with HARNESS_BIN_ROOT UNSET the layout collapses to self-host and
        # the whole-bin catch-all is inert (a project IS its own bin). Point the
        # project at the repo and DROP the bin env — a repo-internal path is allowed.
        import os
        env = dict(os.environ)
        for k in ("HARNESS_BIN_ROOT", "HARNESS_ROOT", "HARNESS_DATA_ROOT"):
            env.pop(k, None)
        env["CLAUDE_PROJECT_DIR"] = str(_REPO_ROOT)
        r = subprocess.run(
            [sys.executable, str(_HOOKS / "write_guard.py")],
            input=json.dumps(_write_payload(_REPO_ROOT / "README.md")),
            capture_output=True, text=True, env=env)
        assert r.returncode == 0, r.stderr


class TestStateIsolation:
    def test_session_actor_cache_lands_in_project_not_bin(self, tmp_path):
        # session_init writes the actor cache via hook_runtime._state_dir(); under
        # a global layout with HARNESS_STATE_DIR unset it must land in the PROJECT's
        # .harness/state, never the shared binary's harness/state (the data-leak
        # the code-review caught — phantom coverage previously masked it).
        proj = _project(tmp_path)
        r = _run("session_init.py", {"session_id": "e2e-state"}, _REPO_ROOT, proj)
        assert r.returncode == 0, r.stderr
        cached = proj / ".harness" / "state" / "sessions" / "e2e-state.json"
        assert cached.is_file(), "actor cache not in project .harness/state"
        # the shared binary must NOT have gained a project's session cache
        leaked = _REPO_ROOT / "harness" / "state" / "sessions" / "e2e-state.json"
        assert not leaked.exists(), "session cache leaked into the shared bin"


class TestRbacComposed:
    def test_confined_subagent_write_into_bin_blocks(self, tmp_path):
        proj = _project(tmp_path)
        # a confined advisory subagent may not reach OUTSIDE its project isolation
        # root — a write into the shared binary escapes and is blocked (isolation
        # floor), composing the two-zone model at the rbac layer.
        r = _run("agent_rbac_guard.py",
                 _write_payload(_REPO_ROOT / "harness" / "hooks" / "x.py",
                                agent_type="hs:code-reviewer"),
                 _REPO_ROOT, proj)
        assert r.returncode == 2, r.stderr

    def test_parent_agent_exempt_from_isolation(self, tmp_path):
        proj = _project(tmp_path)
        # the top-level agent (no agent_type) is not containment-confined; rbac
        # passes it (write_guard is the layer that still fences the bin for it).
        r = _run("agent_rbac_guard.py",
                 _write_payload(_REPO_ROOT / "harness" / "hooks" / "x.py"),
                 _REPO_ROOT, proj)
        assert r.returncode == 0, r.stderr
