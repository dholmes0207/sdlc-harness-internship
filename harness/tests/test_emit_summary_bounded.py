"""test_emit_summary_bounded.py — non-regression guard for the session summary read.

An earlier brief claimed emit_session_summary did an O(n²) splitlines() over the
whole transcript. The code already reads a BOUNDED head+tail window (256KB each);
this test pins that so a future refactor cannot regress into a full-file read on a
multi-MB transcript. It asserts behaviour, not the current line numbers.
"""
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import emit_session_summary as ess  # noqa: E402


def test_read_tail_is_bounded(tmp_path):
    p = tmp_path / "transcript.jsonl"
    # 2 MB of filler, far larger than the 256KB tail window
    line = ("{\"x\": \"" + "y" * 200 + "\"}\n")
    with p.open("w", encoding="utf-8") as fh:
        while p.stat().st_size < 2_000_000:
            fh.write(line * 100)
    assert p.stat().st_size > ess.TAIL_BYTES * 4
    tail = ess.read_tail(str(p))
    # the tail read never returns more than the window (+ one decode slack byte)
    assert len(tail.encode("utf-8")) <= ess.TAIL_BYTES + 1


def test_read_tail_no_full_read(tmp_path, monkeypatch):
    p = tmp_path / "transcript.jsonl"
    p.write_text("a" * (ess.TAIL_BYTES * 3), encoding="utf-8")
    calls = []
    orig = Path.read_text

    def spy(self, *a, **k):
        calls.append(str(self))
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy)
    ess.read_tail(str(p))
    assert str(p) not in calls, "read_tail must not full-read via read_text"
