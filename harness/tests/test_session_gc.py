"""test_session_gc.py — mtime-TTL reclaim of stale per-session state.

state/sessions/ accumulates one actor-cache file per session and never shrinks;
state/nudge-inject/ (the Phase-1 pointer) and state/skip-marks/ (Phase-3) grow the
same way. session_gc sweeps these subdirs by mtime, removing only files older than
the TTL, and a throttle keeps the sweep to once a day. Telemetry-class: a missing
dir or an unremovable file is swallowed, never raised.
"""
import os
import sys
import time
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import session_gc  # noqa: E402


def _aged_file(path: Path, age_days: float, base: float = None):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    b = base if base is not None else time.time()
    old = b - age_days * 86400
    os.utime(path, (old, old))


class TestGcState:
    def test_keeps_fresh_removes_stale(self, tmp_path):
        _aged_file(tmp_path / "sessions" / "fresh.json", age_days=1)
        _aged_file(tmp_path / "sessions" / "stale.json", age_days=30)
        removed = session_gc.gc_state(tmp_path, ttl_days=14)
        assert removed == 1
        assert (tmp_path / "sessions" / "fresh.json").is_file()
        assert not (tmp_path / "sessions" / "stale.json").exists()

    def test_cleans_nudge_inject_and_skip_marks(self, tmp_path):
        _aged_file(tmp_path / "nudge-inject" / "old.obs.json", age_days=30)
        _aged_file(tmp_path / "skip-marks" / "old.mark", age_days=30)
        _aged_file(tmp_path / "nudge-inject" / "new.obs.json", age_days=1)
        removed = session_gc.gc_state(tmp_path, ttl_days=14)
        assert removed == 2
        assert (tmp_path / "nudge-inject" / "new.obs.json").is_file()
        assert not (tmp_path / "nudge-inject" / "old.obs.json").exists()
        assert not (tmp_path / "skip-marks" / "old.mark").exists()

    def test_missing_dir_failopen(self, tmp_path):
        # none of the subdirs exist -> 0 removed, no raise
        assert session_gc.gc_state(tmp_path / "absent", ttl_days=14) == 0

    def test_unpatterned_files_untouched(self, tmp_path):
        # a subdir we do not sweep is left alone
        _aged_file(tmp_path / "trace" / "trace-old.jsonl", age_days=99)
        session_gc.gc_state(tmp_path, ttl_days=14)
        assert (tmp_path / "trace" / "trace-old.jsonl").is_file()


class TestGcThrottle:
    def test_runs_when_due_then_skips(self, tmp_path):
        t0 = 1_800_000_000.0
        _aged_file(tmp_path / "sessions" / "stale.json", age_days=30, base=t0)
        first = session_gc.gc_if_due(tmp_path, ttl_days=14, interval_hours=24, now=t0)
        assert first == 1  # ran, removed the stale file
        # a second call within the interval is skipped
        _aged_file(tmp_path / "sessions" / "stale2.json", age_days=30, base=t0)
        second = session_gc.gc_if_due(tmp_path, ttl_days=14, interval_hours=24,
                                      now=t0 + 3600)
        assert second == -1  # not due
        assert (tmp_path / "sessions" / "stale2.json").is_file()

    def test_runs_again_after_interval(self, tmp_path):
        t0 = 1_800_000_000.0
        session_gc.gc_if_due(tmp_path, ttl_days=14, interval_hours=24, now=t0)
        _aged_file(tmp_path / "sessions" / "stale.json", age_days=30, base=t0)
        again = session_gc.gc_if_due(tmp_path, ttl_days=14, interval_hours=24,
                                     now=t0 + 25 * 3600)
        assert again == 1  # interval elapsed -> ran again
