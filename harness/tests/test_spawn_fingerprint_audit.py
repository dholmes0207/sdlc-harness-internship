"""Tests for spawn_fingerprint_audit.py — P9 (Ask-3 C3) group-fingerprint
post-run audit, and the two spawn_provenance.py reader additions it depends
on (`groups_missing_early_write`, `load_active_token`).

This is a Stop-hook, NUDGE-class, OBSERVATION-only audit. It must NEVER emit
additionalContext / decision:block — a Stop-hook additionalContext forces a
re-invocation (memory `cc-stop-additionalcontext-causes-continuation`), which
this audit must not do. So `handle_stop` always returns None (never routed as
a live nudge by the dispatcher) and the only side effect on a miss is ONE
closed-vocab `emit_observation` record.

Properties under test (phase-9 T1-T5 + the token-loader unit):
  T1: every declared group has an early-written file -> groups_missing_early_write
      == [] and the hook writes no observation (silent).
  T2: one group missing its early-write -> groups_missing_early_write names it;
      the hook appends ONE observation record naming the group key.
  T3: no active token this run -> silent (fail-open), nothing written.
  T4: an unreadable/missing report_dir -> groups_missing_early_write == [],
      no crash, hook silent.
  T5: the audit never forces a continuation -- stdout carries a plain
      {"continue": true} blob, no additionalContext/reason.
  token-loader: load_active_token None with no token dir; the dict for a
      fresh fixture token; None (skip) for an expired one.
"""
import importlib.util
import json
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

HOOK_PATH = _HOOKS / "spawn_fingerprint_audit.py"
_SIGNAL = "spawn-group-early-write-missing"

_ENABLED = "hooks:\n  spawn_fingerprint_audit: {enabled: true}\n"
_DISABLED = "hooks: {}\n"


def _load_hook():
    spec = importlib.util.spec_from_file_location("spawn_fingerprint_audit", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_ENABLED, encoding="utf-8")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.setenv("HARNESS_USER", "tester")
    hook_runtime._reset_config_cache()
    yield {"state": state, "cfg": cfg}
    hook_runtime._reset_config_cache()


def _write_token(state_dir, run_id, session, report_dir, groups, expires_at=None):
    d = state_dir / "orchestrate" / run_id
    d.mkdir(parents=True, exist_ok=True)
    tok = {
        "mode": "workflow",
        "sub_count": len(groups),
        "groups": [{"key": k} for k in groups],
        "report_dir": str(report_dir),
        "session": session,
        "ts": "2026-07-16T00:00:00+00:00",
        "run_id": run_id,
    }
    if expires_at is not None:
        tok["expires_at"] = expires_at
    (d / "token.json").write_text(json.dumps(tok), encoding="utf-8")
    return tok


# ---------------------------------------------------------------------------
# spawn_provenance.groups_missing_early_write
# ---------------------------------------------------------------------------

def test_groups_missing_early_write_all_present_returns_empty(tmp_path):
    rdir = tmp_path / "reports"
    rdir.mkdir()
    (rdir / "alpha-findings.md").write_text("x", encoding="utf-8")
    (rdir / "beta-findings.md").write_text("x", encoding="utf-8")
    token = {"report_dir": str(rdir), "groups": [{"key": "alpha"}, {"key": "beta"}]}
    assert spawn_provenance.groups_missing_early_write(token) == []


def test_groups_missing_early_write_one_missing(tmp_path):
    rdir = tmp_path / "reports"
    rdir.mkdir()
    (rdir / "alpha-findings.md").write_text("x", encoding="utf-8")
    token = {"report_dir": str(rdir), "groups": [{"key": "alpha"}, {"key": "beta"}]}
    assert spawn_provenance.groups_missing_early_write(token) == ["beta"]


def test_groups_missing_early_write_case_insensitive_match(tmp_path):
    rdir = tmp_path / "reports"
    rdir.mkdir()
    (rdir / "ALPHA-report.md").write_text("x", encoding="utf-8")
    token = {"report_dir": str(rdir), "groups": [{"key": "alpha"}]}
    assert spawn_provenance.groups_missing_early_write(token) == []


def test_groups_missing_early_write_missing_report_dir_fails_open():
    token = {"groups": [{"key": "alpha"}]}
    assert spawn_provenance.groups_missing_early_write(token) == []


def test_groups_missing_early_write_unreadable_report_dir_fails_open(tmp_path):
    token = {"report_dir": str(tmp_path / "does-not-exist"),
             "groups": [{"key": "alpha"}]}
    assert spawn_provenance.groups_missing_early_write(token) == []


def test_groups_missing_early_write_malformed_token_fails_open():
    assert spawn_provenance.groups_missing_early_write(None) == []
    assert spawn_provenance.groups_missing_early_write("not a dict") == []
    assert spawn_provenance.groups_missing_early_write({}) == []


def test_groups_missing_early_write_no_groups_fails_open(tmp_path):
    rdir = tmp_path / "reports"
    rdir.mkdir()
    token = {"report_dir": str(rdir), "groups": []}
    assert spawn_provenance.groups_missing_early_write(token) == []


# ---------------------------------------------------------------------------
# spawn_provenance.load_active_token
# ---------------------------------------------------------------------------

def test_load_active_token_none_when_no_token_dir(tmp_path):
    env = {"HARNESS_STATE_DIR": str(tmp_path / "state")}
    assert spawn_provenance.load_active_token("sess-A", env=env) is None


def test_load_active_token_returns_dict_for_fresh_token(tmp_path):
    import time
    state = tmp_path / "state"
    rdir = tmp_path / "reports"
    rdir.mkdir()
    _write_token(state, "run-1", "sess-A", rdir, ["alpha"], expires_at=time.time() + 3600)
    env = {"HARNESS_STATE_DIR": str(state)}
    tok = spawn_provenance.load_active_token("sess-A", env=env)
    assert isinstance(tok, dict)
    assert tok["run_id"] == "run-1"
    assert tok["groups"] == [{"key": "alpha"}]


def test_load_active_token_skips_expired_token(tmp_path):
    import time
    state = tmp_path / "state"
    rdir = tmp_path / "reports"
    rdir.mkdir()
    _write_token(state, "run-1", "sess-A", rdir, ["alpha"], expires_at=time.time() - 10)
    env = {"HARNESS_STATE_DIR": str(state)}
    assert spawn_provenance.load_active_token("sess-A", env=env) is None


def test_load_active_token_session_mismatch(tmp_path):
    import time
    state = tmp_path / "state"
    rdir = tmp_path / "reports"
    rdir.mkdir()
    _write_token(state, "run-1", "sess-OTHER", rdir, ["alpha"], expires_at=time.time() + 3600)
    env = {"HARNESS_STATE_DIR": str(state)}
    assert spawn_provenance.load_active_token("sess-A", env=env) is None


# ---------------------------------------------------------------------------
# T1/T2/T3/T4 — handle_stop (direct-import unit tests)
# ---------------------------------------------------------------------------

def test_t1_all_groups_written_is_silent(tmp_path, _env):
    import time
    mod = _load_hook()
    state = _env["state"]
    rdir = tmp_path / "reports"
    rdir.mkdir()
    (rdir / "alpha-findings.md").write_text("x", encoding="utf-8")
    (rdir / "beta-findings.md").write_text("x", encoding="utf-8")
    _write_token(state, "run-1", "sess-A", rdir, ["alpha", "beta"],
                 expires_at=time.time() + 3600)
    store = tmp_path / "observations.jsonl"
    result = mod.handle_stop({"session_id": "sess-A"}, store=str(store))
    assert result is None
    assert not store.exists()


def test_t2_missing_group_writes_one_observation(tmp_path, _env):
    import time
    mod = _load_hook()
    state = _env["state"]
    rdir = tmp_path / "reports"
    rdir.mkdir()
    (rdir / "alpha-findings.md").write_text("x", encoding="utf-8")
    _write_token(state, "run-1", "sess-A", rdir, ["alpha", "beta"],
                 expires_at=time.time() + 3600)
    store = tmp_path / "observations.jsonl"
    result = mod.handle_stop({"session_id": "sess-A"}, store=str(store))
    assert result is None  # NEVER a live nudge value — observation-only
    assert store.is_file()
    lines = [ln for ln in store.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["signal"] == _SIGNAL
    assert "beta" in rec["payload"]
    assert "alpha" not in rec["payload"]  # alpha had its early-write, not flagged
    assert rec["actor"]
    assert rec["ts"]


def test_t3_no_token_is_silent(tmp_path, _env):
    mod = _load_hook()
    store = tmp_path / "observations.jsonl"
    result = mod.handle_stop({"session_id": "sess-no-token"}, store=str(store))
    assert result is None
    assert not store.exists()


def test_t4_unreadable_report_dir_is_silent(tmp_path, _env):
    import time
    mod = _load_hook()
    state = _env["state"]
    _write_token(state, "run-1", "sess-A", tmp_path / "does-not-exist", ["alpha"],
                 expires_at=time.time() + 3600)
    store = tmp_path / "observations.jsonl"
    result = mod.handle_stop({"session_id": "sess-A"}, store=str(store))
    assert result is None
    assert not store.exists()


def test_handle_stop_malformed_payload_is_silent(_env):
    mod = _load_hook()
    assert mod.handle_stop(None) is None
    assert mod.handle_stop("not a dict") is None
    assert mod.handle_stop({}) is None


def test_handle_stop_internal_crash_is_silent(tmp_path, _env, monkeypatch):
    import time
    mod = _load_hook()
    state = _env["state"]
    rdir = tmp_path / "reports"
    rdir.mkdir()
    _write_token(state, "run-1", "sess-A", rdir, ["alpha"], expires_at=time.time() + 3600)

    def _boom(*a, **kw):
        raise RuntimeError("reader exploded")

    monkeypatch.setattr(spawn_provenance, "groups_missing_early_write", _boom)
    store = tmp_path / "observations.jsonl"
    assert mod.handle_stop({"session_id": "sess-A"}, store=str(store)) is None
    assert not store.exists()


# ---------------------------------------------------------------------------
# T5 — never forces a continuation: subprocess main() stdout contract
# ---------------------------------------------------------------------------

def _run(env_extra, payload, raw=False):
    import os
    env = dict(os.environ)
    env.update(env_extra)
    stdin = payload if raw else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin, text=True, capture_output=True, env=env,
    )


def test_t5_stdout_is_plain_continue_never_additional_context(tmp_path, _env):
    import time
    state = _env["state"]
    cfg = _env["cfg"]
    rdir = tmp_path / "reports"
    rdir.mkdir()
    _write_token(state, "run-1", "sess-A", rdir, ["alpha", "beta"],
                 expires_at=time.time() + 3600)
    r = _run(
        {"HARNESS_STATE_DIR": str(state), "HARNESS_HOOK_CONFIG": str(cfg)},
        {"session_id": "sess-A"},
    )
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("continue") is True
    assert "additionalContext" not in out
    assert "hookSpecificOutput" not in out
    assert "decision" not in out
    assert "reason" not in out


def test_disabled_by_default_is_inert(tmp_path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_DISABLED, encoding="utf-8")
    r = _run({"HARNESS_STATE_DIR": str(state), "HARNESS_HOOK_CONFIG": str(cfg)},
              {"session_id": "sess-A"})
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out.get("continue") is True


def test_main_never_blocks_on_malformed_input(tmp_path):
    state = tmp_path / "state"
    state.mkdir(exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text(_ENABLED, encoding="utf-8")
    r = _run({"HARNESS_STATE_DIR": str(state), "HARNESS_HOOK_CONFIG": str(cfg)},
              "}{ not json", raw=True)
    assert r.returncode == 0
