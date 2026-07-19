"""test_session_init_gc.py — SessionStart GC of leaked rule_nudge markers.

rule_nudge_hook writes ephemeral $TMPDIR/harness-rulenudge-* dedup flags but
never unlinks them, so they accumulate until a reboot. session_init garbage-
collects markers older than a TTL at SessionStart. Telemetry-class: fail-open,
never raises, never touches unrelated files.
"""
import os
import sys
import time
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import session_init  # noqa: E402

_PREFIX = "harness-rulenudge-"


def _marker(d: Path, name: str, age_hours: float) -> Path:
    p = d / (_PREFIX + name)
    p.write_text("1", encoding="utf-8")
    if age_hours:
        old = time.time() - age_hours * 3600
        os.utime(p, (old, old))
    return p


def test_stale_marker_removed(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    m = _marker(tmp_path, "sess-a", age_hours=48)
    assert session_init._gc_stale_nudge_markers() == 1
    assert not m.exists()


def test_fresh_marker_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    m = _marker(tmp_path, "sess-fresh", age_hours=0)
    assert session_init._gc_stale_nudge_markers() == 0
    assert m.exists()


def test_mixed(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    for i in range(3):
        _marker(tmp_path, "old-%d" % i, age_hours=48)
    fresh = [_marker(tmp_path, "new-%d" % i, age_hours=1) for i in range(2)]
    assert session_init._gc_stale_nudge_markers() == 3
    assert all(p.exists() for p in fresh)


def test_missing_tmpdir_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path / "does-not-exist"))
    assert session_init._gc_stale_nudge_markers() == 0  # no raise


def test_unrelated_files_untouched(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    other = tmp_path / "other-thing"
    other.write_text("keep", encoding="utf-8")
    os.utime(other, (time.time() - 99 * 3600,) * 2)  # ancient, but not ours
    _marker(tmp_path, "stale", age_hours=48)
    assert session_init._gc_stale_nudge_markers() == 1
    assert other.exists()


def test_never_raises_on_permission_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    _marker(tmp_path, "a", age_hours=48)
    _marker(tmp_path, "b", age_hours=48)

    real_unlink = Path.unlink
    calls = {"n": 0}

    def _boom(self, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("denied")
        return real_unlink(self, *a, **k)

    monkeypatch.setattr(Path, "unlink", _boom)
    # one unlink raises, the other still happens; the count reflects successes.
    assert session_init._gc_stale_nudge_markers() == 1


def test_ttl_boundary_respected(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    _marker(tmp_path, "just-over", age_hours=2)
    # a 1-hour TTL with injected now removes the 2h-old marker.
    assert session_init._gc_stale_nudge_markers(ttl_hours=1) == 1


def test_core_calls_gc(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("HARNESS_USER", "alice")
    called = {"n": 0}
    monkeypatch.setattr(session_init, "_gc_stale_nudge_markers",
                        lambda *a, **k: called.__setitem__("n", called["n"] + 1) or 0)
    session_init.core({})  # no session_id, must still run + GC, no raise
    assert called["n"] == 1
