"""test_verify_trace_chain_shards.py — verify partitions legacy vs per-session
shards and walks each as an INDEPENDENT chain (R3).

A shard `trace-YYYYMMDD-<disc>.jsonl` sorts BEFORE the same-day legacy
`trace-YYYYMMDD.jsonl` (ASCII '-'=45 < '.'=46). A single shared walk would carry
the shard's prev_chain into the legacy file and false-BREAK. verify() must
partition and keep prev_chain + seen_hashed separate per group.
"""
import json
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for p in (_HOOKS, _SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import trace_log  # noqa: E402
import verify_trace_chain as vtc  # noqa: E402


def _write_chain(path: Path, records):
    """Write a self-consistent genesis chain of records to `path`."""
    path.parent.mkdir(parents=True, exist_ok=True)
    prev = ""
    lines = []
    for rec in records:
        rec = dict(rec)
        rec["chain_hash"] = trace_log._chain_hash(prev, rec)
        prev = rec["chain_hash"]
        lines.append(json.dumps(rec, ensure_ascii=False))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _rec(ts, **kw):
    r = {"ts": ts, "actor": "user:x", "hook": "h", "event": "e"}
    r.update(kw)
    return r


class TestPartitionVerify:
    def test_t1_legacy_plus_two_shards_ok(self, tmp_path):
        d = tmp_path / "trace"
        _write_chain(d / "trace-20260710.jsonl",
                     [_rec("2026-07-10T01:00:00+00:00"), _rec("2026-07-10T02:00:00+00:00")])
        _write_chain(d / "trace-20260714-S1.jsonl",
                     [_rec("2026-07-14T01:00:00+00:00"), _rec("2026-07-14T02:00:00+00:00")])
        _write_chain(d / "trace-20260714-S2.jsonl",
                     [_rec("2026-07-14T03:00:00+00:00")])
        assert vtc.verify(d) == 0

    def test_t1b_same_day_legacy_plus_shard_no_false_break(self, tmp_path):
        # R3: same-day legacy + shard, shard sorts first — must NOT false-BREAK.
        d = tmp_path / "trace"
        _write_chain(d / "trace-20260714.jsonl",
                     [_rec("2026-07-14T05:00:00+00:00")])
        _write_chain(d / "trace-20260714-S1.jsonl",
                     [_rec("2026-07-14T01:00:00+00:00")])
        assert vtc.verify(d) == 0

    def test_t2_tampered_shard_isolated_break(self, tmp_path):
        d = tmp_path / "trace"
        _write_chain(d / "trace-20260714.jsonl", [_rec("2026-07-14T05:00:00+00:00")])
        _write_chain(d / "trace-20260714-S1.jsonl",
                     [_rec("2026-07-14T01:00:00+00:00"), _rec("2026-07-14T02:00:00+00:00")])
        # tamper the shard's 2nd record
        f = d / "trace-20260714-S1.jsonl"
        lines = f.read_text().splitlines()
        rec = json.loads(lines[1]); rec["note"] = "TAMPERED"
        lines[1] = json.dumps(rec, ensure_ascii=False)
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert vtc.verify(d) == 1  # BREAK in the shard, legacy untouched

    def test_real_live_shards_each_verify_independently(self, tmp_path):
        # Every live per-session shard in the real trace store is a well-formed
        # INDEPENDENT chain (proves the P2 shard writer + the partition walk on
        # real data). NOTE: verify --all on the whole live store can still BREAK
        # on a LEGACY file — a pre-existing checkpoint-gap artifact of concurrent
        # multi-session writing that predates this change (main's verifier breaks
        # identically on the same legacy-only files); that is out of scope here.
        import re
        tdir = _HARNESS / "state" / "trace"
        if not tdir.is_dir():
            import pytest
            pytest.skip("no real trace store")
        shards = [f for f in sorted(tdir.glob("trace-*.jsonl"))
                  if re.match(r"trace-\d{8}-.+\.jsonl$", f.name)]
        if not shards:
            import pytest
            pytest.skip("no live shards present")
        for sh in shards:
            iso = tmp_path / sh.stem
            iso.mkdir()
            (iso / sh.name).write_bytes(sh.read_bytes())
            assert vtc.verify(iso) == 0, "live shard broke: %s" % sh.name


class TestAnchorAndDateFilter:
    def test_anchor_emits_valid_date_when_day_is_shard_only(self, tmp_path):
        # Bug #1: with the newest day holding only shards, anchor() picked a shard
        # via files[-1] and emitted a garbage date "YYYYMMDD-<disc>". The date must
        # be a valid 8-char YYYYMMDD.
        import io, json as _json
        from contextlib import redirect_stdout
        d = tmp_path / "trace"
        _write_chain(d / "trace-20260714-a1b2c3.jsonl", [_rec("2026-07-14T01:00:00+00:00")])
        _write_chain(d / "trace-20260714-z9y8x7.jsonl", [_rec("2026-07-14T02:00:00+00:00")])
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = vtc.anchor(d)
        assert rc == 0
        out = _json.loads(buf.getvalue())
        assert out["date"] == "20260714", "anchor date must be a valid YYYYMMDD, got %r" % out["date"]

    def test_anchor_prefers_legacy_on_same_day(self, tmp_path):
        # The durable audit anchor is the legacy daily chain; a same-day shard
        # must not hijack the anchor target.
        import io, json as _json
        from contextlib import redirect_stdout
        d = tmp_path / "trace"
        _write_chain(d / "trace-20260714.jsonl", [_rec("2026-07-14T05:00:00+00:00", note="legacy")])
        _write_chain(d / "trace-20260714-s1.jsonl", [_rec("2026-07-14T01:00:00+00:00", note="shard")])
        buf = io.StringIO()
        with redirect_stdout(buf):
            vtc.anchor(d)
        out = _json.loads(buf.getvalue())
        assert out["date"] == "20260714"
        # the legacy file's head hash is the one anchored
        legacy_last = json.loads((d / "trace-20260714.jsonl").read_text().splitlines()[-1])
        assert out["final_hash"] == legacy_last["chain_hash"]

    def test_date_filter_includes_that_days_shards(self, tmp_path):
        # Bug #2: verify --date X only returned [trace-X.jsonl]; a day with only
        # shards then reported "No trace files" and exited 0 (false-clean). A
        # tampered shard for that date must be caught.
        d = tmp_path / "trace"
        _write_chain(d / "trace-20260714-s1.jsonl",
                     [_rec("2026-07-14T01:00:00+00:00"), _rec("2026-07-14T02:00:00+00:00")])
        # clean day-only-shard verifies
        assert vtc.verify(d, date_filter="20260714") == 0
        # tamper -> must BREAK, not false-clean
        f = d / "trace-20260714-s1.jsonl"
        lines = f.read_text().splitlines()
        rec = json.loads(lines[1]); rec["note"] = "TAMPERED"
        lines[1] = json.dumps(rec, ensure_ascii=False)
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        assert vtc.verify(d, date_filter="20260714") == 1


_HARNESS = Path(__file__).resolve().parent.parent
