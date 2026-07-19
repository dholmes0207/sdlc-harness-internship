"""Read-only gemini job lens (phase 9).

Aggregates the P4 job registry (harness/state/gemini/jobs.jsonl) by purpose+model:
counts, token totals, pass/degrade, latency percentiles (paired from the
running→terminal ts). Fail-soft: a torn JSONL line is skipped, never a crash
(usage lens, not a gate). Registry-wired so hs:insights surfaces it.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
import lens_gemini as lg  # noqa: E402


def _rec(job_id, purpose, model, status, ts, stats=None):
    r = {"job_id": job_id, "purpose": purpose, "model": model, "status": status, "ts": ts}
    if stats:
        r["stats"] = stats
    return r


# --- T1: group by purpose+model, token sums --------------------------------
def test_t1_group_and_token_sum():
    recs = [
        _rec("a", "review", "gemini-3.1-pro-preview", "done", "2026-07-06T10:00:01+00:00",
             {"input_tokens": 100, "output_tokens": 50}),
        _rec("b", "review", "gemini-3.1-pro-preview", "done", "2026-07-06T10:00:02+00:00",
             {"input_tokens": 200, "output_tokens": 20}),
        _rec("c", "research", "gemini-3.5-flash", "done", "2026-07-06T10:00:03+00:00",
             {"input_tokens": 30, "output_tokens": 10}),
    ]
    agg = lg._aggregate(recs)
    assert agg["total_jobs"] == 3
    groups = {(g["purpose"], g["model"]): g for g in agg["by_group"]}
    review = groups[("review", "gemini-3.1-pro-preview")]
    assert review["count"] == 2
    assert review["tokens"] == 370  # 150 + 220
    assert groups[("research", "gemini-3.5-flash")]["count"] == 1


# --- T1b: print-mode stats carry total_tokens (no output_tokens key) --------
def test_t1b_print_shape_total_tokens_counted():
    # The print _stats_of normalizes to {input_tokens,total_tokens,thoughts_tokens}
    # with NO output_tokens key; the lens must read total_tokens (dogfood: total is
    # the full accounting) instead of silently summing to 0.
    assert lg._tokens({"input_tokens": 16040, "total_tokens": 16135,
                       "thoughts_tokens": 94}) == 16135
    # legacy ACP/flat shape (no total_tokens) still sums input+output
    assert lg._tokens({"input_tokens": 100, "output_tokens": 50}) == 150
    # an explicit null total falls back to input+output, never crashes
    assert lg._tokens({"total_tokens": None, "input_tokens": 7, "output_tokens": 3}) == 10


# --- T2: degrade is visible (RT-07 signal) ---------------------------------
def test_t2_degrade_counted():
    recs = [
        _rec("a", "review", "m", "done", "2026-07-06T10:00:01+00:00"),
        _rec("b", "review", "m", "degraded", "2026-07-06T10:00:02+00:00"),
    ]
    agg = lg._aggregate(recs)
    assert agg["degrade_total"] == 1
    g = agg["by_group"][0]
    assert g["degraded"] == 1 and g["passed"] == 1


# --- T3: a broken JSONL line is skipped, never crashes ----------------------
def test_t3_bad_line_skipped(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    d = tmp_path / "gemini"
    d.mkdir()
    (d / "jobs.jsonl").write_text(
        json.dumps(_rec("a", "review", "m", "done", "2026-07-06T10:00:01+00:00")) + "\n"
        + "{ this is not json\n"
        + json.dumps(_rec("b", "review", "m", "done", "2026-07-06T10:00:02+00:00")) + "\n",
        encoding="utf-8")
    agg = lg.gather(days=3650)
    assert agg["total_jobs"] == 2  # bad line dropped, both good ones counted


# --- T4: registry wires the gemini lens ------------------------------------
def test_t4_registry_has_gemini():
    import analyze_telemetry as at
    assert "gemini" in at.LENS_REGISTRY
    assert "gemini" in at.OVERVIEW_ORDER


# --- T5: latency percentiles from running→terminal pairs --------------------
def test_t5_latency_percentiles():
    recs = []
    for i in range(10):
        jid = "j%d" % i
        recs.append(_rec(jid, "review", "m", "running", "2026-07-06T10:00:00+00:00"))
        # terminal at i+1 seconds → latency (i+1)*1000 ms
        recs.append(_rec(jid, "review", "m", "done",
                         "2026-07-06T10:00:%02d+00:00" % (i + 1)))
    agg = lg._aggregate(recs)
    g = agg["by_group"][0]
    assert g["count"] == 10
    assert g["latency_p50_ms"] is not None and g["latency_p50_ms"] > 0
    assert g["latency_p95_ms"] >= g["latency_p50_ms"]

def test_running_record_outside_window_excluded(tmp_path, monkeypatch):
    """A running record older than the day window must NOT be yielded — otherwise a
    stale start ts pairs against an in-window terminal and distorts latency."""
    import datetime as _dt
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    d = tmp_path / "gemini"
    d.mkdir()
    now = _dt.datetime.now(_dt.timezone.utc)
    old = (now - _dt.timedelta(days=40)).isoformat()
    recent = (now - _dt.timedelta(days=1)).isoformat()
    (d / "jobs.jsonl").write_text(
        json.dumps(_rec("j1", "review", "m", "running", old)) + "\n"
        + json.dumps(_rec("j1", "review", "m", "done", recent)) + "\n",
        encoding="utf-8")
    got = list(lg._iter_records(days=30))
    statuses = [r.get("status") for r in got]
    assert "running" not in statuses  # stale running dropped by the window
    assert "done" in statuses         # in-window terminal kept
