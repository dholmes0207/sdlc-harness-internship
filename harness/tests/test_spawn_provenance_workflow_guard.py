"""Tests for spawn_provenance_workflow_guard.py + spawn_provenance_workflow_nudge.py
— Ask-3 Layer-2 (P8): the `Workflow` tool declares its OWN fan-out width in a
single call (WP1-verified: `tool_input.script`, plans/260715-0021-subagent-
spawn-guards-and-reinject/probes/WP1-workflow-pretooluse.md), so this lane
checks a REAL per-call width via a static script-parse
(`spawn_provenance.workflow_width`) instead of the Agent|Task lane's
cumulative session count. Shares the SAME token/budget logic as the Agent|Task
lane (`spawn_provenance.budget`/`has_orchestrate_token`) — no separate
counter, no `record_spawn` call (a Workflow call is self-contained, not
cumulative across calls).

Properties under test (phase-8 W1-W7):
  W1: `workflow_width` — covered in test_spawn_provenance.py.
  W2: block_enabled false -> guard core is inert (returns None).
  W3: block_enabled true — width > budget, no token -> BLOCK; width <= budget
      -> pass.
  W4: width > threshold but an active covering token (sub_count >= width) ->
      budget reflects the token -> pass (approved wide orchestrate).
  W5: an internal crash inside core(), driven through the REAL dispatcher
      (hook_dispatch.py) — never spawn_provenance_workflow_guard.main(),
      which the in-process dispatcher bypasses — FAILS OPEN (exit 0), backed
      by the shipped `fail_open: true` on this hook's dispatch row (M5).
  W6: a real block drives exit 2 through the dispatcher, with an
      honest-ceiling reason (static estimate, provenance/count/budget/shape,
      never strategy quality).
  W7: the advisory nudge fires at width > threshold with no token; silent
      otherwise; a reader crash is silent (nudge-class contract).
"""
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_DATA = Path(__file__).resolve().parent.parent / "data"
for _p in (_HOOKS, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import hook_runtime  # noqa: E402
import spawn_provenance  # noqa: E402

HOOK_PATH = _HOOKS / "spawn_provenance_workflow_guard.py"
NUDGE_PATH = _HOOKS / "spawn_provenance_workflow_nudge.py"

_ENABLED = ("hooks:\n"
            "  spawn_provenance_workflow_guard: {enabled: true}\n"
            "  spawn_provenance_workflow_nudge: {enabled: true}\n")


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_guard():
    return _load_module(HOOK_PATH, "spawn_provenance_workflow_guard")


def _load_nudge():
    return _load_module(NUDGE_PATH, "spawn_provenance_workflow_nudge")


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_ENABLED, encoding="utf-8")
    spcfg = tmp_path / "spawn-provenance.yaml"
    spcfg.write_text("block_enabled: true\nthreshold: 5\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.setenv("HARNESS_SPAWN_PROVENANCE", str(spcfg))
    monkeypatch.setenv("HARNESS_USER", "tester")
    hook_runtime._reset_config_cache()
    yield {"state": state, "cfg": cfg, "spcfg": spcfg, "tmp": tmp_path}
    hook_runtime._reset_config_cache()


def _agent_script(n):
    return ";".join(["agent({})"] * n)


def _workflow(session="sess-1", script="agent({})", tool_use_id=None):
    d = {"tool_name": "Workflow", "session_id": session,
         "tool_input": {"script": script}}
    if tool_use_id:
        d["tool_use_id"] = tool_use_id
    return d


def _write_token(tmp_path, run_id, session, ts=None, ttl=1800, sub_count=5):
    d = tmp_path / "state" / "orchestrate" / run_id
    d.mkdir(parents=True, exist_ok=True)
    ts = time.time() if ts is None else ts
    tok = {"session": session, "actor": "tester", "ts": ts, "expires_at": ts + ttl,
           "sub_count": sub_count, "run_id": run_id}
    (d / "token.json").write_text(json.dumps(tok), encoding="utf-8")
    return tok


# ---------------------------------------------------------------------------
# W2 — block_enabled false => inert (default ship state)
# ---------------------------------------------------------------------------

def test_w2_block_disabled_is_inert(env):
    env["spcfg"].write_text("block_enabled: false\nthreshold: 5\n", encoding="utf-8")
    mod = _load_guard()
    assert mod.core(_workflow(script=_agent_script(20))) is None


def test_w2_ignores_non_workflow_tool(env):
    mod = _load_guard()
    assert mod.core({"tool_name": "Agent", "session_id": "sess-1",
                      "tool_input": {"subagent_type": "hs:developer"}}) is None


def test_w2_ignores_malformed_payload(env):
    mod = _load_guard()
    assert mod.core(None) is None
    assert mod.core("not a dict") is None
    assert mod.core({}) is None


# ---------------------------------------------------------------------------
# W3 — no token: width > budget(threshold) blocks; width <= budget passes
# ---------------------------------------------------------------------------

def test_w3_wide_no_token_blocks(env):
    mod = _load_guard()
    reason = mod.core(_workflow(session="sess-1", script=_agent_script(10)))
    assert reason is not None
    assert "spawn_provenance" in reason
    assert "10" in reason  # names the width
    assert "5" in reason   # names the budget


def test_w3_narrow_no_token_passes(env):
    mod = _load_guard()
    assert mod.core(_workflow(session="sess-1", script=_agent_script(3))) is None


def test_w3_width_equal_to_budget_passes(env):
    # BLOCK iff width > budget — equal is allowed.
    mod = _load_guard()
    assert mod.core(_workflow(session="sess-1", script=_agent_script(5))) is None


# ---------------------------------------------------------------------------
# W4 — an active covering token raises the budget -> passes
# ---------------------------------------------------------------------------

def test_w4_covering_token_passes(env):
    _write_token(env["tmp"], "run-1", "sess-1", sub_count=12)
    mod = _load_guard()
    assert mod.core(_workflow(session="sess-1", script=_agent_script(10))) is None


def test_w4_no_token_at_same_width_still_blocks(env):
    # control: without the token, the same width-10 call blocks.
    mod = _load_guard()
    assert mod.core(_workflow(session="sess-1", script=_agent_script(10))) is not None


def test_w4_width_exceeding_token_budget_still_blocks(env):
    # N3: an active token raises the ceiling to its OWN sub_count, it does
    # NOT blanket-bypass the width check (that was asymmetric with the
    # Agent|Task lane, which enforces the token's sub_count). A width beyond
    # the token's approved sub_count must still block, even WITH the token.
    _write_token(env["tmp"], "run-1", "sess-1", sub_count=8)
    mod = _load_guard()
    reason = mod.core(_workflow(session="sess-1", script=_agent_script(10)))
    assert reason is not None
    assert "10" in reason  # names the width
    assert "8" in reason   # names the (token-derived) budget


def test_w4_width_within_token_budget_passes(env):
    # control/symmetry: a width within the token's sub_count passes.
    _write_token(env["tmp"], "run-1", "sess-1", sub_count=8)
    mod = _load_guard()
    assert mod.core(_workflow(session="sess-1", script=_agent_script(8))) is None


# ---------------------------------------------------------------------------
# W5 — dispatcher-driven fail-open on an internal crash (M5)
# ---------------------------------------------------------------------------

def test_w5_dispatch_row_carries_fail_open(env):
    registry = yaml.safe_load((_DATA / "hook-dispatch.yaml").read_text(encoding="utf-8"))
    rows = registry["groups"]["PreToolUse:Workflow"]
    row = next(r for r in rows if r["name"] == "spawn_provenance_workflow_guard")
    assert row["fail_open"] is True
    assert row["class"] == "compliance"


def test_w5_dispatcher_driven_internal_crash_fails_open(env):
    script = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "sys.path.insert(0, %r)\n"
        "import hook_runtime, spawn_provenance, hook_dispatch\n"
        "def _boom(*a, **kw):\n"
        "    raise RuntimeError('forced internal crash (test)')\n"
        "spawn_provenance.budget = _boom\n"
        "payload = '{\"session_id\": \"sess-1\", \"tool_name\": \"Workflow\", "
        "\"tool_input\": {\"script\": \"agent({});agent({});agent({});agent({});"
        "agent({});agent({})\"}}'\n"
        "code = hook_dispatch.run(['PreToolUse', 'Workflow'], stdin_text=payload)\n"
        "sys.exit(code)\n"
    ) % (str(_HOOKS), str(_SCRIPTS))
    sub_env = dict(os.environ)
    sub_env["HARNESS_STATE_DIR"] = str(env["state"])
    sub_env["HARNESS_HOOK_CONFIG"] = str(env["cfg"])
    sub_env["HARNESS_SPAWN_PROVENANCE"] = str(env["spcfg"])
    r = subprocess.run([sys.executable, "-c", script], input="",
                        capture_output=True, text=True, env=sub_env)
    assert r.returncode == 0  # the crash must NOT block the Workflow call
    out = json.loads(r.stdout)
    assert out.get("continue") is True


# ---------------------------------------------------------------------------
# W6 — a real block drives exit 2 through the dispatcher, honest-ceiling reason
# ---------------------------------------------------------------------------

def _run_dispatcher(env, session, script):
    sub_env = dict(os.environ)
    sub_env["HARNESS_STATE_DIR"] = str(env["state"])
    sub_env["HARNESS_HOOK_CONFIG"] = str(env["cfg"])
    sub_env["HARNESS_SPAWN_PROVENANCE"] = str(env["spcfg"])
    payload = json.dumps({"session_id": session, "tool_name": "Workflow",
                          "tool_input": {"script": script}})
    return subprocess.run(
        [sys.executable, str(_HOOKS / "hook_dispatch.py"), "PreToolUse", "Workflow"],
        input=payload, capture_output=True, text=True, env=sub_env,
    )


def test_w6_real_block_exit2_through_dispatcher(env):
    r = _run_dispatcher(env, "sess-1", _agent_script(10))
    assert r.returncode == 2
    assert "BLOCKED" in r.stderr
    assert "spawn_provenance" in r.stderr
    assert "static" in r.stderr.lower()          # honest ceiling: static estimate
    assert "never strategy quality" in r.stderr  # honest ceiling: not strategy quality


def test_w6_narrow_passes_through_dispatcher(env):
    r = _run_dispatcher(env, "sess-1", _agent_script(3))
    assert r.returncode == 0


def test_w6_disabled_by_default_never_blocks(tmp_path):
    """block_enabled default (false, unset) keeps the guard fully inert
    through the real dispatcher — the opt-in ship posture."""
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_ENABLED, encoding="utf-8")
    spcfg = tmp_path / "spawn-provenance.yaml"
    spcfg.write_text("threshold: 1\n", encoding="utf-8")  # block_enabled omitted -> False
    fake_env = {"state": state, "cfg": cfg, "spcfg": spcfg}
    r = _run_dispatcher(fake_env, "sess-1", _agent_script(50))
    assert r.returncode == 0


# ---------------------------------------------------------------------------
# W7 — the advisory nudge: fires wide+no-token, silent otherwise, crash silent
# ---------------------------------------------------------------------------

def test_w7_nudge_fires_wide_no_token(env):
    mod = _load_nudge()
    msg = mod.core(_workflow(session="sess-1", script=_agent_script(10)))
    assert msg is not None
    assert "hs:workflow-orchestrate" in msg


def test_w7_nudge_silent_narrow(env):
    mod = _load_nudge()
    assert mod.core(_workflow(session="sess-1", script=_agent_script(2))) is None


def test_w7_nudge_silent_with_covering_token(env):
    _write_token(env["tmp"], "run-1", "sess-1", sub_count=20)
    mod = _load_nudge()
    assert mod.core(_workflow(session="sess-1", script=_agent_script(10))) is None


def test_w7_nudge_ignores_non_workflow_tool(env):
    mod = _load_nudge()
    assert mod.core({"tool_name": "Agent", "session_id": "sess-1"}) is None


def test_w7_nudge_ignores_malformed_payload(env):
    mod = _load_nudge()
    assert mod.core(None) is None
    assert mod.core("not a dict") is None
    assert mod.core({}) is None


def test_w7_nudge_crash_is_silent(env, monkeypatch, capsys):
    mod = _load_nudge()
    payload = json.dumps(_workflow(session="sess-1", script=_agent_script(10)))
    monkeypatch.setattr(sys.stdin, "read", lambda: payload)

    def _boom(*a, **kw):
        raise RuntimeError("reader exploded")

    monkeypatch.setattr(spawn_provenance, "workflow_width", _boom)
    rc = mod.main()
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("continue") is True


def test_w7_nudge_disabled_by_default_is_inert(tmp_path):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text("hooks: {}\n", encoding="utf-8")
    env_extra = dict(os.environ)
    env_extra["HARNESS_STATE_DIR"] = str(state)
    env_extra["HARNESS_HOOK_CONFIG"] = str(cfg)
    payload = json.dumps(_workflow(session="sess-1", script=_agent_script(50)))
    r = subprocess.run([sys.executable, str(NUDGE_PATH)], input=payload,
                        capture_output=True, text=True, env=env_extra)
    assert r.returncode == 0
    assert "spawn_provenance" not in r.stderr
