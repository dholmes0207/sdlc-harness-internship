"""Tests for agent_rbac_guard — PreToolUse compliance gate keyed on agent_type.

A thin compliance wrapper whose core() returns None (pass) or a
reason string (block) under run_compliance_hook (fail-closed). It reads the role
straight off the payload (agent_type / subagent_type, NOT resolve_actor, which
collapses parent/sub on the shared session_id), relativizes the write target to
the repo root, and consults the agent_permissions table.

Additive-skip: with no permission table the gate is inert (allow). The top-level
agent (_parent) is unrestricted unless the table declares it.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hook_runtime  # noqa: E402,F401

HOOK_PATH = _HOOKS / "agent_rbac_guard.py"


def _hr():
    import hook_runtime as hr  # noqa: E402
    return sys.modules.get("hook_runtime", hr)


def _load():
    spec = importlib.util.spec_from_file_location("agent_rbac_guard", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_hook_config(path: Path, enabled: bool = True):
    path.write_text(
        "hooks:\n  agent_rbac_guard:\n    enabled: %s\n"
        % ("true" if enabled else "false"), encoding="utf-8")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    for d in (state, logs):
        d.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    _write_hook_config(cfg, enabled=True)
    perm = tmp_path / "agent-permissions.yaml"  # written per-test
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(logs))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.setenv("HARNESS_AGENT_PERMISSIONS_FILE", str(perm))
    monkeypatch.setenv("HARNESS_USER", "tester")
    _hr()._reset_config_cache()
    yield {"state": state, "cfg": cfg, "perm": perm, "root": tmp_path}
    _hr()._reset_config_cache()


def _perm(env, body):
    env["perm"].write_text(body, encoding="utf-8")


def _payload(env, *, agent_type=None, subagent_type=None, rel="harness/x.py",
            tool="Write"):
    data = {"session_id": "s1", "cwd": str(env["root"]),
            "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"file_path": str(env["root"] / rel)}}
    if agent_type is not None:
        data["agent_type"] = agent_type
    if subagent_type is not None:
        data["subagent_type"] = subagent_type
    return data


# ---------------------------------------------------------------------------
# core(): pass / block
# ---------------------------------------------------------------------------

def test_plain_source_allowed(_env):
    mod = _load()
    # deny-list floor: a subagent writes plain source out of the box, no config
    assert mod.core(_payload(_env, agent_type="general-purpose",
                             rel="src/app.tsx")) is None


def test_docs_path_allowed(_env):
    mod = _load()
    # docs/** is not on the floor (only docs/decisions.* is core) → allowed
    assert mod.core(_payload(_env, agent_type="general-purpose",
                             rel="docs/guide.md")) is None


def test_soft_hit_emits_no_block(_env, monkeypatch):
    import write_deny_policy as wdp
    mod = _load()
    monkeypatch.setattr(mod, "_load_deny_policy",
                        lambda: wdp.assemble_policy([("deny", "docs/**")]))
    # soft-tier match: detective, the write is ALLOWED and a deny_soft_hit is traced
    assert mod.core(_payload(_env, agent_type="general-purpose", rel="docs/x.md")) is None
    trace_files = list((_env["state"]).rglob("*.jsonl"))
    text = "".join(p.read_text(encoding="utf-8") for p in trace_files)
    assert "deny_soft_hit" in text, trace_files


def test_out_of_lane_blocks(_env):
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    reason = mod.core(_payload(_env, agent_type="general-purpose",
                               rel="harness/hooks/x.py"))
    assert reason and "outside" in reason.lower()


def _nb_payload(env, *, agent_type, rel):
    # NotebookEdit's target lives in `notebook_path`, not `file_path`.
    return {"session_id": "s1", "cwd": str(env["root"]),
            "hook_event_name": "PreToolUse", "tool_name": "NotebookEdit",
            "tool_input": {"notebook_path": str(env["root"] / rel)},
            "agent_type": agent_type}


def test_notebook_edit_out_of_lane_blocks(_env):
    # NotebookEdit is a write tool (ui-ux-designer carries it) — it must be lane-checked
    # too, or its writes bypass RBAC entirely.
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    reason = mod.core(_nb_payload(_env, agent_type="general-purpose",
                                  rel="harness/hooks/x.ipynb"))
    assert reason and "outside" in reason.lower()


def test_notebook_edit_allowed_path_passes(_env):
    mod = _load()
    assert mod.core(_nb_payload(_env, agent_type="general-purpose",
                                rel="notebooks/analysis.ipynb")) is None


def test_parent_unrestricted(_env):
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    # no agent_type → _parent → unrestricted (no _parent entry)
    assert mod.core(_payload(_env, rel="harness/anything.py")) is None


# ---------------------------------------------------------------------------
# H4: MCP blind spot (mcp__<server>__<method> tools)
# ---------------------------------------------------------------------------

def _mcp_payload(env, *, agent_type=None, tool="mcp__devtool__write",
                 tool_input=None):
    data = {"session_id": "s1", "cwd": str(env["root"]),
            "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": tool_input or {}}
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


def test_mcp_read_shaped_tool_passes_untouched(_env):
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    reason = mod.core(_mcp_payload(
        _env, agent_type="general-purpose", tool="mcp__devtool__query",
        tool_input={"path": str(_env["root"] / "harness/hooks/x.py")}))
    assert reason is None


def test_mcp_write_shaped_out_of_lane_blocks(_env):
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    reason = mod.core(_mcp_payload(
        _env, agent_type="general-purpose", tool="mcp__devtool__write",
        tool_input={"path": str(_env["root"] / "harness/hooks/x.py")}))
    assert reason and "outside" in reason.lower()


def test_mcp_write_shaped_allowed_path_passes(_env):
    mod = _load()
    reason = mod.core(_mcp_payload(
        _env, agent_type="general-purpose", tool="mcp__devtool__update",
        tool_input={"target_path": str(_env["root"] / "src/app.tsx")}))
    assert reason is None


def test_mcp_write_shaped_no_extractable_path_fails_closed_even_for_parent(_env):
    # No path -> no RBAC lane check possible at all; unlike the native-tool
    # isolation floor (which exempts the parent), the parent gets no pass here
    # because there is no path for any layer to audit.
    mod = _load()
    reason = mod.core(_mcp_payload(
        _env, agent_type=None, tool="mcp__devtool__write",
        tool_input={"sql": "INSERT INTO t VALUES (1)"}))
    assert reason and "unknowable" in reason.lower()


def test_mcp_non_write_verb_unrecognized_key_passes(_env):
    # a genuinely read-only MCP method with no verb match is out of scope.
    mod = _load()
    reason = mod.core(_mcp_payload(
        _env, agent_type="general-purpose", tool="mcp__devtool__describe",
        tool_input={"table": "users"}))
    assert reason is None


def test_present_parent_sentinel_is_confined(_env):
    # R1: only an ABSENT attribution may be the unrestricted parent. A payload that
    # SPELLS the reserved sentinel (verbatim / plugin-qualified / whitespace) must
    # drift to a confined role, never inherit the parent lane.
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    for spoof in ("_parent", "hs:_parent", " _parent "):
        reason = mod.core(_payload(_env, agent_type=spoof, rel="harness/x.py"))
        assert reason, "spoofed agent_type %r must be confined, got allow" % spoof
    # absent attribution remains the genuine unrestricted parent
    assert mod.core(_payload(_env, rel="harness/x.py")) is None


def test_subagent_type_fallback(_env):
    _perm(_env, "default_deny: true\nroles:\n  cook: ['plans/**']\n")
    mod = _load()
    reason = mod.core(_payload(_env, subagent_type="cook", rel="harness/x.py"))
    assert reason and "cook" in reason


def test_non_write_tool_passes(_env):
    _perm(_env, "roles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    assert mod.core(_payload(_env, agent_type="general-purpose",
                             rel="harness/x.py", tool="Read")) is None


def test_malformed_table_fails_closed(_env):
    _perm(_env, "roles: [not, a, mapping]\n")
    mod = _load()
    reason = mod.core(_payload(_env, agent_type="general-purpose"))
    assert reason  # present-but-malformed → block reason, not silent skip


# ---------------------------------------------------------------------------
# isolation floor: cwd/worktree containment (independent of the glob lane)
# ---------------------------------------------------------------------------

def _escape_payload(env, *, agent_type=None):
    # a target OUTSIDE the cwd/worktree root
    outside = env["root"].parent / ("escape-%s.txt" % env["root"].name)
    data = {"session_id": "s1", "cwd": str(env["root"]),
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "tool_input": {"file_path": str(outside)}}
    if agent_type is not None:
        data["agent_type"] = agent_type
    return data


def test_containment_blocks_subagent_escape_even_with_broad_lane(_env):
    # a non-_parent role with a permissive '**' lane still cannot write OUTSIDE the
    # cwd/worktree root — the isolation floor catches what the glob would allow
    _perm(_env, "roles:\n  general-purpose: ['**']\n")
    mod = _load()
    reason = mod.core(_escape_payload(_env, agent_type="general-purpose"))
    assert reason and "isolation" in reason.lower()


def test_containment_allows_parent_escape(_env):
    # the top-level agent (_parent) is NOT containment-confined
    _perm(_env, "roles:\n  general-purpose: ['**']\n")
    mod = _load()
    assert mod.core(_escape_payload(_env)) is None


def test_containment_active_by_default(_env):
    # the isolation floor is always on now (no table gates it): a subagent escape
    # from its worktree root blocks out of the box.
    mod = _load()
    reason = mod.core(_escape_payload(_env, agent_type="general-purpose"))
    assert reason and "isolation" in reason.lower()


# ---------------------------------------------------------------------------
# CLI: real exit codes via run_compliance_hook
# ---------------------------------------------------------------------------

def _run(payload, env):
    return subprocess.run([sys.executable, str(HOOK_PATH)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env)


def test_cli_blocks_out_of_lane_exit2(_env):
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    env = dict(os.environ)
    p = _run(_payload(_env, agent_type="general-purpose", rel="harness/x.py"), env)
    assert p.returncode == 2, (p.returncode, p.stderr)
    assert "outside" in p.stderr.lower()


def test_cli_allows_allowed_path_exit0(_env):
    env = dict(os.environ)
    p = _run(_payload(_env, agent_type="general-purpose", rel="src/app.tsx"), env)
    assert p.returncode == 0, (p.returncode, p.stderr)


def test_cli_disabled_is_inert_exit0(_env):
    _write_hook_config(_env["cfg"], enabled=False)
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    env = dict(os.environ)
    p = _run(_payload(_env, agent_type="general-purpose", rel="harness/x.py"), env)
    assert p.returncode == 0, (p.returncode, p.stderr)


# ---------------------------------------------------------------------------
# present-but-empty attribution: a drifted subagent, NOT the top-level _parent
# ---------------------------------------------------------------------------

def test_empty_agent_type_does_not_collapse_to_parent(_env):
    # agent_type="" is a PRESENT-but-empty attribution → a drifted subagent whose
    # role vanished. It must NOT read as _parent (write-everything); under
    # default_deny it is fail-closed blocked.
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    assert mod.core(_payload(_env, agent_type="", rel="harness/x.py")) is not None


def test_whitespace_agent_type_does_not_collapse_to_parent(_env):
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    assert mod.core(_payload(_env, agent_type="   ", rel="harness/x.py")) is not None


def test_absent_agent_type_remains_parent(_env):
    # truly ABSENT (no key) is the real top-level agent → unrestricted
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    assert mod.core(_payload(_env, rel="harness/anything.py")) is None


# ---------------------------------------------------------------------------
# relative target resolves against the payload cwd, not the hook process cwd
# ---------------------------------------------------------------------------

def _rel_payload(env, *, agent_type, rel):
    return {"session_id": "s1", "cwd": str(env["root"]),
            "hook_event_name": "PreToolUse", "tool_name": "Write",
            "agent_type": agent_type, "tool_input": {"file_path": rel}}


def test_relative_target_contained_under_payload_cwd(_env):
    # a RELATIVE file_path must be resolved under the payload cwd (the worktree),
    # not the hook process cwd — else a legit in-worktree write false-blocks on the
    # isolation floor. Allowed relative write → pass.
    mod = _load()
    assert mod.core(_rel_payload(_env, agent_type="general-purpose",
                                 rel="src/app.tsx")) is None


def test_relative_escape_still_blocks_isolation(_env):
    # '../escape.txt' relative to cwd still escapes the root → isolation block
    _perm(_env, "roles:\n  general-purpose: ['**']\n")
    mod = _load()
    reason = mod.core(_rel_payload(_env, agent_type="general-purpose",
                                   rel="../escape.txt"))
    assert reason and "isolation" in reason.lower()


# ---------------------------------------------------------------------------
# global-layout isolation floor: no payload cwd → project root, never CWD decay
# ---------------------------------------------------------------------------

def _no_cwd_payload(target, *, agent_type="general-purpose"):
    # a subagent write with NO payload cwd (the case the '.'-decay used to catch)
    d = {"session_id": "s1", "hook_event_name": "PreToolUse",
         "tool_name": "Write", "tool_input": {"file_path": str(target)}}
    if agent_type is not None:
        d["agent_type"] = agent_type
    return d


def test_isolation_uses_project_dir_under_global(_env, monkeypatch, tmp_path):
    # bin != project, no payload cwd → confine the subagent to CLAUDE_PROJECT_DIR.
    binr = tmp_path / "bin"; binr.mkdir()
    proj = tmp_path / "A"; proj.mkdir()
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(binr))
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    _perm(_env, "roles:\n  general-purpose: ['**']\n")
    mod = _load()
    assert mod.core(_no_cwd_payload(proj / "sub" / "x.txt")) is None      # under project
    r = mod.core(_no_cwd_payload(tmp_path / "other" / "x.txt"))           # outside project
    assert r and "isolation" in r.lower()


def test_isolation_failclosed_when_project_dir_unset_global(_env, monkeypatch, tmp_path):
    # global layout, no payload cwd, CLAUDE_PROJECT_DIR unset → the old '.'-decay
    # would confine to the hook process CWD and ALLOW a write under it. The floor
    # must fail CLOSED instead: a confined role cannot resolve a project root.
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path / "bin"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("HARNESS_DATA_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)  # so a '.'-decay would ALLOW a write under here
    _perm(_env, "roles:\n  general-purpose: ['**']\n")
    mod = _load()
    r = mod.core(_no_cwd_payload(tmp_path / "sub" / "x.txt"))
    assert r and "isolation" in r.lower()  # fail-closed, NOT a CWD-decay allow


def test_parent_exempt_even_when_project_unresolved_global(_env, monkeypatch, tmp_path):
    # the top-level agent stays exempt even when no project root resolves.
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path / "bin"))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    monkeypatch.delenv("HARNESS_DATA_ROOT", raising=False)
    monkeypatch.chdir(tmp_path)
    _perm(_env, "default_deny: true\nroles:\n  general-purpose: ['plans/**']\n")
    mod = _load()
    assert mod.core(_no_cwd_payload(tmp_path / "sub" / "x.txt",
                                    agent_type=None)) is None
