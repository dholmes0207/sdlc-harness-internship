"""test_lens_reliability.py — the reliability lens: per-harness-script run count,
failure rate, and duration percentiles from hook-telemetry.jsonl (the records
track_script_execution writes). Read-only, advisory, low-volume gated.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lens_reliability as lens  # noqa: E402


def _seed(tmp_path, rows):
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    lines = []
    for rec in rows:
        rec = dict(rec)
        rec.setdefault("source", "hook:bash")
        rec.setdefault("ts", (now - timedelta(days=rec.pop("age_days", 0))).isoformat())
        lines.append(json.dumps(rec))
    (tel / "hook-telemetry.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_per_script_runs_and_failure_rate(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [
        {"script": "scripts/a.py", "exit": 0, "ms": 100, "session": "s1"},
        {"script": "scripts/a.py", "exit": 1, "ms": 200, "session": "s1"},
        {"script": "scripts/a.py", "exit": 0, "ms": 300, "session": "s2"},
        {"script": "scripts/b.py", "exit": 0, "ms": 50, "session": "s1"},
    ])
    agg = lens.gather(days=30, top=10)
    by = {s["script"]: s for s in agg["scripts"]}
    assert by["scripts/a.py"]["runs"] == 3
    assert by["scripts/a.py"]["failures"] == 1
    assert abs(by["scripts/a.py"]["fail_rate"] - (1 / 3)) < 1e-6
    assert by["scripts/b.py"]["failures"] == 0
    assert agg["total_runs"] == 4


def test_duration_percentiles_present_when_ms_recorded(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [{"script": "scripts/a.py", "exit": 0, "ms": m, "session": "s1"}
                     for m in (100, 200, 300, 400)])
    a = {s["script"]: s for s in lens.gather(days=30, top=10)["scripts"]}["scripts/a.py"]
    assert a["p50_ms"] is not None and a["p95_ms"] is not None
    assert a["p50_ms"] <= a["p95_ms"]


def test_missing_ms_degrades_to_none(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [{"script": "scripts/a.py", "exit": 0, "session": "s1"}])  # no ms
    a = {s["script"]: s for s in lens.gather(days=30, top=10)["scripts"]}["scripts/a.py"]
    assert a["p50_ms"] is None and a["p95_ms"] is None
    assert a["runs"] == 1


def test_old_records_excluded(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [{"script": "scripts/a.py", "exit": 0, "age_days": 90, "session": "s1"}])
    assert lens.gather(days=30, top=10)["total_runs"] == 0
