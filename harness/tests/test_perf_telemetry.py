"""test_perf_telemetry.py — load/perf is TELEMETRY, never a hard gate.

A load test is a measurement, not a pass/fail: read k6/JMeter JSON into
{p50, p95, error_rate, throughput}, append to the perf channel, and on a p95
regression vs the previous baseline emit an ADVISORY trace — never an exit-2
block. The invariant under test: perf_regression returns an advisory verdict and
the gate path never raises on it.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import perf_telemetry as pt  # noqa: E402

_K6 = json.dumps({
    "metrics": {
        "http_req_duration": {"values": {"med": 40.0, "p(95)": 120.0}},
        "http_req_failed": {"values": {"rate": 0.01}},
        "http_reqs": {"values": {"rate": 850.0}},
    }
})


def _w(p, body):
    p.write_text(body, encoding="utf-8")
    return p


def test_read_k6_metrics(tmp_path):
    m = pt.read_k6(_w(tmp_path / "k6.json", _K6))
    assert abs(m["p95"] - 120.0) < 1e-9
    assert abs(m["p50"] - 40.0) < 1e-9
    assert abs(m["error_rate"] - 0.01) < 1e-9
    assert abs(m["throughput"] - 850.0) < 1e-9


def test_p95_regression_is_advisory_not_block(tmp_path):
    cur = {"p95": 200.0}
    base = {"p95": 100.0}  # p95 doubled — a regression
    verdict = pt.perf_regression(cur, base, threshold_pct=20)
    assert verdict["regressed"] is True
    # the key invariant: this is advisory, NOT a gate block.
    assert verdict["enforcement"] == "advisory"
    assert verdict.get("block") in (None, False)


def test_within_noise_band_is_not_flagged(tmp_path):
    cur = {"p95": 105.0}
    base = {"p95": 100.0}  # +5% — inside the 20% band
    verdict = pt.perf_regression(cur, base, threshold_pct=20)
    assert verdict["regressed"] is False


def test_emit_regression_trace_writes_advisory(monkeypatch):
    # a real p95 regression appends a `perf_regression` advisory trace…
    _HOOKS = Path(__file__).resolve().parent.parent / "hooks"
    if str(_HOOKS) not in sys.path:
        sys.path.insert(0, str(_HOOKS))
    import trace_log
    calls = []
    monkeypatch.setattr(trace_log, "append_event",
                        lambda *a, **k: calls.append((a, k)))
    v = pt.emit_regression_trace({"p95": 200.0}, {"p95": 100.0}, label="login")
    assert v["regressed"] is True and v["enforcement"] == "advisory"
    assert calls and calls[0][0][1] == "perf_regression"
    # …but a within-band move writes no trace.
    calls.clear()
    v2 = pt.emit_regression_trace({"p95": 105.0}, {"p95": 100.0})
    assert v2["regressed"] is False and not calls


def test_emit_perf_metrics_appends_channel(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    pt.emit_perf_metrics({"p50": 40, "p95": 120, "error_rate": 0.01,
                          "throughput": 850}, label="login-load")
    sink = tmp_path / "telemetry" / "perf-metrics.jsonl"
    assert sink.is_file()
    rec = json.loads(sink.read_text().splitlines()[0])
    assert rec["p95"] == 120 and rec["label"] == "login-load"
    assert "actor" in rec and "ts" in rec
