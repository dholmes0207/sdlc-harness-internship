"""Tests for spawn_provenance_guard.py — the Layer-1b Agent|Task
spawn-provenance BLOCK form (`HOOK_CLASS=compliance`, opt-in via
`spawn_provenance.block_enabled()`).

Properties under test (phase-7 T1-T9, the token-lifecycle decision in
plans/reports/spawn-provenance-token-lifecycle-sequential-thinking-260716.md):
  T1: block_enabled false -> core is inert (returns None every call).
  T2: no token, no active budget -> the un-ticketed threshold(5): <=5
      spawns pass, the 6th blocks.
  T3: an active token with sub_count=10 -> 10 spawns pass, the 11th blocks.
  T4: an expired token -> post-expiry spawns are counted FRESH against
      threshold(5); the pre-expiry burst does not false-block them.
  T5: a token's sub_count is clamped to sub_count_cap() at read time.
  T6: the nudge (P7-1a) and this guard recording the SAME spawn (shared
      tool_use_id) dedup to ONE counted event.
  T7: an internal crash inside core(), driven through the REAL dispatcher
      (hook_dispatch.py) — never spawn_provenance_guard.main(), which the
      in-process dispatcher bypasses — FAILS OPEN (exit 0), backed by the
      shipped `fail_open: true` on this hook's dispatch row (M5).
  T8: a real block drives exit 2 through the dispatcher, with an
      honest-ceiling reason (provenance/count/budget/shape, not strategy).
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

HOOK_PATH = _HOOKS / "spawn_provenance_guard.py"
NUDGE_PATH = _HOOKS / "spawn_provenance_nudge.py"

_ENABLED = "hooks:\n  spawn_provenance_guard: {enabled: true}\n"
_DISABLED = "hooks: {}\n"


def _load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _load_hook():
    return _load_module(HOOK_PATH, "spawn_provenance_guard")


def _load_nudge():
    return _load_module(NUDGE_PATH, "spawn_provenance_nudge")


@pytest.fixture
def env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_ENABLED, encoding="utf-8")
    spcfg = tmp_path / "spawn-provenance.yaml"
    # Pin a wide no-token window (1h) so the count-semantics tests driven
    # through the real dispatcher (each subprocess ~0.5-1s of startup) are
    # immune to wall-clock aging under heavy CI load: a burst's earliest
    # record never drifts out of the window before the block-triggering spawn.
    # Tests that specifically exercise aging write their own narrow W.
    # Pin threshold:5 explicitly so the off-by-one budget tests (T2/T6/T8/T9)
    # keep checking budget 5 after the code default rises to 8 (== shipped).
    spcfg.write_text("block_enabled: true\nunticketed_window_seconds: 3600\nthreshold: 5\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.setenv("HARNESS_SPAWN_PROVENANCE", str(spcfg))
    monkeypatch.setenv("HARNESS_USER", "tester")
    hook_runtime._reset_config_cache()
    yield {"state": state, "cfg": cfg, "spcfg": spcfg, "tmp": tmp_path}
    hook_runtime._reset_config_cache()


def _spawn(session="sess-1", tool="Agent", tool_use_id=None):
    d = {"tool_name": tool, "session_id": session,
         "tool_input": {"subagent_type": "hs:developer"}}
    if tool_use_id:
        d["tool_use_id"] = tool_use_id
    return d


def _write_spcfg(spcfg_path, body):
    spcfg_path.write_text(body, encoding="utf-8")


def _write_raw_spawn(tmp_path, session, ts, tool_use_id=None):
    """Append a spawn record with a CONTROLLED `ts` directly to the store —
    `record_spawn` always stamps `time.time()` (now), so simulating a
    pre-expiry burst (a ts in the PAST) needs a raw append, bypassing it."""
    p = tmp_path / "state" / "spawn-provenance" / "spawns.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    rec = {"session": session, "tool_use_id": tool_use_id, "actor": "tester", "ts": ts}
    with open(p, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")


def _write_token(tmp_path, run_id, session, ts=None, ttl=1800, sub_count=5):
    d = tmp_path / "state" / "orchestrate" / run_id
    d.mkdir(parents=True, exist_ok=True)
    ts = time.time() if ts is None else ts
    tok = {"session": session, "actor": "tester", "ts": ts, "expires_at": ts + ttl,
           "sub_count": sub_count, "run_id": run_id}
    (d / "token.json").write_text(json.dumps(tok), encoding="utf-8")
    return tok


# ---------------------------------------------------------------------------
# T1 — block_enabled false => inert (default ship state)
# ---------------------------------------------------------------------------

def test_t1_block_disabled_is_inert(env):
    _write_spcfg(env["spcfg"], "block_enabled: false\n")
    mod = _load_hook()
    for _ in range(20):
        assert mod.core(_spawn()) is None


# ---------------------------------------------------------------------------
# T2 — no token: <=5 pass, the 6th (prior=5) blocks
# ---------------------------------------------------------------------------

def test_t2_no_token_blocks_after_five(env):
    mod = _load_hook()
    for _ in range(5):
        assert mod.core(_spawn(session="sess-1")) is None
    reason = mod.core(_spawn(session="sess-1"))
    assert reason is not None
    assert "spawn_provenance" in reason
    assert "5" in reason  # names the budget


def test_t2_ignores_non_spawn_tool(env):
    mod = _load_hook()
    assert mod.core({"tool_name": "Bash", "session_id": "sess-1"}) is None
    assert spawn_provenance.count_in_window("sess-1") == 0  # never recorded


# ---------------------------------------------------------------------------
# no-token sliding window: records older than W age out, fresh burst still blocks
# ---------------------------------------------------------------------------

def test_no_token_ages_out_over_window(env):
    # A no-token session whose prior spawns are ALL older than W must not
    # block a new spawn — the old records have aged out of the sliding window
    # (the whole point of the fix: budget is "N in the last W seconds", not
    # "N ever this session").
    _write_spcfg(env["spcfg"], "block_enabled: true\nunticketed_window_seconds: 60\n")
    old = time.time() - 120  # two minutes ago, well outside the 60s window
    for i in range(8):
        _write_raw_spawn(env["tmp"], "sess-1", ts=old + i, tool_use_id="old-%d" % i)
    mod = _load_hook()
    assert mod.core(_spawn(session="sess-1")) is None


def test_no_token_fresh_burst_still_blocks(env):
    # The window still bites a fresh burst — 5 records inside W push prior to
    # the threshold, so the 6th spawn blocks (this is not a gate removal).
    _write_spcfg(env["spcfg"], "block_enabled: true\nunticketed_window_seconds: 60\nthreshold: 5\n")
    now = time.time()
    for i in range(5):
        _write_raw_spawn(env["tmp"], "sess-1", ts=now, tool_use_id="fresh-%d" % i)
    mod = _load_hook()
    assert mod.core(_spawn(session="sess-1")) is not None


# ---------------------------------------------------------------------------
# budget 8 semantics + honest true-no-token block message
# ---------------------------------------------------------------------------

def test_no_token_budget_eight_blocks_at_ninth(env):
    # Budget 8 = 8 spawns allowed in the window, the 9th blocks (prior=8>=8).
    # Locks the exact off-by-one (P-DEC-b): "8 pass, 9th blocks", mirroring
    # T2's "5 pass, 6th blocks". Config-driven, independent of the shipped yaml.
    _write_spcfg(env["spcfg"], "block_enabled: true\nthreshold: 8\nunticketed_window_seconds: 60\n")
    mod = _load_hook()
    for i in range(8):
        assert mod.core(_spawn(session="sess-1")) is None, "spawn #%d must pass" % (i + 1)
    reason = mod.core(_spawn(session="sess-1"))
    assert reason is not None
    assert "8" in reason  # names the budget


def test_block_reason_names_window_for_no_token(env):
    # A TRUE no-token block names the concrete window seconds and the age-out
    # recovery — honest here because the no-token window slides by wall-clock.
    _write_spcfg(env["spcfg"], "block_enabled: true\nthreshold: 8\nunticketed_window_seconds: 60\n")
    now = time.time()
    for i in range(8):
        _write_raw_spawn(env["tmp"], "sess-1", ts=now, tool_use_id="s-%d" % i)
    mod = _load_hook()
    reason = mod.core(_spawn(session="sess-1"))
    assert reason is not None
    assert "60" in reason  # the concrete window seconds
    assert "age out" in reason or "window" in reason
    assert "oldest" not in reason  # blocked spawns are still recorded (nudge) — do not over-claim


def test_block_reason_no_ageout_claim_for_expired_token(env):
    # An EXPIRED token also reports has_token=False, but its window
    # [expires_at, now] does NOT slide — so the block message must NOT promise
    # an age-out here (F1). Gate the age-out clause on true-no-token only.
    _write_spcfg(env["spcfg"], "block_enabled: true\nthreshold: 8\nunticketed_window_seconds: 60\n")
    past = time.time() - 5000
    _write_token(env["tmp"], "run-1", "sess-1", ts=past, ttl=10, sub_count=5)  # expired
    now = time.time()
    for i in range(8):
        _write_raw_spawn(env["tmp"], "sess-1", ts=now, tool_use_id="s-%d" % i)
    mod = _load_hook()
    reason = mod.core(_spawn(session="sess-1"))
    assert reason is not None
    assert "age out" not in reason
    assert "60" not in reason


def test_shipped_threshold_is_eight():
    # The shipped budget is 8, matching the code default.
    env = {"HARNESS_SPAWN_PROVENANCE": str(_DATA / "spawn-provenance.yaml")}
    assert spawn_provenance.threshold(env=env) == 8
    assert spawn_provenance.unticketed_window_seconds(env=env) == 60


# ---------------------------------------------------------------------------
# T3 — active token sub_count=10: 10 pass, 11th blocks
# ---------------------------------------------------------------------------

def test_t3_active_token_budget_ten(env):
    _write_token(env["tmp"], "run-1", "sess-1", sub_count=10)
    mod = _load_hook()
    for _ in range(10):
        assert mod.core(_spawn(session="sess-1")) is None
    assert mod.core(_spawn(session="sess-1")) is not None


# ---------------------------------------------------------------------------
# T4 — expired token: post-expiry spawns counted fresh vs threshold(5)
# ---------------------------------------------------------------------------

def test_t4_expired_token_resets_window(env):
    past = time.time() - 5000
    _write_token(env["tmp"], "run-1", "sess-1", ts=past, ttl=10, sub_count=10)
    # A pre-expiry burst (ts BEFORE expires_at=past+10) — simulating the
    # orchestrated run the token covered — must NOT count against the
    # post-expiry window.
    for i in range(8):
        _write_raw_spawn(env["tmp"], "sess-1", ts=past + 1 + i, tool_use_id="burst-%d" % i)
    mod = _load_hook()
    for _ in range(5):
        assert mod.core(_spawn(session="sess-1")) is None
    assert mod.core(_spawn(session="sess-1")) is not None


# ---------------------------------------------------------------------------
# T5 — sub_count clamped to the configured cap
# ---------------------------------------------------------------------------

def test_t5_sub_count_clamped_to_cap(env):
    _write_spcfg(env["spcfg"], "block_enabled: true\nsub_count_cap: 3\n")
    _write_token(env["tmp"], "run-1", "sess-1", sub_count=9999)
    mod = _load_hook()
    for _ in range(3):
        assert mod.core(_spawn(session="sess-1")) is None
    assert mod.core(_spawn(session="sess-1")) is not None


def test_t5_default_cap_is_32(env):
    _write_token(env["tmp"], "run-1", "sess-1", sub_count=9999)
    assert spawn_provenance.budget("sess-1") == 32


# ---------------------------------------------------------------------------
# T6 — nudge + guard recording the SAME spawn (shared tool_use_id) dedup to 1
# ---------------------------------------------------------------------------

def test_t6_dedups_with_nudge_via_tool_use_id(env):
    nudge = _load_nudge()
    guard = _load_hook()
    payload = _spawn(session="sess-1", tool_use_id="tid-shared")
    nudge.core(payload)
    guard.core(payload)
    assert spawn_provenance.count_in_window("sess-1") == 1


def test_t6_distinct_spawns_still_count_separately(env):
    nudge = _load_nudge()
    guard = _load_hook()
    for i in range(3):
        payload = _spawn(session="sess-1", tool_use_id="tid-%d" % i)
        nudge.core(payload)
        guard.core(payload)
    assert spawn_provenance.count_in_window("sess-1") == 3


# ---------------------------------------------------------------------------
# T7 — dispatcher-driven fail-open on an internal crash (M5)
# ---------------------------------------------------------------------------

def test_t7_dispatcher_driven_internal_crash_fails_open(env):
    # Static proof: the shipped registry row for this hook carries
    # fail_open: true — the flag that actually protects production, since
    # the in-process dispatcher calls core() directly and never runs this
    # module's own main().
    registry = yaml.safe_load((_DATA / "hook-dispatch.yaml").read_text(encoding="utf-8"))
    rows = registry["groups"]["PreToolUse:Agent|Task"]
    row = next(r for r in rows if r["name"] == "spawn_provenance_guard")
    assert row["fail_open"] is True
    assert row["class"] == "compliance"

    # Dynamic proof: force a genuine internal crash inside core() (a
    # monkeypatched dependency, per the phase's own T7 spec) and drive the
    # REAL dispatcher subprocess — never spawn_provenance_guard.main().
    script = (
        "import sys\n"
        "sys.path.insert(0, %r)\n"
        "sys.path.insert(0, %r)\n"
        "import hook_runtime, spawn_provenance, hook_dispatch\n"
        "def _boom(*a, **kw):\n"
        "    raise RuntimeError('forced internal crash (test)')\n"
        "spawn_provenance.window_start = _boom\n"
        "code = hook_dispatch.run(\n"
        "    ['PreToolUse', 'Agent|Task'],\n"
        "    stdin_text='{\"session_id\": \"sess-1\", \"tool_name\": \"Agent\", "
        "\"tool_use_id\": \"tid-crash\", "
        "\"tool_input\": {\"subagent_type\": \"hs:developer\"}}')\n"
        "sys.exit(code)\n"
    ) % (str(_HOOKS), str(_SCRIPTS))
    sub_env = dict(os.environ)
    sub_env["HARNESS_STATE_DIR"] = str(env["state"])
    sub_env["HARNESS_HOOK_CONFIG"] = str(env["cfg"])
    sub_env["HARNESS_SPAWN_PROVENANCE"] = str(env["spcfg"])
    r = subprocess.run([sys.executable, "-c", script], input="",
                        capture_output=True, text=True, env=sub_env)
    assert r.returncode == 0  # the crash must NOT block the spawn
    out = json.loads(r.stdout)
    assert out.get("continue") is True


# ---------------------------------------------------------------------------
# T8 — a real block drives exit 2 through the dispatcher, honest-ceiling reason
# ---------------------------------------------------------------------------

def _run_dispatcher(env, session, tool_use_id):
    sub_env = dict(os.environ)
    sub_env["HARNESS_STATE_DIR"] = str(env["state"])
    sub_env["HARNESS_HOOK_CONFIG"] = str(env["cfg"])
    sub_env["HARNESS_SPAWN_PROVENANCE"] = str(env["spcfg"])
    payload = json.dumps({"session_id": session, "tool_name": "Agent",
                          "tool_use_id": tool_use_id,
                          "tool_input": {"subagent_type": "hs:developer"}})
    return subprocess.run(
        [sys.executable, str(_HOOKS / "hook_dispatch.py"), "PreToolUse", "Agent|Task"],
        input=payload, capture_output=True, text=True, env=sub_env,
    )


def test_t8_real_block_exit2_through_dispatcher(env):
    for i in range(5):
        r = _run_dispatcher(env, "sess-1", "tid-%d" % i)
        assert r.returncode == 0
    r = _run_dispatcher(env, "sess-1", "tid-blocked")
    assert r.returncode == 2
    assert "BLOCKED" in r.stderr
    assert "spawn_provenance" in r.stderr
    assert "PROVENANCE" in r.stderr  # names what it checks
    assert "never strategy quality" in r.stderr  # the honest ceiling


def test_t8_disabled_by_default_never_blocks(tmp_path):
    """block_enabled default (false, unset) keeps the guard fully inert
    through the real dispatcher — the opt-in ship posture."""
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_ENABLED, encoding="utf-8")
    spcfg = tmp_path / "spawn-provenance.yaml"
    spcfg.write_text("threshold: 1\n", encoding="utf-8")  # block_enabled omitted -> False
    fake_env = {"state": state, "cfg": cfg, "spcfg": spcfg}
    for i in range(10):
        r = _run_dispatcher(fake_env, "sess-1", "tid-%d" % i)
        assert r.returncode == 0


# ---------------------------------------------------------------------------
# T9 — F1: nudge AND guard BOTH enabled through the REAL dispatcher must not
# false-block one spawn early. The dispatcher runs nudge-class cores (records
# the in-flight spawn) BEFORE compliance-class cores (this guard, which reads
# the count) — hook_dispatch.py:133-135. Without excluding the in-flight
# tool_use_id from the guard's own count, budget N becomes N-1: the 5th spawn
# blocks instead of the 6th. This is the dispatcher-driven, load-bearing proof
# the module-level T6 dedup tests (which call core() directly, not through the
# dispatcher's nudge-before-compliance ordering) do not exercise.
# ---------------------------------------------------------------------------

def test_t9_nudge_and_guard_both_enabled_blocks_at_sixth_not_fifth(env):
    both_enabled = (
        "hooks:\n"
        "  spawn_provenance_nudge: {enabled: true}\n"
        "  spawn_provenance_guard: {enabled: true}\n"
    )
    env["cfg"].write_text(both_enabled, encoding="utf-8")
    for i in range(5):
        r = _run_dispatcher(env, "sess-1", "tid-%d" % i)
        assert r.returncode == 0, (
            "spawn #%d must CONTINUE (budget=5, prior<5) — got exit %d: %s"
            % (i + 1, r.returncode, r.stderr)
        )
    r = _run_dispatcher(env, "sess-1", "tid-5")
    assert r.returncode == 2, (
        "spawn #6 must BLOCK (prior=5 >= budget=5) — got exit %d: %s"
        % (r.returncode, r.stderr)
    )
    assert "BLOCKED" in r.stderr
    assert "spawn_provenance" in r.stderr
