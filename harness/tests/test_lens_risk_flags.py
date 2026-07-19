"""test_lens_risk_flags.py — the risk-flags lens: surface guarded-config paths
that were written THROUGH THE SHELL (write_guard_bypass events that
bash_write_guard records), so a human can confirm each was intended. Read-only,
advisory — it counts + ranks, never judges or blocks. (cowork-logs harvest:
risk-flag scan, adapted to the harness's own telemetry.)
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lens_risk_flags as lens  # noqa: E402


def _seed(tmp_path, rows):
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    lines = []
    for rec in rows:
        rec = dict(rec)
        rec.setdefault("ts", (now - timedelta(days=rec.pop("age_days", 0))).isoformat())
        lines.append(json.dumps(rec))
    (tel / "hook-telemetry.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_aggregates_write_guard_bypass_by_target(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [
        {"event": "write_guard_bypass", "target": "harness/data/team.yaml", "session": "s1"},
        {"event": "write_guard_bypass", "target": "harness/data/team.yaml", "session": "s2"},
        {"event": "write_guard_bypass", "target": "harness/hooks/gate_stage.py", "session": "s1"},
        {"source": "hook:bash", "script": "scripts/x.py", "exit": 0, "session": "s1"},  # not a bypass
    ])
    agg = lens.gather(days=30, top=10)
    flags = {f["target"]: f for f in agg["flags"]}
    assert agg["total_bypasses"] == 3
    assert flags["harness/data/team.yaml"]["count"] == 2
    assert flags["harness/data/team.yaml"]["sessions"] == 2
    assert flags["harness/hooks/gate_stage.py"]["count"] == 1


def test_no_bypasses_is_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [{"source": "hook:bash", "script": "scripts/x.py", "exit": 0}])
    agg = lens.gather(days=30, top=10)
    assert agg["total_bypasses"] == 0
    assert agg["flags"] == []


def test_old_events_excluded_by_window(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [
        {"event": "write_guard_bypass", "target": "harness/data/team.yaml",
         "session": "s1", "age_days": 90},
    ])
    assert lens.gather(days=30, top=10)["total_bypasses"] == 0


def test_missing_sink_is_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state" / "telemetry").mkdir(parents=True)
    agg = lens.gather(days=30, top=10)
    assert agg["total_bypasses"] == 0 and agg["flags"] == []
