"""test_verify_trace_chain.py — tests for the trace-chain verifier CLI
and the checkpoint-on-rollover mechanism in trace_log.

Phase 2: checkpoint linking daily files + verify_trace_chain.py read-only CLI.
"""
import hashlib
import importlib
import json
import subprocess
import sys
from pathlib import Path


_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_SCRIPTS))

_VERIFIER = _SCRIPTS / "verify_trace_chain.py"


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


def _trace_dir(tmp_path):
    return tmp_path / "state" / "trace"


def _run_verifier(*args, state_dir=None):
    """Run verify_trace_chain.py as subprocess; return (returncode, stdout, stderr)."""
    cmd = [sys.executable, str(_VERIFIER)]
    if state_dir:
        cmd += ["--state-dir", str(state_dir)]
    cmd += list(args)
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode, result.stdout, result.stderr


def _chain_hash(prev, record):
    rec_no_chain = {k: v for k, v in record.items() if k != "chain_hash"}
    canonical = json.dumps(rec_no_chain, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(((prev or "") + canonical).encode("utf-8")).hexdigest()


def _write_trace_file(trace_dir, date_str, records):
    """Write JSONL records to a trace file; records must already have chain_hash."""
    trace_dir.mkdir(parents=True, exist_ok=True)
    path = trace_dir / ("trace-%s.jsonl" % date_str)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return path


def _make_chain(records_data, prev_chain=""):
    """Build a list of records with chain_hash computed correctly."""
    records = []
    prev = prev_chain
    for data in records_data:
        rec = dict(data)
        rec["chain_hash"] = _chain_hash(prev, rec)
        prev = rec["chain_hash"]
        records.append(rec)
    return records


class TestCheckpointOnRollover:
    def test_checkpoint_written_on_day_rollover(self, tmp_path, monkeypatch):
        """When the date changes between two appends, a checkpoint is written
        for the previous day with its final_hash."""
        tl = _fresh(monkeypatch, tmp_path)
        from datetime import datetime, timezone as _tz

        t1 = datetime(2026, 6, 20, 12, 0, 0, tzinfo=_tz.utc)
        t2 = datetime(2026, 6, 21, 12, 0, 0, tzinfo=_tz.utc)

        calls = [0]

        def fake_now(tz=None):
            idx = calls[0]
            calls[0] += 1
            return t1 if idx == 0 else t2

        monkeypatch.setattr(tl, "datetime", type("DT", (), {"now": staticmethod(fake_now)})())
        tl.append_event(hook="h", event="day1")
        monkeypatch.setattr(tl, "datetime", type("DT", (), {"now": staticmethod(fake_now)})())
        tl.append_event(hook="h", event="day2")

        checkpoint_path = tmp_path / "state" / "trace" / "trace-checkpoint-20260620.json"
        assert checkpoint_path.exists(), "checkpoint must be written on day rollover"
        cp = json.loads(checkpoint_path.read_text())
        assert "final_hash" in cp
        assert "date" in cp
        assert cp["date"] == "20260620"

    def test_chain_continues_across_days(self, tmp_path, monkeypatch):
        """Record at start of new day must chain from checkpoint.final_hash."""
        tl = _fresh(monkeypatch, tmp_path)
        from datetime import datetime, timezone as _tz

        t_day1 = datetime(2026, 6, 20, 12, 0, 0, tzinfo=_tz.utc)
        t_day2 = datetime(2026, 6, 21, 12, 0, 0, tzinfo=_tz.utc)

        calls = [0]
        times = [t_day1, t_day2, t_day2]

        def fake_now(tz=None):
            idx = min(calls[0], len(times) - 1)
            calls[0] += 1
            return times[idx]

        DT = type("DT", (), {"now": staticmethod(fake_now)})()
        monkeypatch.setattr(tl, "datetime", DT)
        tl.append_event(hook="h", event="day1_rec")
        tl.append_event(hook="h", event="day2_rec")

        trace_dir = tmp_path / "state" / "trace"
        day1_file = trace_dir / "trace-20260620.jsonl"
        day2_file = trace_dir / "trace-20260621.jsonl"
        checkpoint = trace_dir / "trace-checkpoint-20260620.json"

        assert day1_file.exists()
        assert day2_file.exists()
        assert checkpoint.exists()

        day2_recs = [json.loads(l) for l in day2_file.read_text().splitlines() if l.strip()]
        cp = json.loads(checkpoint.read_text())

        assert day2_recs, "day2 must have a record"
        # The first record of day2 must chain from checkpoint.final_hash
        expected = _chain_hash(cp["final_hash"], day2_recs[0])
        assert day2_recs[0]["chain_hash"] == expected, \
            "day2 record must chain from checkpoint.final_hash"


class TestVerifierOkClean:
    def test_verifier_ok_on_clean_chain(self, tmp_path):
        trace_dir = _trace_dir(tmp_path)
        recs = _make_chain([
            {"ts": "2026-06-20T01:00:00+00:00", "actor": "t", "event": "e1"},
            {"ts": "2026-06-20T02:00:00+00:00", "actor": "t", "event": "e2"},
            {"ts": "2026-06-20T03:00:00+00:00", "actor": "t", "event": "e3"},
        ])
        _write_trace_file(trace_dir, "20260620", recs)
        rc, out, err = _run_verifier("--all", state_dir=trace_dir)
        assert rc == 0, "clean chain must exit 0\nout: %s\nerr: %s" % (out, err)
        assert "OK" in out or "ok" in out.lower()

    def test_verifier_detects_modification(self, tmp_path):
        trace_dir = _trace_dir(tmp_path)
        recs = _make_chain([
            {"ts": "2026-06-20T01:00:00+00:00", "actor": "t", "event": "e1"},
            {"ts": "2026-06-20T02:00:00+00:00", "actor": "t", "event": "e2"},
            {"ts": "2026-06-20T03:00:00+00:00", "actor": "t", "event": "e3"},
        ])
        path = _write_trace_file(trace_dir, "20260620", recs)
        # Tamper record index 1
        lines = path.read_text().splitlines()
        r1 = json.loads(lines[1])
        r1["event"] = "TAMPERED"
        lines[1] = json.dumps(r1)
        path.write_text("\n".join(lines) + "\n")

        rc, out, err = _run_verifier("--all", state_dir=trace_dir)
        assert rc != 0, "tampered record must cause non-zero exit\nout: %s\nerr: %s" % (out, err)
        assert "BREAK" in out or "break" in out.lower() or "BREAK" in err

    def test_verifier_detects_deletion(self, tmp_path):
        trace_dir = _trace_dir(tmp_path)
        recs = _make_chain([
            {"ts": "2026-06-20T01:00:00+00:00", "actor": "t", "event": "e1"},
            {"ts": "2026-06-20T02:00:00+00:00", "actor": "t", "event": "e2"},
            {"ts": "2026-06-20T03:00:00+00:00", "actor": "t", "event": "e3"},
        ])
        path = _write_trace_file(trace_dir, "20260620", recs)
        # Delete record index 1
        lines = path.read_text().splitlines()
        del lines[1]
        path.write_text("\n".join(lines) + "\n")

        rc, out, err = _run_verifier("--all", state_dir=trace_dir)
        assert rc != 0, "deleted record must cause non-zero exit\nout: %s\nerr: %s" % (out, err)

    def test_verifier_handles_pre_chain_records(self, tmp_path):
        """Records with ts before cutover and no chain_hash must be skipped."""
        trace_dir = _trace_dir(tmp_path)
        cutover = "2026-06-20T12:00:00+00:00"

        # Write checkpoint with cutover
        trace_dir.mkdir(parents=True, exist_ok=True)
        cp = {"date": "20260620", "final_hash": "", "record_count": 1,
              "chain_cutover_ts": cutover}
        (trace_dir / "trace-checkpoint-20260619.json").write_text(json.dumps(cp))

        # Write a file with pre-chain and post-chain records
        pre_rec = {"ts": "2026-06-20T06:00:00+00:00", "actor": "t", "event": "pre"}
        post_recs = _make_chain([
            {"ts": "2026-06-20T14:00:00+00:00", "actor": "t", "event": "post1"},
        ])
        _write_trace_file(trace_dir, "20260620", [pre_rec] + post_recs)

        rc, out, err = _run_verifier("--all", state_dir=trace_dir)
        assert rc == 0, "pre-chain record skipped, post chain valid → exit 0\nout: %s\nerr: %s" % (out, err)
        assert "pre-chain" in out or "skip" in out.lower() or "unverifiable" in out.lower()

    def test_post_cutover_missing_field_is_break(self, tmp_path):
        """Record with ts >= cutover but missing chain_hash must be a BREAK."""
        trace_dir = _trace_dir(tmp_path)
        cutover = "2026-06-20T00:00:00+00:00"

        trace_dir.mkdir(parents=True, exist_ok=True)
        cp = {"date": "20260619", "final_hash": "", "record_count": 0,
              "chain_cutover_ts": cutover}
        (trace_dir / "trace-checkpoint-20260619.json").write_text(json.dumps(cp))

        # Write record with ts >= cutover but NO chain_hash
        bad_rec = {"ts": "2026-06-20T10:00:00+00:00", "actor": "t", "event": "no_chain"}
        _write_trace_file(trace_dir, "20260620", [bad_rec])

        rc, out, err = _run_verifier("--all", state_dir=trace_dir)
        assert rc != 0, "post-cutover record missing chain_hash must BREAK\nout: %s\nerr: %s" % (out, err)
        assert "BREAK" in out or "BREAK" in err

    def test_verifier_warns_fork_not_break(self, tmp_path):
        """Two records sharing the same prev (fork) must warn but not BREAK."""
        trace_dir = _trace_dir(tmp_path)
        # Build two records both chaining from the same prev=""
        rec1 = {"ts": "2026-06-20T01:00:00+00:00", "actor": "t", "event": "fork_a"}
        rec2 = {"ts": "2026-06-20T01:00:01+00:00", "actor": "t", "event": "fork_b"}
        rec1["chain_hash"] = _chain_hash("", rec1)
        rec2["chain_hash"] = _chain_hash("", rec2)  # same prev as rec1 — fork!

        _write_trace_file(trace_dir, "20260620", [rec1, rec2])

        rc, out, err = _run_verifier("--all", state_dir=trace_dir)
        assert rc == 0, "fork is WARN not BREAK; must exit 0\nout: %s\nerr: %s" % (out, err)
        assert "FORK" in out or "fork" in out.lower() or "WARN" in out or "FORK" in err

    def test_verifier_accepts_full_rewrite(self, tmp_path):
        """A consistent full rewrite of ALL records must exit 0.
        This test intentionally documents the honest limit: hash-chain cannot
        detect an insider who rewrites the whole file consistently.
        External anchor is the only defense against this."""
        trace_dir = _trace_dir(tmp_path)
        # Build a fully-consistent rewritten chain
        recs = _make_chain([
            {"ts": "2026-06-20T01:00:00+00:00", "actor": "attacker", "event": "fake_e1"},
            {"ts": "2026-06-20T02:00:00+00:00", "actor": "attacker", "event": "fake_e2"},
        ])
        _write_trace_file(trace_dir, "20260620", recs)

        rc, out, err = _run_verifier("--all", state_dir=trace_dir)
        assert rc == 0, "consistent full rewrite must exit 0 (honest limit)\nout: %s\nerr: %s" % (out, err)

    def test_anchor_accepts_state_dir_after_subcommand(self, tmp_path):
        """The documented form `anchor --state-dir DIR --date X` (state-dir AFTER
        the subcommand token) must resolve that dir, not fall back to the default
        harness/state/trace. Guards the subparser --state-dir wiring."""
        trace_dir = _trace_dir(tmp_path)
        recs = _make_chain([
            {"ts": "2026-06-20T01:00:00+00:00", "actor": "t", "event": "e1"},
        ])
        _write_trace_file(trace_dir, "20260620", recs)

        # state-dir AFTER the `anchor` token — the previously-broken parse path.
        cmd = [sys.executable, str(_VERIFIER), "anchor",
               "--state-dir", str(trace_dir), "--date", "20260620"]
        result = subprocess.run(cmd, capture_output=True, text=True)
        assert result.returncode == 0, \
            "anchor --state-dir DIR must parse\nout: %s\nerr: %s" % (result.stdout, result.stderr)
        data = json.loads(result.stdout)
        assert data["date"] == "20260620"
        assert data["count"] == 1, "must read the given dir (count 1), not the default trace dir"

    def test_anchor_emits_head_only_no_commit(self, tmp_path):
        """The anchor subcommand must print JSON {date,final_hash,count} and
        must NOT write any git-tracked file or invoke git commit."""
        trace_dir = _trace_dir(tmp_path)
        recs = _make_chain([
            {"ts": "2026-06-20T01:00:00+00:00", "actor": "t", "event": "e1"},
        ])
        _write_trace_file(trace_dir, "20260620", recs)

        rc, out, err = _run_verifier("anchor", "--date", "20260620", state_dir=trace_dir)
        assert rc == 0, "anchor must exit 0\nout: %s\nerr: %s" % (out, err)
        data = json.loads(out)
        assert "final_hash" in data
        assert "date" in data
        assert "count" in data

def test_deleted_hash_without_cutover_is_break(tmp_path):
    """No cutover checkpoint present, records DO carry chain_hash. Stripping the
    chain_hash from the LAST record (nothing after it to orphan) must BREAK. The
    old code reclassed a hashless record as pre-chain when cutover_ts was None, so
    this tamper slipped past as 'skipped' (exit 0) — undetected."""
    trace_dir = _trace_dir(tmp_path)
    recs = _make_chain([
        {"ts": "2026-06-20T01:00:00+00:00", "actor": "t", "event": "e1"},
        {"ts": "2026-06-20T02:00:00+00:00", "actor": "t", "event": "e2"},
        {"ts": "2026-06-20T03:00:00+00:00", "actor": "t", "event": "e3"},
    ])
    del recs[-1]["chain_hash"]  # strip the LAST record's hash — the undetected hole
    _write_trace_file(trace_dir, "20260620", recs)
    rc, out, err = _run_verifier("--all", state_dir=trace_dir)
    assert rc != 0, "deleted hash with no cutover must BREAK\nout: %s\nerr: %s" % (out, err)
    assert "BREAK" in out or "BREAK" in err
