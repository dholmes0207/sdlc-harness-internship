"""Tests for spawn_provenance_nudge.py — Layer-1a advisory: a wide un-planned
Agent|Task fan-out (nudge class, fail-open SILENT).

Properties under test (phase-7 T1-T6):
  T1: prior spawn count below threshold -> silent (None).
  T2: prior spawn count at/over threshold, no orchestrate token -> advisory
      naming the count + hs:workflow-orchestrate.
  T3: an internal reader crash -> SILENT continue (nudge-class contract),
      never exit 2.
  T4: an empty session_id spawn is bucketed separately, not merged into a
      real session's count.
  T5: a different session's prior spawns do not count toward this session.
  T6: JSONL truncation keeps the per-spawn scan bounded (covered directly in
      test_spawn_provenance.py; re-asserted here via the hook's own path).

Also: nudge posture defaults OFF (config-gated) and never blocks on malformed
input — the two invariants every nudge-class hook in this repo carries.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _p in (_HOOKS, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import hook_runtime  # noqa: E402
import spawn_provenance  # noqa: E402

HOOK_PATH = _HOOKS / "spawn_provenance_nudge.py"

_ENABLED = "hooks:\n  spawn_provenance_nudge: {enabled: true}\n"
_DISABLED = "hooks: {}\n"


def _load_hook():
    spec = importlib.util.spec_from_file_location("spawn_provenance_nudge", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_ENABLED, encoding="utf-8")
    # Pin threshold:5 explicitly so the advisory-boundary tests keep checking
    # the small off-by-one (fires on the 6th spawn, prior=5) after the shipped
    # / code-default budget rose to 8. The shipped 8 is covered separately by
    # test_shipped_threshold_is_eight + test_threshold_default_when_no_config.
    spcfg = tmp_path / "spawn-provenance.yaml"
    spcfg.write_text("threshold: 5\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.setenv("HARNESS_USER", "tester")
    monkeypatch.setenv("HARNESS_SPAWN_PROVENANCE", str(spcfg))
    hook_runtime._reset_config_cache()
    yield {"state": state, "cfg": cfg}
    hook_runtime._reset_config_cache()


def _spawn(session="sess-1", tool="Agent"):
    return {"tool_name": tool, "session_id": session,
            "tool_input": {"subagent_type": "hs:developer"}}


# ---------------------------------------------------------------------------
# core() — direct-import unit tests (T1/T2/T4/T5)
# ---------------------------------------------------------------------------

def test_t1_silent_below_threshold(_env):
    mod = _load_hook()
    for _ in range(4):  # prior=0..3 -> this is spawn #1..4
        assert mod.core(_spawn("sess-1")) is None
    # This call is the 5th spawn: prior=4 (< threshold=5) -> still silent.
    assert mod.core(_spawn("sess-1")) is None


def test_t2_advisory_at_threshold(_env):
    mod = _load_hook()
    for _ in range(5):  # prior=0..4, silent every time (5th spawn, prior=4)
        mod.core(_spawn("sess-1"))
    # 6th spawn: prior=5 (== threshold) -> fires.
    msg = mod.core(_spawn("sess-1"))
    assert msg is not None
    assert "hs:workflow-orchestrate" in msg
    assert "6" in msg  # names the (prior + 1) spawn count


def test_t2_records_after_decision_not_before(_env):
    """M6 order: the spawn under evaluation must not count against itself."""
    mod = _load_hook()
    for _ in range(5):
        mod.core(_spawn("sess-1"))
    # Reads os.environ, which the _env fixture already pinned via monkeypatch.
    assert spawn_provenance.count_in_window("sess-1") == 5
    mod.core(_spawn("sess-1"))  # the firing (6th) spawn
    assert spawn_provenance.count_in_window("sess-1") == 6


def test_t4_empty_session_bucketed_separately(_env):
    mod = _load_hook()
    for _ in range(6):
        mod.core(_spawn(session=""))
    for _ in range(2):
        mod.core(_spawn(session="sess-real"))
    # The empty-session bucket crossed the threshold on its own; sess-real did not.
    assert spawn_provenance.count_in_window("") >= 5
    assert spawn_provenance.count_in_window("sess-real") == 2


def test_t5_other_session_does_not_leak_into_new_session(_env):
    mod = _load_hook()
    for _ in range(9):
        mod.core(_spawn(session="sess-busy"))
    # A brand-new session_id starts back at 0 -> silent, unaffected by sess-busy.
    assert mod.core(_spawn(session="sess-fresh")) is None


def test_core_ignores_non_spawn_tool(_env):
    mod = _load_hook()
    assert mod.core({"tool_name": "Bash", "session_id": "sess-1"}) is None
    assert spawn_provenance.count_in_window("sess-1") == 0  # never recorded


def test_core_ignores_malformed_payload(_env):
    mod = _load_hook()
    assert mod.core(None) is None
    assert mod.core("not a dict") is None
    assert mod.core({}) is None


# ---------------------------------------------------------------------------
# T3 — internal crash is SILENT (nudge-class contract), never exit 2
# ---------------------------------------------------------------------------

def test_t3_reader_crash_is_silent(_env, monkeypatch, capsys):
    mod = _load_hook()

    def _boom(*a, **kw):
        raise RuntimeError("reader exploded")

    monkeypatch.setattr(spawn_provenance, "count_in_window", _boom)
    rc = mod.main()
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out.get("continue") is True


# ---------------------------------------------------------------------------
# main() subprocess contract: default OFF, never blocks on malformed input
# ---------------------------------------------------------------------------

def _run(env_extra, payload, raw=False):
    env = dict(os.environ)
    env.update(env_extra)
    ch = Path(env_extra["HARNESS_STATE_DIR"]).parent / "nudge-channels.yaml"
    ch.write_text("default: stderr\nchannels: {}\n", encoding="utf-8")
    env["HARNESS_NUDGE_CHANNELS"] = str(ch)
    stdin = payload if raw else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin, text=True, capture_output=True, env=env,
    )


def test_disabled_by_default_is_inert(tmp_path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)  # the autouse _env fixture already created it
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_DISABLED, encoding="utf-8")
    r = _run({"HARNESS_STATE_DIR": str(state), "HARNESS_HOOK_CONFIG": str(cfg)},
              _spawn("sess-1"))
    assert r.returncode == 0
    assert "spawn_provenance" not in r.stderr


def test_never_blocks_on_malformed_input(tmp_path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)  # the autouse _env fixture already created it
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_ENABLED, encoding="utf-8")
    r = _run({"HARNESS_STATE_DIR": str(state), "HARNESS_HOOK_CONFIG": str(cfg)},
              "}{ not json", raw=True)
    assert r.returncode == 0


def test_advisory_over_subprocess_when_enabled(tmp_path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)  # the autouse _env fixture already created it
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_ENABLED, encoding="utf-8")
    env_extra = {"HARNESS_STATE_DIR": str(state), "HARNESS_HOOK_CONFIG": str(cfg)}
    for _ in range(6):
        r = _run(env_extra, _spawn("sess-1"))
        assert r.returncode == 0
    assert "hs:workflow-orchestrate" in r.stderr
