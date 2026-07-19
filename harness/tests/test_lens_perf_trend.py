"""test_lens_perf_trend.py — the read-only perf p95-trend lens now reads the LIVE
`ms` durations from hook-telemetry.jsonl (the old perf-metrics.jsonl sink was dead).
Per script it takes p95 and flags an early-half vs late-half regression. Advisory only."""
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
import lens_perf_trend as lens  # noqa: E402


def _seed(tmp_path, monkeypatch, rows):
    tele = tmp_path / "state" / "telemetry"
    tele.mkdir(parents=True)
    (tele / "hook-telemetry.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))


def _ms(script, ms):
    return {"ts": datetime.now(timezone.utc).isoformat(), "script": script,
            "ms": ms, "exit": 0}


def test_reads_ms_from_hook_telemetry(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _ms("a.py", 10), _ms("a.py", 20),
        {"ts": datetime.now(timezone.utc).isoformat(), "script": "b.py", "exit": 0},  # no ms
    ])
    agg = lens.gather(days=3650)
    assert agg["total_samples"] == 2
    labels = {r["label"] for r in agg["labels"]}
    assert "a.py" in labels and "b.py" not in labels


def test_flags_regression_early_vs_late(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _ms("slow.py", 10), _ms("slow.py", 12),
        _ms("slow.py", 900), _ms("slow.py", 1000)])
    agg = lens.gather(days=3650)
    row = next(r for r in agg["labels"] if r["label"] == "slow.py")
    assert row["samples"] == 4
    assert row["last_p95"] > row["first_p95"]
    assert row["regressed"] is True


def test_empty_window_renders(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    agg = lens.gather(days=30)
    assert agg["total_samples"] == 0
    assert "no perf samples" in lens.render(agg)

def test_orders_by_datetime_not_raw_ts_string(tmp_path, monkeypatch):
    """Samples must sort by parsed datetime, not the raw ts string: a +07:00 offset
    sorts LATER lexically than an earlier +00:00 wall-clock yet is EARLIER in real
    time. A string sort scrambles the early/late split and misses the regression."""
    rows = [
        {"ts": "2026-07-06T10:00:00+07:00", "script": "s.py", "ms": 10, "exit": 0},    # 03:00 UTC
        {"ts": "2026-07-06T05:00:00+00:00", "script": "s.py", "ms": 12, "exit": 0},    # 05:00 UTC
        {"ts": "2026-07-06T15:00:00+07:00", "script": "s.py", "ms": 900, "exit": 0},   # 08:00 UTC
        {"ts": "2026-07-06T09:00:00+00:00", "script": "s.py", "ms": 1000, "exit": 0},  # 09:00 UTC
    ]
    _seed(tmp_path, monkeypatch, rows)
    agg = lens.gather(days=3650)
    row = next(r for r in agg["labels"] if r["label"] == "s.py")
    assert row["samples"] == 4
    assert row["last_p95"] > row["first_p95"]
    assert row["regressed"] is True
