"""test_telemetry_shard.py — per-session telemetry sink sharding (P2).

A telemetry record carrying a real session is written to `<stem>-<disc>.jsonl`;
a sessionless record keeps the shared legacy `<stem>.jsonl` (R2 — no pid). Each
shard rotates independently at 8MB, ending rotation races. R7: telemetry is OFF
under pytest (PYTEST_CURRENT_TEST) — the arm helper deletes that marker so the
write actually happens (else the test passes vacuously).
"""
import importlib
import json
import os
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import telemetry_paths as tp  # noqa: E402


def _arm(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    monkeypatch.delenv("HARNESS_SESSION_ID", raising=False)
    for ci in ("CI", "GITLAB_CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(ci, raising=False)
    monkeypatch.setenv("HARNESS_USER", "alice")
    importlib.reload(tp)
    monkeypatch.setattr(tp, "_actor_cache", None)
    return tp


def _telem_dir(tmp_path):
    return tmp_path / "state" / "telemetry"


def _names(tmp_path):
    d = _telem_dir(tmp_path)
    return sorted(p.name for p in d.glob("*.jsonl")) if d.exists() else []


class TestSinkPath:
    def test_session_inserts_discriminator(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        assert tp.sink_path("invocations.jsonl", session="S1").name == \
            "invocations-S1.jsonl"

    def test_no_session_keeps_legacy_name(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        assert tp.sink_path("invocations.jsonl").name == "invocations.jsonl"
        assert tp.sink_path("invocations.jsonl", session=None).name == \
            "invocations.jsonl"


class TestShardWrites:
    def test_t7_session_record_shards(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        tp.append_event("invocations.jsonl", {"event": "x", "session": "S1"})
        assert "invocations-S1.jsonl" in _names(tmp_path)
        recs = [json.loads(l) for l in
                (_telem_dir(tmp_path) / "invocations-S1.jsonl").read_text().splitlines()]
        assert recs[0]["session"] == "S1"

    def test_t7b_sessionless_record_legacy(self, monkeypatch, tmp_path):
        # R2: no session in record and HARNESS_SESSION_ID unset -> legacy sink.
        _arm(monkeypatch, tmp_path)
        tp.append_event("invocations.jsonl", {"event": "x"})
        assert _names(tmp_path) == ["invocations.jsonl"]

    def test_t8_shard_rotates_independently(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        monkeypatch.setattr(tp, "MAX_SINK_BYTES", 10)  # tiny so 2nd write rotates
        tp.append_event("invocations.jsonl", {"event": "a", "session": "S1"})
        tp.append_event("invocations.jsonl", {"event": "b", "session": "S1"})
        names = _names(tmp_path)
        d = _telem_dir(tmp_path)
        assert (d / "invocations-S1.jsonl").exists()
        assert (d / "invocations-S1.jsonl.bak").exists()

    def test_t9_same_session_same_shard(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        tp.append_event("invocations.jsonl", {"event": "a", "session": "S1"})
        tp.append_event("invocations.jsonl", {"event": "b", "session": "S1"})
        recs = (_telem_dir(tmp_path) / "invocations-S1.jsonl").read_text().splitlines()
        assert len(recs) == 2

    def test_t10_no_pid_in_names(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        tp.append_event("invocations.jsonl", {"event": "a", "session": "S1"})
        tp.append_event("invocations.jsonl", {"event": "b"})
        pid = str(os.getpid())
        for name in _names(tmp_path):
            assert pid not in name
