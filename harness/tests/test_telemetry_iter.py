"""test_telemetry_iter.py — the shared read-path helper every lens uses to walk a
JSONL telemetry sink: skip a missing file / unparseable line / parseable
non-object line / out-of-window or unplaceable-ts record. One home so no lens can
forget the non-object guard (the bug class the lens review found in two lenses).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import telemetry_paths as tp  # noqa: E402


def _seed(tmp_path, monkeypatch, raw):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True)
    (tel / "x.jsonl").write_text(raw, encoding="utf-8")


def test_yields_only_valid_in_window_dicts(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=99)).isoformat()
    _seed(tmp_path, monkeypatch,
          "[1,2,3]\n"                                    # non-object → skip
          "not json at all\n"                            # unparseable → skip
          '{"v":"keep","ts":"%s"}\n'                     # valid in-window → yield
          '{"v":"old","ts":"%s"}\n'                      # out of window → skip
          '{"v":"nots"}\n' % (now, old))                 # missing ts → skip
    got = [r["v"] for r in tp.iter_records_in_window("x.jsonl", 30)]
    assert got == ["keep"]


def test_missing_sink_yields_nothing(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state" / "telemetry").mkdir(parents=True)
    assert list(tp.iter_records_in_window("absent.jsonl", 30)) == []


def test_invalid_utf8_byte_is_skipped_not_fatal(tmp_path, monkeypatch):
    # The docstring promises FAIL-SOFT, but a strict whole-file decode crashed
    # the whole read on one bad byte. The streaming reader must skip the corrupt
    # line and still yield the valid records around it.
    now = datetime.now(timezone.utc).isoformat()
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True)
    good = ('{"v":"keep","ts":"%s"}\n' % now).encode("utf-8")
    bad = b'{"v":"\xff\xfe bad bytes","ts":"x"}\n'  # invalid UTF-8 mid-record
    (tel / "x.jsonl").write_bytes(good + bad + good)
    got = [r["v"] for r in tp.iter_records_in_window("x.jsonl", 30)]  # must not raise
    assert got == ["keep", "keep"]
