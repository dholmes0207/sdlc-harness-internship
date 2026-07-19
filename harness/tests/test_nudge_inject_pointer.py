"""test_nudge_inject_pointer.py — per-session observation pointer for the
nudge-inject re-surfacer.

The re-surfacer re-reads the audit trace at each UserPromptSubmit to find this
session's newest turn-end observation. The old code, on a tail-miss in the
current-day file, paid a FULL read of the whole (multi-week, multi-MB) file — 5+
times per prompt (once per observation family plus each relayed name). This
module replaces that with a per-session pointer: a small cache holding, per
family, the newest observation record already seen plus the byte offset scanned
up to. The next prompt seeks that offset and scans only the NEW bytes; a cache
hit supplies an observation that has since fallen out of the bounded tail window,
so the full read is never needed.

Contract under test:
  - busy day (obs out of the tail window) is served from the pointer cache with
    ZERO full-file read.
  - a newer observation appended after the pointer still supersedes it.
  - a missing/corrupt pointer degrades to the bounded tail (fail-open), never a
    full read and never a raise.
  - the single-shot-per-kind marker is unchanged: an already-surfaced obs is not
    re-injected.
"""
import json
import os
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import nudge_context_inject as nci  # noqa: E402


def _obs(session, note, ts, event="decision_capture_observation"):
    return {"ts": ts, "actor": "user:x", "session": session,
            "hook": "decision_capture_nudge", "event": event,
            "status": "observed", "note": note}


def _spy_read_text(monkeypatch):
    """Record every Path.read_text target so a test can assert no trace file was
    full-read. Returns the recording list."""
    calls = []
    orig = Path.read_text

    def spy(self, *a, **k):
        calls.append(str(self))
        return orig(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", spy)
    return calls


def _big_trace(trace_dir, head_records, pad_bytes, name="trace-20260101.jsonl"):
    """A trace file whose head_records sit BEFORE `pad_bytes` of filler, so the
    head is pushed out of the bounded tail window."""
    trace_dir.mkdir(parents=True, exist_ok=True)
    p = trace_dir / name
    lines = [json.dumps(r) for r in head_records]
    # filler lines that never match the session/event under test
    filler = json.dumps({"ts": "2020-01-01T00:00:00+00:00", "event": "noise",
                         "session": "OTHER", "actor": "x"})
    n = max(1, pad_bytes // (len(filler) + 1))
    lines.extend(filler for _ in range(n))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# --- pointer helpers ----------------------------------------------------------

class TestPointerHelpers:
    def test_roundtrip(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        entry = {"decision": {"file": "trace-20260101.jsonl", "offset": 4096,
                              "ts": "2026-06-18T03:00:00+00:00",
                              "obs": _obs("S1", "x — a", "2026-06-18T03:00:00+00:00")}}
        nci._write_pointer("S1", entry)
        got = nci._read_pointer("S1")
        assert got == entry

    def test_missing_pointer_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        assert nci._read_pointer("S1") == {}

    def test_corrupt_pointer_is_empty(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        p = nci._obs_pointer_path("S1")
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{not json", encoding="utf-8")
        assert nci._read_pointer("S1") == {}


# --- latest_observation(since_offset=) ---------------------------------------

class TestScanSinceOffset:
    def test_scans_only_forward_from_offset(self, tmp_path):
        trace = tmp_path / "trace"
        old = _obs("S1", "x — old", "2026-06-18T01:00:00+00:00")
        trace.mkdir(parents=True)
        p = trace / "trace-20260101.jsonl"
        p.write_text(json.dumps(old) + "\n", encoding="utf-8")
        off = p.stat().st_size
        new = _obs("S1", "x — new", "2026-06-18T05:00:00+00:00")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(new) + "\n")
        rec = nci.latest_observation("S1", trace, since_offset=off)
        assert rec is not None and rec["note"].endswith("new")

    def test_no_new_bytes_since_offset_is_none(self, tmp_path):
        trace = tmp_path / "trace"
        rec0 = _obs("S1", "x — only", "2026-06-18T01:00:00+00:00")
        trace.mkdir(parents=True)
        p = trace / "trace-20260101.jsonl"
        p.write_text(json.dumps(rec0) + "\n", encoding="utf-8")
        assert nci.latest_observation("S1", trace, since_offset=p.stat().st_size) is None


# --- core() with the pointer --------------------------------------------------

class TestCoreNoFullRead:
    def test_no_full_read_on_busy_day(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        trace = tmp_path / "trace"
        obs = _obs("S1", "unrecorded_decision×1 — beginning",
                   "2026-06-18T01:00:00+00:00")
        p = _big_trace(trace, [obs], pad_bytes=nci._TAIL_BYTES * 2)
        # pre-seed the pointer as if the obs was cached while it was still fresh;
        # the scan cursor is already at EOF, so no forward bytes remain.
        nci._write_pointer("S1", {"decision": {
            "file": p.name, "offset": p.stat().st_size,
            "ts": obs["ts"], "obs": obs}})
        calls = _spy_read_text(monkeypatch)
        text = nci.core({"session_id": "S1"})
        assert text is not None and "/hs:remember" in text and "beginning" in text
        assert not any("trace-" in c for c in calls), \
            "no trace file may be full-read via read_text"

    def test_pointer_miss_falls_back_bounded(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        trace = tmp_path / "trace"
        obs = _obs("S1", "unrecorded_decision×1 — buried",
                   "2026-06-18T01:00:00+00:00")
        _big_trace(trace, [obs], pad_bytes=nci._TAIL_BYTES * 2)
        # no pointer: the obs is out of the tail window -> a benign miss, but NO
        # full read and NO raise (fail-open telemetry contract).
        calls = _spy_read_text(monkeypatch)
        text = nci.core({"session_id": "S1"})
        assert text is None
        assert not any("trace-" in c for c in calls)

    def test_pointer_roundtrip_surfaces_newer(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        trace = tmp_path / "trace"
        trace.mkdir(parents=True)
        p = trace / "trace-20260101.jsonl"
        obs1 = _obs("S1", "unrecorded_decision×1 — first", "2026-06-18T01:00:00+00:00")
        p.write_text(json.dumps(obs1) + "\n", encoding="utf-8")
        # prompt 1: surfaces obs1 and writes the pointer
        t1 = nci.core({"session_id": "S1"})
        assert t1 is not None and "first" in t1
        assert nci._read_pointer("S1").get("decision", {}).get("ts") == obs1["ts"]
        # a newer obs appended -> prompt 2 supersedes the single-shot marker
        obs2 = _obs("S1", "unrecorded_decision×2 — first, second",
                    "2026-06-18T09:00:00+00:00")
        with p.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(obs2) + "\n")
        t2 = nci.core({"session_id": "S1"})
        assert t2 is not None and "second" in t2

    def test_no_trace_files_skips_family_scan(self, tmp_path, monkeypatch):
        # No trace dir at all -> no observation can exist. core() must return None
        # WITHOUT running the per-prompt family + generic relay scan (the cost this
        # early-exit guards). Spy on the generic relay name lookup: it must not run.
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))  # tmp/trace is absent
        import hook_runtime  # noqa: E402
        calls = []
        monkeypatch.setattr(hook_runtime, "nudge_observation_names",
                            lambda: calls.append(1) or [])
        assert nci.core({"session_id": "S1"}) is None
        assert calls == [], "generic relay scan ran despite no trace files"

    def test_marker_single_shot_preserved(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
        trace = tmp_path / "trace"
        trace.mkdir(parents=True)
        (trace / "trace-20260101.jsonl").write_text(
            json.dumps(_obs("S1", "unrecorded_decision×1 — once",
                            "2026-06-18T01:00:00+00:00")) + "\n",
            encoding="utf-8")
        first = nci.core({"session_id": "S1"})
        assert first is not None and "once" in first
        second = nci.core({"session_id": "S1"})
        assert second is None, "an already-surfaced obs must not re-inject"
