"""test_resolver_completed_fallback.py — the opt-in `allow_completed` fallback.

Default resolve_active_plan stays in_progress-only (the in_progress-only invariant is
untouched). With allow_completed=True — passed only by the transport `push`
gate — a zero-in_progress board may anchor to the freshly-closed plan so a
close-then-push does not read as "no active plan".

Stale-completed guard: the completed plan is anchored ONLY when it is the newest
plan dir on the board (newest timestamped dir-name that parses a status). A newer
plan of any status means work moved on — no anchor, the gate blocks as before.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402


def _mk(root: Path, name, status):
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: %s\n---\n\n# Body\n" % (name, status),
        encoding="utf-8")
    return d


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    (tmp_path / "plans").mkdir()
    return tmp_path


class TestDefaultUnchanged:
    def test_completed_invisible_by_default(self, root):
        _mk(root, "260101-0000-done", status="completed")
        assert ac.resolve_active_plan(root) is None

    def test_completed_invisible_when_flag_false(self, root):
        _mk(root, "260101-0000-done", status="completed")
        assert ac.resolve_active_plan(root, allow_completed=False) is None


class TestCompletedFallback:
    def test_newest_completed_anchors(self, root):
        d = _mk(root, "260101-0000-done", status="completed")
        assert ac.resolve_active_plan(root, allow_completed=True) == d

    def test_in_progress_beats_completed(self, root):
        _mk(root, "260101-0000-done", status="completed")
        ip = _mk(root, "260101-0001-live", status="in_progress")
        assert ac.resolve_active_plan(root, allow_completed=True) == ip

    def test_two_completed_returns_newest(self, root):
        _mk(root, "260101-0000-old", status="completed")
        new = _mk(root, "260101-0002-new", status="completed")
        assert ac.resolve_active_plan(root, allow_completed=True) == new

    def test_newer_pending_blocks_fallback(self, root):
        # moved on: a completed plan sits under a NEWER pending one — do not
        # anchor to the old finished work.
        _mk(root, "260101-0000-done", status="completed")
        _mk(root, "260101-0003-next", status="pending")
        assert ac.resolve_active_plan(root, allow_completed=True) is None

    def test_newer_approved_blocks_fallback(self, root):
        _mk(root, "260101-0000-done", status="completed")
        _mk(root, "260101-0003-appr", status="approved")
        assert ac.resolve_active_plan(root, allow_completed=True) is None

    def test_ambiguity_guard_preserved(self, root):
        # >1 in_progress still refuses to guess even with the flag on.
        _mk(root, "260101-0001-a", status="in_progress")
        _mk(root, "260101-0002-b", status="in_progress")
        assert ac.resolve_active_plan(root, allow_completed=True) is None

    def test_env_override_still_wins(self, root, monkeypatch):
        _mk(root, "260101-0000-done", status="completed")
        target = _mk(root, "260101-0009-forced", status="completed")
        monkeypatch.setenv("HARNESS_ACTIVE_PLAN", "260101-0009-forced")
        assert ac.resolve_active_plan(root, allow_completed=True) == target

    def test_newer_junk_dir_without_planmd_is_skipped(self, root):
        # a newer dir with no plan.md is not a plan — it must not shadow the
        # completed fallback.
        d = _mk(root, "260101-0000-done", status="completed")
        (root / "plans" / "260101-0005-junk").mkdir()
        assert ac.resolve_active_plan(root, allow_completed=True) == d
