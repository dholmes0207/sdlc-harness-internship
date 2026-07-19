"""test_lens_gate.py — the gate lens over the trace (gate_pass/block/advisory/skip)."""
import json
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "hooks"))
import lens_gate as lg  # noqa: E402


def _seed(tmp_path, monkeypatch, recs, date=None):
    date = date or datetime.now(timezone.utc).strftime("%Y%m%d")
    td = tmp_path / "state" / "trace"
    td.mkdir(parents=True)
    (td / ("trace-%s.jsonl" % date)).write_text(
        "\n".join(json.dumps(r) for r in recs) + "\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))


def test_counts_pass_block_advisory_by_stage(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    _seed(tmp_path, monkeypatch, [
        {"event": "gate_pass", "target": "push", "hook": "gate_stage", "ts": now},
        {"event": "gate_block", "target": "pr", "hook": "gate_stage",
         "note": "missing verification", "ts": now},
        {"event": "gate_advisory", "target": "pr", "hook": "gate_stage",
         "note": "missing plan-approval", "ts": now},
        {"event": "gate_skip", "target": "push", "hook": "gate_stage", "ts": now},
        {"event": "unrelated", "ts": now},  # not a gate event → ignored
    ])
    agg = lg.gather(days=30)
    assert agg["total_events"] == 4
    assert agg["by_event"] == {"gate_pass": 1, "gate_block": 1,
                               "gate_advisory": 1, "gate_skip": 1}
    stages = {r["stage"]: r["count"] for r in agg["top_stages"]}
    assert stages["pr"] == 2 and stages["push"] == 2
    assert agg["top_advisory_reasons"][0]["reason"] == "missing plan-approval"


def test_non_string_event_is_skipped_not_crash(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc).isoformat()
    _seed(tmp_path, monkeypatch, [
        {"event": {"nested": "dict"}, "ts": now},          # unhashable → must not crash
        {"event": "gate_pass", "target": "push", "ts": now},
    ])
    agg = lg.gather(days=30)
    assert agg["total_events"] == 1


def test_days_filter_by_filename(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    td = tmp_path / "state" / "trace"
    td.mkdir(parents=True)
    (td / "trace-20000101.jsonl").write_text(
        json.dumps({"event": "gate_block", "target": "x", "ts": now.isoformat()}) + "\n",
        encoding="utf-8")
    (td / ("trace-%s.jsonl" % now.strftime("%Y%m%d"))).write_text(
        json.dumps({"event": "gate_pass", "target": "y", "ts": now.isoformat()}) + "\n",
        encoding="utf-8")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    agg = lg.gather(days=30)   # the 2000 file is filtered by its filename date
    assert agg["total_events"] == 1
    assert agg["by_event"].get("gate_pass") == 1


def test_missing_dir_failsoft(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    agg = lg.gather(days=30)
    assert agg["total_events"] == 0
