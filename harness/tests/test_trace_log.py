"""test_trace_log.py — the audit trace store (append-only, daily files,
NO rotation — trace is the audit ledger, only telemetry counters
rotate). Schema learned from CK hook-logger (written new, not copied).
"""
import importlib
import json
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(_HOOKS))


def _fresh(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("HARNESS_USER", "tester")
    monkeypatch.delenv("HARNESS_AGENT", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("GITLAB_CI", raising=False)
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    for m in ("trace_log", "hook_runtime"):
        sys.modules.pop(m, None)
    import trace_log
    importlib.reload(trace_log)
    return trace_log


def _trace_files(tmp_path):
    d = tmp_path / "state" / "trace"
    return sorted(d.glob("trace-*.jsonl")) if d.exists() else []


def _records(tmp_path):
    out = []
    for f in _trace_files(tmp_path):
        out.extend(json.loads(l) for l in f.read_text().splitlines() if l.strip())
    return out


class TestAppendEvent:
    def test_basic_record_schema(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="gate_stage", event="gate_block",
                        tool="Bash", target="git push",
                        status="blocked", exit_code=2,
                        note="missing verification")
        recs = _records(tmp_path)
        assert len(recs) == 1
        r = recs[0]
        # schema: ts/actor/session/hook/event + optional tool/target/status/...
        assert r["event"] == "gate_block"
        assert r["hook"] == "gate_stage"
        assert r["actor"] == "user:tester"
        assert r["ts"]  # ISO ts present
        assert r["tool"] == "Bash"
        assert r["status"] == "blocked"
        assert r["exit"] == 2
        assert r["note"] == "missing verification"

    def test_daily_file_naming(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="session_start")
        files = _trace_files(tmp_path)
        assert len(files) == 1
        import re
        assert re.fullmatch(r"trace-\d{8}\.jsonl", files[0].name)

    def test_append_only_two_events_two_lines(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="e1")
        tl.append_event(hook="h", event="e2")
        assert [r["event"] for r in _records(tmp_path)] == ["e1", "e2"]

    def test_no_rotation_ever(self, tmp_path, monkeypatch):
        # audit trace must NOT truncate-rotate, however big.
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="seed")
        f = _trace_files(tmp_path)[0]
        # Inflate the file way past any plausible rotation cap.
        with open(f, "a", encoding="utf-8") as fh:
            fh.write("x" * (9 * 1024 * 1024) + "\n")
        size_before = f.stat().st_size
        tl.append_event(hook="h", event="after-big")
        assert f.stat().st_size > size_before          # still appending
        assert not (f.parent / (f.name + ".1")).exists()  # no rotation artifact

    def test_payload_hash_when_tool_input_given(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="g", event="gate_pass",
                        tool_input={"command": "git push"})
        r = _records(tmp_path)[0]
        assert len(r["payload_hash"]) == 12
        # deterministic: same input → same hash
        tl.append_event(hook="g", event="gate_pass",
                        tool_input={"command": "git push"})
        assert _records(tmp_path)[1]["payload_hash"] == r["payload_hash"]

    def test_unserializable_payload_still_records_audit_line(self, tmp_path, monkeypatch):
        # A non-JSON-serializable tool_input must drop ONLY the payload_hash field,
        # never the whole audit record — losing the audit line on a hashing failure
        # would silently erase the very event the ledger exists to witness.
        tl = _fresh(monkeypatch, tmp_path)

        class _Unhashable:  # json.dumps cannot serialize a bare object
            pass

        tl.append_event(hook="gate_stage", event="gate_block", tool="Bash",
                        status="blocked", tool_input={"obj": _Unhashable()})
        recs = _records(tmp_path)
        assert len(recs) == 1                    # audit line survives
        assert recs[0]["event"] == "gate_block"
        assert recs[0]["status"] == "blocked"
        assert "payload_hash" not in recs[0]     # only the hash field is dropped

    def test_fail_open_when_dir_unwritable(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        blocker = tmp_path / "blocked"
        blocker.write_text("file-not-dir")
        monkeypatch.setenv("HARNESS_STATE_DIR", str(blocker / "state"))
        tl.append_event(hook="h", event="e")  # must not raise

    def test_actor_override_param(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="e", actor="ci")
        assert _records(tmp_path)[0]["actor"] == "ci"

    def test_session_recorded(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="e", session="s42")
        assert _records(tmp_path)[0]["session"] == "s42"


def test_ts_and_filename_derive_from_one_instant(tmp_path, monkeypatch):
    """ts and the daily filename must come from a SINGLE now() — two separate
    now() calls can straddle UTC midnight, filing a record under a date that
    disagrees with its own ts (audit-ledger integrity)."""
    from datetime import datetime, timezone
    tl = _fresh(monkeypatch, tmp_path)
    t1 = datetime(2026, 6, 20, 23, 59, 59, 900000, tzinfo=timezone.utc)
    t2 = datetime(2026, 6, 21, 0, 0, 0, 0, tzinfo=timezone.utc)  # next day

    class _Seq:
        seq = [t1, t2]
        i = [0]

        @classmethod
        def now(cls, tz=None):
            v = cls.seq[min(cls.i[0], len(cls.seq) - 1)]
            cls.i[0] += 1
            return v

    monkeypatch.setattr(tl, "datetime", _Seq)
    tl.append_event(hook="h", event="e")

    files = _trace_files(tmp_path)
    recs = _records(tmp_path)
    assert len(files) == 1 and len(recs) == 1
    ts_date = recs[0]["ts"][:10].replace("-", "")
    fname_date = files[0].name[len("trace-"):len("trace-") + 8]
    assert ts_date == fname_date, \
        "ts %s vs filename %s — two now() calls split at midnight" % (ts_date, fname_date)
# ---------------------------------------------------------------------------
# Hash-chain tests (Phase 1)
# ---------------------------------------------------------------------------

import hashlib as _hashlib


def _expected_chain(prev, record):
    """Compute expected chain_hash for a record given the previous chain_hash."""
    import json as _json
    rec_no_chain = {k: v for k, v in record.items() if k != "chain_hash"}
    canonical = _json.dumps(
        rec_no_chain, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return _hashlib.sha256(
        ((prev or "") + canonical).encode("utf-8")
    ).hexdigest()


class TestHashChain:
    def test_chain_hash_links_records(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="e1")
        tl.append_event(hook="h", event="e2")
        recs = _records(tmp_path)
        assert len(recs) == 2
        r0, r1 = recs
        assert "chain_hash" in r0
        assert "chain_hash" in r1
        expected = _expected_chain(r0["chain_hash"], r1)
        assert r1["chain_hash"] == expected, "record[1].chain_hash must chain from record[0]"

    def test_genesis_record_chains_from_empty(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="genesis")
        recs = _records(tmp_path)
        r = recs[0]
        assert "chain_hash" in r
        expected = _expected_chain("", r)
        assert r["chain_hash"] == expected

    def test_chain_detects_record_modification(self, tmp_path, monkeypatch):
        import json as _json
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="e0")
        tl.append_event(hook="h", event="e1")
        tl.append_event(hook="h", event="e2")
        files = _trace_files(tmp_path)
        lines = files[0].read_text().splitlines()
        r0 = _json.loads(lines[0])
        r1_original = _json.loads(lines[1])
        # Tamper r1: change the event field but keep the stored chain_hash
        r1_tampered = dict(r1_original)
        r1_tampered["event"] = "TAMPERED"
        # After tamper: recomputing chain from r0.chain_hash + canonical(r1_tampered)
        # must NOT equal r1.chain_hash (which was computed from original r1)
        recalc_r1 = _expected_chain(r0["chain_hash"], r1_tampered)
        assert recalc_r1 != r1_original["chain_hash"], \
            "tampered record: recomputed chain must not match stored chain_hash"

    def test_chain_deterministic_excludes_self(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="det")
        recs = _records(tmp_path)
        r = recs[0]
        stored = r["chain_hash"]
        r_with_fake = dict(r)
        r_with_fake["chain_hash"] = "ff" * 32
        recomputed = _expected_chain("", r_with_fake)
        assert recomputed == stored, "chain_hash must be excluded from canonical before hashing"

    def test_append_still_fail_open_on_lock_or_io_error(self, tmp_path, monkeypatch):
        import unittest.mock as _mock
        _fresh(monkeypatch, tmp_path)
        fcntl_mock = _mock.MagicMock()
        fcntl_mock.flock = _mock.MagicMock(side_effect=OSError("lock fail"))
        with _mock.patch.dict("sys.modules", {"fcntl": fcntl_mock}):
            for m in ("trace_log", "hook_runtime"):
                sys.modules.pop(m, None)
            import trace_log as tl2
            tl2.append_event(hook="h", event="failopen")
        recs = _records(tmp_path)
        assert len(recs) == 1
        assert recs[0]["event"] == "failopen"


    def test_concurrent_appends_no_break(self, tmp_path, monkeypatch):
        import platform
        import threading
        if platform.system() == "Windows":
            pytest.skip("flock not available on Windows")
        tl = _fresh(monkeypatch, tmp_path)
        errors = []

        def worker(i):
            try:
                tl.append_event(hook="h", event="concurrent_%d" % i)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, "concurrent appends must not raise"
        recs = _records(tmp_path)
        assert len(recs) == 8
        for r in recs:
            if "chain_hash" not in r:
                continue
            assert len(r["chain_hash"]) == 64
            assert all(c in "0123456789abcdef" for c in r["chain_hash"])

    def test_post_cutover_missing_chain_is_break_property(self, tmp_path, monkeypatch):
        tl = _fresh(monkeypatch, tmp_path)
        tl.append_event(hook="h", event="post_cutover")
        r = _records(tmp_path)[0]
        assert "chain_hash" in r, "every record written after chain is introduced must carry chain_hash"
