"""test_hlog.py — the diagnostic/perf log helper.

hlog is a stdlib-only diag stream: level-filtered (DEBUG shown only under
HARNESS_DEBUG), fail-open, rotated at 8MB like telemetry (one generation, NO
hash-chain — it is NOT the audit trace). It writes state/diag/diag.jsonl.
"""
import json
import os
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hlog  # noqa: E402


def _records(state_dir):
    p = Path(state_dir) / "diag" / "diag.jsonl"
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


class TestLevels:
    def test_debug_filtered_when_off(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        monkeypatch.delenv("HARNESS_DEBUG", raising=False)
        hlog.debug("d", x=1)
        hlog.info("i", x=2)
        hlog.warn("w", x=3)
        evs = [r["event"] for r in _records(tmp_path)]
        assert "d" not in evs        # DEBUG dropped below the INFO threshold
        assert "i" in evs and "w" in evs

    def test_debug_shown_when_on(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        monkeypatch.setenv("HARNESS_DEBUG", "1")
        hlog.debug("d", x=1)
        assert "d" in [r["event"] for r in _records(tmp_path)]

    def test_fields_carried(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        hlog.info("timing", hook="gate_stage", elapsed_ms=1.5)
        rec = _records(tmp_path)[-1]
        assert rec["hook"] == "gate_stage" and rec["elapsed_ms"] == 1.5
        assert rec["level"] == "INFO" and "ts" in rec


class TestRotate:
    def test_rotate_at_8mb_one_generation(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        p = tmp_path / "diag" / "diag.jsonl"
        p.parent.mkdir(parents=True)
        p.write_text("x" * (hlog._ROTATE_BYTES + 10), encoding="utf-8")
        hlog.info("after_rotate", x=1)
        assert (tmp_path / "diag" / "diag.jsonl.1").is_file()  # rotated generation
        # the fresh file holds only the new record (not the 8MB of filler)
        recs = _records(tmp_path)
        assert len(recs) == 1 and recs[0]["event"] == "after_rotate"


class TestFailOpen:
    def test_unwritable_dir_no_raise(self, tmp_path, monkeypatch):
        # diag path occupied by a plain file -> mkdir/append fail, swallowed
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        (tmp_path / "diag").write_text("occupied", encoding="utf-8")
        hlog.warn("w", x=1)  # must not raise
