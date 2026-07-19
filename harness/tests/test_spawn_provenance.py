"""Tests for spawn_provenance.py — the shared reader/counter backing the
Layer-1a Agent|Task spawn-provenance advisory nudge.

Properties under test:
  - record_spawn appends one {session, actor, ts} line, fail-open.
  - count_in_window is a scan-derived PRIOR count, windowed per session_id
    (T5: a different session's lines never count toward this one) and never
    merges the empty-session "unknown" bucket into a real session (T4).
  - count_in_window fails open (-> 0) on a missing/corrupt store.
  - record_spawn truncates the store so the scan stays bounded (T6).
  - has_orchestrate_token is a reader-only seam: False with no token dir
    (Layer-1b has no producer yet), True for a matching non-expired token,
    False for an expired one or a session mismatch.
  - threshold/block_enabled are fail-open config readers (mirrors
    model_policy's env-dict seam, no global-state monkeypatching).
"""
import json
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import spawn_provenance  # noqa: E402


def _env(tmp_path):
    return {"HARNESS_STATE_DIR": str(tmp_path / "state"), "HARNESS_USER": "tester"}


# ---- record_spawn / count_in_window -----------------------------------

def test_record_spawn_appends_actor_and_ts(tmp_path):
    env = _env(tmp_path)
    spawn_provenance.record_spawn("sess-A", env=env)
    store = tmp_path / "state" / "spawn-provenance" / "spawns.jsonl"
    assert store.is_file()
    rec = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    assert rec["session"] == "sess-A"
    assert rec["actor"]
    assert rec["ts"]


def test_count_in_window_missing_store_returns_zero(tmp_path):
    env = _env(tmp_path)
    assert spawn_provenance.count_in_window("sess-A", env=env) == 0


def test_count_in_window_counts_only_this_session(tmp_path):
    """T5: a prior/different session's lines do NOT count toward this session."""
    env = _env(tmp_path)
    for _ in range(3):
        spawn_provenance.record_spawn("sess-A", env=env)
    for _ in range(7):
        spawn_provenance.record_spawn("sess-OTHER", env=env)
    assert spawn_provenance.count_in_window("sess-A", env=env) == 3
    assert spawn_provenance.count_in_window("sess-OTHER", env=env) == 7
    assert spawn_provenance.count_in_window("sess-NEVER-SEEN", env=env) == 0


def test_count_in_window_buckets_empty_session_separately(tmp_path):
    """T4: an empty/missing session_id is its own "unknown" bucket, never
    merged into a real session's count."""
    env = _env(tmp_path)
    for _ in range(2):
        spawn_provenance.record_spawn("sess-A", env=env)
    for _ in range(4):
        spawn_provenance.record_spawn("", env=env)
    assert spawn_provenance.count_in_window("sess-A", env=env) == 2
    assert spawn_provenance.count_in_window("", env=env) == 4
    # None coerces to the same "" bucket.
    assert spawn_provenance.count_in_window(None, env=env) == 4


def test_count_in_window_fail_open_on_corrupt_store(tmp_path):
    env = _env(tmp_path)
    store = tmp_path / "state" / "spawn-provenance" / "spawns.jsonl"
    store.parent.mkdir(parents=True)
    store.write_text("not json at all\n{also not json\n", encoding="utf-8")
    assert spawn_provenance.count_in_window("sess-A", env=env) == 0


def test_record_spawn_never_raises_on_unwritable_state_dir(tmp_path):
    # Point state dir at a path that collides with an existing FILE (not a
    # dir) so mkdir fails -- record_spawn must swallow it (fail-open).
    blocker = tmp_path / "state"
    blocker.write_text("i am a file, not a dir", encoding="utf-8")
    env = {"HARNESS_STATE_DIR": str(blocker)}
    spawn_provenance.record_spawn("sess-A", env=env)  # must not raise


# ---- truncation (T6) ----------------------------------------------------

def test_truncation_keeps_scan_bounded(tmp_path, monkeypatch):
    env = _env(tmp_path)
    monkeypatch.setattr(spawn_provenance, "_MAX_LINES", 10)
    for i in range(25):
        spawn_provenance.record_spawn("sess-A", env=env)
    store = tmp_path / "state" / "spawn-provenance" / "spawns.jsonl"
    lines = [l for l in store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) <= 10
    # The most recent record survives truncation (kept, not dropped).
    assert spawn_provenance.count_in_window("sess-A", env=env) == len(lines)


def test_truncation_preserves_cross_session_scoping(tmp_path, monkeypatch):
    env = _env(tmp_path)
    monkeypatch.setattr(spawn_provenance, "_MAX_LINES", 6)
    for _ in range(4):
        spawn_provenance.record_spawn("sess-A", env=env)
    for _ in range(4):
        spawn_provenance.record_spawn("sess-B", env=env)
    store = tmp_path / "state" / "spawn-provenance" / "spawns.jsonl"
    lines = [l for l in store.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(lines) <= 6
    total = (spawn_provenance.count_in_window("sess-A", env=env)
             + spawn_provenance.count_in_window("sess-B", env=env))
    assert total == len(lines)


# ---- has_orchestrate_token (Layer-1b reader seam) ------------------------

def _write_token(tmp_path, run_id, session, expires_at=None):
    d = tmp_path / "state" / "orchestrate" / run_id
    d.mkdir(parents=True, exist_ok=True)
    tok = {"session": session, "actor": "tester", "ts": "2026-07-16T00:00:00+00:00"}
    if expires_at is not None:
        tok["expires_at"] = expires_at
    (d / "token.json").write_text(json.dumps(tok), encoding="utf-8")


def test_has_orchestrate_token_false_when_no_token_dir(tmp_path):
    env = _env(tmp_path)
    assert spawn_provenance.has_orchestrate_token("sess-A", env=env) is False


def test_has_orchestrate_token_true_for_matching_active_token(tmp_path):
    env = _env(tmp_path)
    _write_token(tmp_path, "run-1", "sess-A", expires_at=time.time() + 3600)
    assert spawn_provenance.has_orchestrate_token("sess-A", env=env) is True


def test_has_orchestrate_token_false_for_session_mismatch(tmp_path):
    env = _env(tmp_path)
    _write_token(tmp_path, "run-1", "sess-OTHER", expires_at=time.time() + 3600)
    assert spawn_provenance.has_orchestrate_token("sess-A", env=env) is False


def test_has_orchestrate_token_false_for_expired_token(tmp_path):
    env = _env(tmp_path)
    _write_token(tmp_path, "run-1", "sess-A", expires_at=time.time() - 10)
    assert spawn_provenance.has_orchestrate_token("sess-A", env=env) is False


def test_has_orchestrate_token_false_on_corrupt_token(tmp_path):
    env = _env(tmp_path)
    d = tmp_path / "state" / "orchestrate" / "run-1"
    d.mkdir(parents=True)
    (d / "token.json").write_text("{ not json", encoding="utf-8")
    assert spawn_provenance.has_orchestrate_token("sess-A", env=env) is False


# ---- threshold / block_enabled (fail-open config readers) ---------------

def _cfg(tmp_path, body):
    p = tmp_path / "spawn-provenance.yaml"
    p.write_text(body, encoding="utf-8")
    return {"HARNESS_SPAWN_PROVENANCE": str(p)}


def test_threshold_default_when_no_config():
    # Code default matches the shipped budget (8): the gate fails toward
    # OVER-block, so a config that degrades to "block on, threshold missing"
    # must land on the shipped 8, not a stricter 5 that would resurrect the
    # very over-count bug this budget replaces.
    assert spawn_provenance.threshold(env={}) == 8


def test_threshold_reads_config_override(tmp_path):
    env = _cfg(tmp_path, "threshold: 8\n")
    assert spawn_provenance.threshold(env=env) == 8


def test_threshold_fail_open_on_malformed_config(tmp_path):
    env = _cfg(tmp_path, "not: [valid, yaml: :::\n")
    assert spawn_provenance.threshold(env=env) == 8  # fail-open to the code default (== shipped)


def test_block_enabled_defaults_false_when_key_absent(tmp_path):
    # fail-open safety: a config that OMITS block_enabled must NEVER enable
    # blocking on its own — only an explicit true does.
    env = _cfg(tmp_path, "threshold: 5\n")
    assert spawn_provenance.block_enabled(env=env) is False


def test_block_enabled_shipped_default_is_on():
    # the shipped spawn-provenance.yaml turns the block form ON by decision
    # (a block makes the model aware + it recovers via hs:workflow-orchestrate).
    assert spawn_provenance.block_enabled(env={}) is True


def test_block_enabled_reads_config(tmp_path):
    env = _cfg(tmp_path, "block_enabled: true\n")
    assert spawn_provenance.block_enabled(env=env) is True


# ---- record_spawn tool_use_id + count_in_window dedup/window (Layer-1b) -----

def test_record_spawn_carries_tool_use_id(tmp_path):
    env = _env(tmp_path)
    spawn_provenance.record_spawn("sess-A", tool_use_id="tid-1", env=env)
    store = tmp_path / "state" / "spawn-provenance" / "spawns.jsonl"
    rec = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    assert rec["tool_use_id"] == "tid-1"


def test_record_spawn_ts_is_numeric_epoch(tmp_path):
    env = _env(tmp_path)
    before = time.time()
    spawn_provenance.record_spawn("sess-A", env=env)
    after = time.time()
    store = tmp_path / "state" / "spawn-provenance" / "spawns.jsonl"
    rec = json.loads(store.read_text(encoding="utf-8").splitlines()[0])
    assert isinstance(rec["ts"], (int, float))
    assert before <= rec["ts"] <= after


def test_record_spawn_without_tool_use_id_still_works(tmp_path):
    """Backward-compatible: old no-tool_use_id callers (P7-1a nudge) unaffected."""
    env = _env(tmp_path)
    spawn_provenance.record_spawn("sess-A", env=env)
    assert spawn_provenance.count_in_window("sess-A", env=env) == 1


def test_count_in_window_dedups_same_tool_use_id(tmp_path):
    """T6: the same tool_use_id recorded twice (nudge + guard) counts ONCE."""
    env = _env(tmp_path)
    spawn_provenance.record_spawn("sess-A", tool_use_id="tid-dup", env=env)
    spawn_provenance.record_spawn("sess-A", tool_use_id="tid-dup", env=env)
    assert spawn_provenance.count_in_window("sess-A", env=env) == 1


def test_count_in_window_no_tool_use_id_counts_each_as_unique(tmp_path):
    env = _env(tmp_path)
    for _ in range(3):
        spawn_provenance.record_spawn("sess-A", env=env)  # no tool_use_id
    assert spawn_provenance.count_in_window("sess-A", env=env) == 3


def test_count_in_window_since_ts_filters_older_records(tmp_path):
    env = _env(tmp_path)
    spawn_provenance.record_spawn("sess-A", tool_use_id="old-1", env=env)
    cutoff = time.time()
    spawn_provenance.record_spawn("sess-A", tool_use_id="new-1", env=env)
    spawn_provenance.record_spawn("sess-A", tool_use_id="new-2", env=env)
    assert spawn_provenance.count_in_window("sess-A", since_ts=cutoff, env=env) == 2
    assert spawn_provenance.count_in_window("sess-A", env=env) == 3  # no filter -> all


# ---- window_start / budget (Layer-1b epoch + budget resolution) -------------

def test_window_start_no_token_is_sliding_window(tmp_path):
    # No token on record -> the no-token lane windows by wall-clock time, not
    # a session-cumulative epoch of 0.0. window_start is now `now - W`, so a
    # record older than W seconds ages out of count_in_window's since_ts.
    # Pin BOTH the empty state-dir (no token) AND W=60 (independent of the
    # shipped yaml, so bumping shipped W never reddens this behavioural test).
    env = {**_env(tmp_path), **_cfg(tmp_path, "unticketed_window_seconds: 60\n")}
    before = time.time()
    ws = spawn_provenance.window_start("sess-A", env=env)
    after = time.time()
    assert ws != 0.0  # no longer the session-cumulative epoch
    assert before - 60 - 1 <= ws <= after - 60 + 1  # ~ now - W, +/-1s slack


def test_window_start_active_token_is_token_ts(tmp_path):
    env = _env(tmp_path)
    ts = time.time()
    _write_token_numeric(tmp_path, "run-1", "sess-A", ts=ts, expires_at=ts + 1800)
    assert spawn_provenance.window_start("sess-A", env=env) == ts


def test_window_start_expired_token_is_expires_at(tmp_path):
    env = _env(tmp_path)
    ts = time.time() - 5000
    expires = ts + 10  # already expired
    _write_token_numeric(tmp_path, "run-1", "sess-A", ts=ts, expires_at=expires)
    assert spawn_provenance.window_start("sess-A", env=env) == expires


def test_budget_no_token_is_threshold(tmp_path):
    env = _env(tmp_path)
    assert spawn_provenance.budget("sess-A", env=env) == spawn_provenance.threshold(env=env)


def test_budget_active_token_uses_sub_count(tmp_path):
    env = _env(tmp_path)
    ts = time.time()
    _write_token_numeric(tmp_path, "run-1", "sess-A", ts=ts, expires_at=ts + 1800, sub_count=10)
    assert spawn_provenance.budget("sess-A", env=env) == 10


def test_budget_clamps_sub_count_to_cap(tmp_path):
    env = _env(tmp_path)
    ts = time.time()
    _write_token_numeric(tmp_path, "run-1", "sess-A", ts=ts, expires_at=ts + 1800, sub_count=9999)
    assert spawn_provenance.budget("sess-A", env=env) == spawn_provenance.sub_count_cap(env=env)


def test_budget_expired_token_falls_back_to_threshold(tmp_path):
    env = _env(tmp_path)
    ts = time.time() - 5000
    _write_token_numeric(tmp_path, "run-1", "sess-A", ts=ts, expires_at=ts + 10, sub_count=10)
    assert spawn_provenance.budget("sess-A", env=env) == spawn_provenance.threshold(env=env)


def test_window_start_and_budget_pair_the_same_token_with_two_active(tmp_path):
    # N1: 2+ ACTIVE tokens for one session, written so the OLDER-by-ts token
    # sorts FIRST in iterdir order (run-1 < run-2 lexically, ts=now-100,
    # sub_count=3) while run-2 is the MOST RECENT by ts (ts=now, sub_count=7).
    # A selector keyed on iterdir order (the old budget(), via
    # _find_active_token's "first active match") and one keyed on ts order
    # (the old window_start(), via _most_recent_token) would then read
    # DIFFERENT tokens: budget=3 (run-1) paired with an epoch of `now`
    # (run-2's ts) — a mismatched pair. Both must select the SAME token: the
    # most-recent ACTIVE one by ts (run-2) — its ts backs window_start AND
    # its sub_count backs budget.
    env = _env(tmp_path)
    now = time.time()
    _write_token_numeric(tmp_path, "run-1", "sess-A", ts=now - 100, expires_at=now + 1700, sub_count=3)
    _write_token_numeric(tmp_path, "run-2", "sess-A", ts=now, expires_at=now + 1800, sub_count=7)
    assert spawn_provenance.window_start("sess-A", env=env) == now
    assert spawn_provenance.budget("sess-A", env=env) == 7


def _write_token_numeric(tmp_path, run_id, session, ts, expires_at, sub_count=5):
    d = tmp_path / "state" / "orchestrate" / run_id
    d.mkdir(parents=True, exist_ok=True)
    tok = {"session": session, "actor": "tester", "ts": ts, "expires_at": expires_at,
           "sub_count": sub_count, "run_id": run_id}
    (d / "token.json").write_text(json.dumps(tok), encoding="utf-8")


# ---- sub_count_cap / token_ttl_seconds (fail-open config readers) -----------

def test_sub_count_cap_default():
    assert spawn_provenance.sub_count_cap(env={}) == 32


def test_sub_count_cap_reads_config(tmp_path):
    env = _cfg(tmp_path, "sub_count_cap: 12\n")
    assert spawn_provenance.sub_count_cap(env=env) == 12


def test_token_ttl_seconds_default():
    assert spawn_provenance.token_ttl_seconds(env={}) == 1800


def test_token_ttl_seconds_reads_config(tmp_path):
    env = _cfg(tmp_path, "token_ttl_seconds: 60\n")
    assert spawn_provenance.token_ttl_seconds(env=env) == 60


# ---- unticketed_window_seconds (no-token sliding window, fail-open reader) ----

def test_unticketed_window_seconds_default():
    assert spawn_provenance.unticketed_window_seconds(env={}) == 60


def test_unticketed_window_seconds_reads_config(tmp_path):
    env = _cfg(tmp_path, "unticketed_window_seconds: 30\n")
    assert spawn_provenance.unticketed_window_seconds(env=env) == 30


def test_unticketed_window_seconds_fail_open_on_malformed(tmp_path):
    env = _cfg(tmp_path, "not: [valid, yaml: :::\n")
    assert spawn_provenance.unticketed_window_seconds(env=env) == 60


# ---- workflow_width (P8 W1 — static script-parse fan-out estimate) --------
#
# CC's PreToolUse(Workflow) payload carries the FULL script TEXT in
# `tool_input.script`, not a structured width arg (WP1-verified,
# plans/260715-0021-subagent-spawn-guards-and-reinject/probes/
# WP1-workflow-pretooluse.md). workflow_width is therefore a STATIC PARSE
# (regex + bracket-depth scan) of that text, never an execution.


def test_workflow_width_counts_agent_calls():
    script = "phase('a'); agent({}); agent({}); agent({});"
    assert spawn_provenance.workflow_width(script) == 3


def test_workflow_width_counts_parallel_array_of_agent_calls():
    script = "parallel([()=>agent({}),()=>agent({})])"
    assert spawn_provenance.workflow_width(script) == 2


def test_workflow_width_counts_pipeline_literal_array_over_single_agent_call():
    # ONE agent() call in the mapper fn, but 3 items in the literal array —
    # the array-literal heuristic must win over the raw agent(-count.
    script = "pipeline(['a','b','c'], (item) => agent(item))"
    assert spawn_provenance.workflow_width(script) == 3


def test_workflow_width_no_agent_is_zero():
    script = "log('hello'); return {ok:true}"
    assert spawn_provenance.workflow_width(script) == 0


def test_workflow_width_malformed_input_is_zero():
    assert spawn_provenance.workflow_width(None) == 0
    assert spawn_provenance.workflow_width(12345) == 0
    assert spawn_provenance.workflow_width("") == 0
    assert spawn_provenance.workflow_width("   ") == 0
    assert spawn_provenance.workflow_width(["not", "a", "string"]) == 0
