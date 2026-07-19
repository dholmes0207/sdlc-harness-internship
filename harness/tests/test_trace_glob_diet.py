"""test_trace_glob_diet.py — the audit trace globs its directory once per (process,
date), not once per write.

append_event re-scanned the trace dir on EVERY record to detect a date rollover.
Under a compliance guard firing on each Bash that is one glob per guard per tool —
thousands a day. The rollover check only matters when the day changes, so a
per-process cache of the last-checked date collapses the steady-state cost to zero
globs while still finalizing a checkpoint the moment the date actually rolls over.
"""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import trace_log  # noqa: E402


class _Clock:
    """A monkeypatch stand-in for trace_log.datetime with a settable now()."""
    def __init__(self, dt):
        self.dt = dt

    def now(self, tz=None):
        return self.dt


def _spy_trace_globs(monkeypatch):
    calls = []
    orig = Path.glob

    def spy(self, pattern):
        if pattern == "trace-*.jsonl":
            calls.append(pattern)
        return orig(self, pattern)

    monkeypatch.setattr(Path, "glob", spy)
    return calls


class TestGlobDiet:
    def test_glob_once_per_day_not_per_write(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        monkeypatch.setattr(trace_log, "_ROLLOVER_CHECKED_DATE", None, raising=False)
        calls = _spy_trace_globs(monkeypatch)
        for i in range(20):
            trace_log.append_event(hook="h", event="e", session=None, actor="user:x")
        # 20 writes must not scale the glob count: the rollover scan runs at most
        # once (first write this process), plus the first-of-day new-file scan.
        assert len(calls) <= 2, "steady-state must not glob per write, got %d" % len(calls)

    def test_glob_reruns_on_date_change(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        monkeypatch.setattr(trace_log, "_ROLLOVER_CHECKED_DATE", None, raising=False)
        clock = _Clock(datetime(2026, 6, 18, 10, 0, tzinfo=timezone.utc))
        monkeypatch.setattr(trace_log, "datetime", clock)
        # day 1
        trace_log.append_event(hook="h", event="e", session=None, actor="user:x")
        assert (tmp_path / "trace" / "trace-20260618.jsonl").is_file()
        # day 2 — the cached date is stale, so the rollover scan must re-run and
        # finalize a checkpoint for day 1.
        clock.dt = datetime(2026, 6, 19, 10, 0, tzinfo=timezone.utc)
        calls = _spy_trace_globs(monkeypatch)
        trace_log.append_event(hook="h", event="e", session=None, actor="user:x")
        assert (tmp_path / "trace" / "trace-20260619.jsonl").is_file()
        assert len(calls) >= 1, "a date change must re-glob to finalize the old day"
        cp = tmp_path / "trace" / "trace-checkpoint-20260618.json"
        assert cp.is_file(), "rollover must finalize the previous day's checkpoint"

    def test_chain_intact_after_diet(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        monkeypatch.setattr(trace_log, "_ROLLOVER_CHECKED_DATE", None, raising=False)
        for i in range(5):
            trace_log.append_event(hook="h", event="e%d" % i, session=None, actor="user:x")
        f = tmp_path / "trace" / ("trace-%s.jsonl" % datetime.now(timezone.utc).strftime("%Y%m%d"))
        recs = [json.loads(l) for l in f.read_text().splitlines() if l.strip()]
        assert len(recs) == 5
        # every record chains to its predecessor (the diet must not break the hash-chain)
        prev = None
        for r in recs:
            assert r.get("chain_hash") == trace_log._chain_hash(prev, r)
            prev = r["chain_hash"]
