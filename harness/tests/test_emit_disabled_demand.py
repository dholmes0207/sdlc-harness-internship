"""test_emit_disabled_demand.py — the demand emitter for off-skill re-enable loop.

When an off skill is reached (via hs:use proxy_run, or a router_block), a demand row is
appended to invocations.jsonl KEYED ON THE TARGET (not "hs:use"), so lens_skill_usage —
which aggregates by `skill` — can count how many distinct sessions wanted it back.

Dedup is PER-SESSION (one demand / skill / session), a variant of append_event_once: a
session that spams the proxy must not skew the "N distinct sessions" signal.

Emit is telemetry-class: it MUST fail-open — a broken sink never raises into the caller.

PYTEST_CURRENT_TEST is cleared per-test (telemetry_paths.disabled() reads it at call time)
so the append actually lands in the tmp sink.
"""
import importlib
import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _reload(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    monkeypatch.setenv("HARNESS_USER", "alice")  # hermetic actor, no git shell-out
    import telemetry_paths
    importlib.reload(telemetry_paths)
    import emit_disabled_demand
    importlib.reload(emit_disabled_demand)
    return emit_disabled_demand


def _rows(tmp_path):
    # Union legacy sink + per-session shards (P2 shards demand rows carrying a
    # session to invocations-<disc>.jsonl); the read-side lens unions the same.
    d = tmp_path / "state" / "telemetry"
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.glob("invocations*.jsonl")):
        if p.name.endswith(".bak"):
            continue
        out.extend(json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x.strip())
    return out


def test_writes_target_with_proxy_flag(tmp_path, monkeypatch):
    mod = _reload(tmp_path, monkeypatch)
    mod.emit("hs:critique", "proxy_run", "s1")
    rows = _rows(tmp_path)
    assert len(rows) == 1
    r = rows[0]
    # keyed on the TARGET, never "hs:use"
    assert r["skill"] == "hs:critique"
    assert r["proxy_invoked"] is True
    assert r["via"] == "proxy_run"
    assert r["session"] == "s1"
    assert r["actor"] and r["ts"]  # enriched by the append path


def test_dedup_per_session(tmp_path, monkeypatch):
    mod = _reload(tmp_path, monkeypatch)
    mod.emit("hs:critique", "proxy_run", "s1")
    mod.emit("hs:critique", "proxy_run", "s1")  # same (session, skill) → collapsed
    assert len(_rows(tmp_path)) == 1
    mod.emit("hs:critique", "proxy_run", "s2")  # different session → new row
    assert len(_rows(tmp_path)) == 2
    # dedup is NOT per-minute: keying is the whole-session tuple
    sessions = {r["session"] for r in _rows(tmp_path)}
    assert sessions == {"s1", "s2"}


def _write_session_file(tmp_path, sid, mtime):
    """Mirror session_init: state/sessions/<sid>.json written at SessionStart."""
    d = tmp_path / "state" / "sessions"
    d.mkdir(parents=True, exist_ok=True)
    p = d / ("%s.json" % sid)
    p.write_text(json.dumps({"actor": "user:alice", "ts": "2026-07-05T00:00:00+00:00"}),
                 encoding="utf-8")
    os.utime(p, (mtime, mtime))
    return p


def test_resolves_current_session_from_state_when_env_absent(tmp_path, monkeypatch):
    # The hs:use proxy runs emit as a bare Bash CLI with no session in its env; the
    # current session must be recovered from the newest state/sessions/<id>.json that
    # session_init wrote — otherwise every proxy demand row collapses to one bucket and
    # the distinct-session threshold (D1/D2) can never trip on the PRIMARY path.
    mod = _reload(tmp_path, monkeypatch)
    _write_session_file(tmp_path, "old-sess", mtime=1000)
    _write_session_file(tmp_path, "live-sess", mtime=2000)  # newest → the live session
    mod.emit("hs:critique", "proxy_run")  # NO explicit session, NO env
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert rows[0]["session"] == "live-sess"


def test_state_sessions_bucket_distinctly(tmp_path, monkeypatch):
    # Two sequential sessions (each the newest at its own emit time) must land as two
    # distinct demand rows — this is what lets the lens count "N distinct sessions".
    mod = _reload(tmp_path, monkeypatch)
    _write_session_file(tmp_path, "s-a", mtime=1000)
    mod.emit("hs:critique", "proxy_run")
    _write_session_file(tmp_path, "s-b", mtime=3000)  # a later session starts
    mod.emit("hs:critique", "proxy_run")
    rows = _rows(tmp_path)
    assert {r["session"] for r in rows} == {"s-a", "s-b"}
    assert len(rows) == 2


def test_env_session_wins_over_state(tmp_path, monkeypatch):
    mod = _reload(tmp_path, monkeypatch)
    _write_session_file(tmp_path, "state-sess", mtime=2000)
    monkeypatch.setenv("HARNESS_SESSION_ID", "env-sess")
    mod.emit("hs:critique", "proxy_run")  # env beats the state fallback
    assert _rows(tmp_path)[0]["session"] == "env-sess"


def test_no_session_source_is_failopen(tmp_path, monkeypatch):
    # No explicit arg, no env, no sessions dir → the row still lands (session-less,
    # collapsed) and nothing raises. Pins the documented degraded behavior.
    mod = _reload(tmp_path, monkeypatch)
    mod.emit("hs:critique", "proxy_run")  # must not raise
    rows = _rows(tmp_path)
    assert len(rows) == 1
    assert "session" not in rows[0] or not rows[0]["session"]


def test_emit_is_failopen(tmp_path, monkeypatch):
    mod = _reload(tmp_path, monkeypatch)
    # Force sink_path to a path whose parent is a FILE → open() fails on the real write
    # path; emit must swallow it and never raise (telemetry fail-open).
    import telemetry_paths
    bad = tmp_path / "afile"
    bad.write_text("x")
    monkeypatch.setattr(telemetry_paths, "sink_path", lambda name: bad / name)
    mod.emit("hs:critique", "proxy_run", "s1")  # must not raise
    # CLI entrypoint likewise returns 0 (telemetry never blocks the caller)
    assert mod.main(["--skill", "hs:critique", "--via", "proxy_run", "--session", "s1"]) == 0
