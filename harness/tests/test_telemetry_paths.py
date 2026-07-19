"""test_telemetry_paths.py — the USAGE telemetry ledger (ported PS write-path).

This is the rotating usage ledger (8MB, one .bak generation) — distinct from
the audit trace (trace_log, never rotated). Fail-open contract: a telemetry
write must never break the op it observes. Harness additions: every record is
enriched with actor (+ ts, + session when known); env names are HARNESS_*;
the sink dir derives from harness_paths (HARNESS_STATE_DIR seam).

Telemetry is OFF under pytest by default (PYTEST_CURRENT_TEST) — tests that
exercise the write path delete that marker explicitly.
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
    """Point the sink at tmp and ARM telemetry (pytest marker off, CI off)."""
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    for ci in ("CI", "GITLAB_CI", "GITHUB_ACTIONS"):
        monkeypatch.delenv(ci, raising=False)
    monkeypatch.setenv("HARNESS_USER", "alice")
    # actor is lazily cached per process — reset so each test resolves fresh
    monkeypatch.setattr(tp, "_actor_cache", None)


def _lines(p: Path):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()]


class TestAppendEvent:
    def test_writes_jsonl_enriched_with_actor_and_ts(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        tp.append_event("usage.jsonl", {"event": "skill", "name": "hs:plan"})
        recs = _lines(tp.sink_path("usage.jsonl"))
        assert len(recs) == 1
        assert recs[0]["event"] == "skill"
        assert recs[0]["actor"] == "user:alice"
        assert recs[0]["ts"]  # enriched, ISO-ish non-empty

    def test_enriched_ts_is_strict_isoformat(self, monkeypatch, tmp_path):
        # The ts a writer emits must round-trip through the read side. The
        # read side uses datetime.fromisoformat, which before Python 3.11
        # accepts ONLY isoformat() output — a strftime("%z") offset without
        # the colon (+0700) breaks it. Pin the exact shape: colon'd offset,
        # timezone-aware.
        import re
        from datetime import datetime
        _arm(monkeypatch, tmp_path)
        tp.append_event("usage.jsonl", {"event": "x"})
        ts = _lines(tp.sink_path("usage.jsonl"))[0]["ts"]
        assert re.fullmatch(
            r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:[+-]\d{2}:\d{2}|\+00:00)", ts)
        assert datetime.fromisoformat(ts).tzinfo is not None

    def test_caller_supplied_actor_is_preserved(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        tp.append_event("usage.jsonl", {"event": "x", "actor": "user:bob"})
        recs = _lines(tp.sink_path("usage.jsonl"))
        assert recs[0]["actor"] == "user:bob"

    def test_disabled_env_kills_write(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        monkeypatch.setenv("HARNESS_TELEMETRY_DISABLED", "1")
        tp.append_event("usage.jsonl", {"event": "x"})
        assert not (tmp_path / "state" / "telemetry" / "usage.jsonl").exists()

    def test_disabled_under_pytest_marker_by_default(self, monkeypatch, tmp_path):
        # No delenv of PYTEST_CURRENT_TEST → telemetry stays off; real test
        # runs never pollute real sinks.
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
        tp.append_event("usage.jsonl", {"event": "x"})
        assert not (tmp_path / "state" / "telemetry" / "usage.jsonl").exists()

    def test_non_serializable_record_fails_open_no_partial_line(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        tp.append_event("usage.jsonl", {"event": "ok"})
        tp.append_event("usage.jsonl", {"bad": {1, 2}})  # set → not JSON
        recs = _lines(tp.sink_path("usage.jsonl"))
        assert len(recs) == 1  # bad record dropped whole, sink intact

    def test_session_enriched_from_env_read_once_at_import(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        monkeypatch.setenv("HARNESS_SESSION_ID", "s-42")
        mod = importlib.reload(tp)
        try:
            monkeypatch.setattr(mod, "_actor_cache", None)
            mod.append_event("usage.jsonl", {"event": "x"})
            # session enrichment now shards the sink (P2): usage-s-42.jsonl
            recs = _lines(mod.sink_path("usage.jsonl", session="s-42"))
            assert recs[0]["session"] == "s-42"
        finally:
            os.environ.pop("HARNESS_SESSION_ID", None)
            importlib.reload(tp)


class TestRotation:
    def _inflate(self, p: Path):
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "wb") as fh:
            fh.seek(tp.MAX_SINK_BYTES + 1)
            fh.write(b"x")

    def test_oversize_sink_rotates_to_bak_then_fresh_file(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        p = tp.sink_path("usage.jsonl")
        self._inflate(p)
        tp.append_event("usage.jsonl", {"event": "after-rotate"})
        assert Path(str(p) + ".bak").exists()
        recs = _lines(p)
        assert len(recs) == 1 and recs[0]["event"] == "after-rotate"

    def test_one_generation_only_second_rotation_overwrites_bak(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        p = tp.sink_path("usage.jsonl")
        self._inflate(p)
        tp.append_event("usage.jsonl", {"event": "a"})
        self._inflate(p)
        tp.append_event("usage.jsonl", {"event": "b"})
        siblings = sorted(x.name for x in p.parent.iterdir())
        assert siblings == ["usage.jsonl", "usage.jsonl.bak"]  # no .bak.1 chain


class TestDedup:
    def test_same_key_logged_once(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        tp.append_event_once("usage.jsonl", {"event": "skill"}, "sess|hs:plan|m1")
        tp.append_event_once("usage.jsonl", {"event": "skill"}, "sess|hs:plan|m1")
        assert len(_lines(tp.sink_path("usage.jsonl"))) == 1

    def test_different_keys_both_logged(self, monkeypatch, tmp_path):
        _arm(monkeypatch, tmp_path)
        tp.append_event_once("usage.jsonl", {"event": "a"}, "k1")
        tp.append_event_once("usage.jsonl", {"event": "b"}, "k2")
        assert len(_lines(tp.sink_path("usage.jsonl"))) == 2


class TestSinkFollowsDataRoot:
    """Under a global install the telemetry sink must ride data_root() (the
    per-project .harness), not a shared bin path — so two projects never write
    the same usage ledger."""

    def _arm_no_state_env(self, monkeypatch):
        monkeypatch.delenv("HARNESS_STATE_DIR", raising=False)
        monkeypatch.delenv("HARNESS_ROOT", raising=False)
        monkeypatch.delenv("HARNESS_BIN_ROOT", raising=False)
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
        for ci in ("CI", "GITLAB_CI", "GITHUB_ACTIONS"):
            monkeypatch.delenv(ci, raising=False)
        monkeypatch.setenv("HARNESS_USER", "alice")
        monkeypatch.setattr(tp, "_actor_cache", None)

    def test_sink_under_data_root_env(self, monkeypatch, tmp_path):
        self._arm_no_state_env(monkeypatch)
        monkeypatch.setenv("HARNESS_DATA_ROOT", str(tmp_path / "d"))
        tp.append_event("usage.jsonl", {"event": "x"})
        assert (tmp_path / "d" / "state" / "telemetry" / "usage.jsonl").exists()

    def test_sink_under_claude_project_dir(self, monkeypatch, tmp_path):
        self._arm_no_state_env(monkeypatch)
        monkeypatch.delenv("HARNESS_DATA_ROOT", raising=False)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
        tp.append_event("usage.jsonl", {"event": "x"})
        sink = tmp_path / "proj" / ".harness" / "state" / "telemetry" / "usage.jsonl"
        assert sink.exists()


class TestLowVolumeGate:

    def test_garbage_count_is_conservatively_gated(self):
        assert tp.low_volume_gate("not-a-number") is True

def test_percentile_nearest_rank_empty_single_and_unsorted():
    assert tp.percentile([], 50) is None
    assert tp.percentile([42], 95) == 42
    vals = [10, 20, 30, 40, 50]
    assert tp.percentile(vals, 0) == 10
    assert tp.percentile(vals, 50) == 30
    assert tp.percentile(vals, 90) == 50
    assert tp.percentile([50, 10, 40, 20, 30], 50) == 30  # sorted internally
