"""test_trace_shard_chain.py — per-session trace sharding (P2 CRUX).

After the host-redirect (P1) N worktrees write to ONE host trace dir. To keep
concurrent sessions off a single daily file, a trace write carrying a real
session goes to `trace-YYYYMMDD-<disc>.jsonl` — an INDEPENDENT hash-chain rooted
at its own genesis. A sessionless write keeps the shared legacy
`trace-YYYYMMDD.jsonl` (R2: NO pid fallback — a single line is < PIPE_BUF so the
shared append stays atomic; a pid shard would sprawl and kill rotation).

The sharded path skips the rollover/checkpoint machinery (that is legacy-only);
flock + FORK-tolerance are unchanged for concurrency WITHIN a shard.
"""
import importlib
import json
import os
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("HARNESS_USER", "tester")
    monkeypatch.delenv("HARNESS_AGENT", raising=False)
    for m in ("trace_log", "hook_runtime"):
        sys.modules.pop(m, None)
    import trace_log
    importlib.reload(trace_log)
    return trace_log


def _dir(tmp_path):
    return tmp_path / "state" / "trace"


def _files(tmp_path):
    d = _dir(tmp_path)
    return sorted(p.name for p in d.glob("trace-*.jsonl")) if d.exists() else []


def _recs(path: Path):
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


class TestShardDiscriminator:
    def test_none_when_no_session(self, monkeypatch, tmp_path):
        tl = _fresh(monkeypatch, tmp_path)
        import hook_runtime
        assert hook_runtime.shard_discriminator(None) is None
        assert hook_runtime.shard_discriminator("") is None

    def test_sanitized_and_capped(self, monkeypatch, tmp_path):
        # T5: a session with path-hostile chars is sanitized to [A-Za-z0-9_-],
        # capped at 12 — never a path traversal.
        tl = _fresh(monkeypatch, tmp_path)
        import hook_runtime
        disc = hook_runtime.shard_discriminator("../a/b .c/xxxxxxxxxxxxxx")
        assert disc is not None
        assert len(disc) <= 12
        assert "/" not in disc and ".." not in disc
        assert all(c.isalnum() or c in "-_" for c in disc)


class TestShardWrites:
    def test_t1_session_shards_with_genesis(self, monkeypatch, tmp_path):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="gate", event="e", session="S1")
        files = _files(tmp_path)
        assert files == ["trace-%s-S1.jsonl" % _today(tl)]
        recs = _recs(_dir(tmp_path) / files[0])
        assert len(recs) == 1
        assert len(recs[0]["chain_hash"]) == 64  # genesis chain present

    def test_t2_two_sessions_two_files(self, monkeypatch, tmp_path):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="g", event="e", session="S1")
        tl.append_event(hook="g", event="e", session="S2")
        today = _today(tl)
        assert _files(tmp_path) == [
            "trace-%s-S1.jsonl" % today, "trace-%s-S2.jsonl" % today]

    def test_t3_sessionless_stays_legacy(self, monkeypatch, tmp_path):
        # R2: no session -> shared legacy file, NO shard.
        tl = _fresh(monkeypatch, tmp_path)
        monkeypatch.delenv("HARNESS_SESSION_ID", raising=False)
        tl.append_event(hook="g", event="e", session=None)
        assert _files(tmp_path) == ["trace-%s.jsonl" % _today(tl)]

    def test_t4_same_session_chain_links(self, monkeypatch, tmp_path):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="g", event="e1", session="S1")
        tl.append_event(hook="g", event="e2", session="S1")
        recs = _recs(_dir(tmp_path) / ("trace-%s-S1.jsonl" % _today(tl)))
        assert len(recs) == 2
        # second record's chain incorporates the first (linked, not two genesis)
        expected = tl._chain_hash(recs[0]["chain_hash"],
                                  {k: v for k, v in recs[1].items() if k != "chain_hash"})
        assert recs[1]["chain_hash"] == expected

    def test_t6_read_only_dir_fail_open(self, monkeypatch, tmp_path):
        # Make the state path a FILE so trace dir mkdir fails -> swallowed.
        (tmp_path / "state").write_text("x", encoding="utf-8")
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
        for m in ("trace_log", "hook_runtime"):
            sys.modules.pop(m, None)
        import trace_log
        importlib.reload(trace_log)
        trace_log.append_event(hook="g", event="e", session="S1")  # must not raise

    def test_t10_no_pid_in_any_filename(self, monkeypatch, tmp_path):
        # R2: prove pid never leaks into a shard name.
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="g", event="e", session="S1")
        tl.append_event(hook="g", event="e", session=None)
        pid = str(os.getpid())
        for name in _files(tmp_path):
            assert pid not in name


def _today(tl):
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y%m%d")
