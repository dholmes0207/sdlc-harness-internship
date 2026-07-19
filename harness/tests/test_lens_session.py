"""test_lens_session.py — the session lens over sessions.jsonl."""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import lens_session as ls  # noqa: E402


def _seed(tmp_path, monkeypatch, rows):
    tele = tmp_path / "state" / "telemetry"
    tele.mkdir(parents=True)
    (tele / "sessions.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))


def _row(ts, dur, tools, files=0, subagents=0, actor="user:a"):
    return {"ts": ts, "session": ts, "duration_s": dur, "tools": tools,
            "files_modified": files, "subagents": subagents, "actor": actor}


def test_gathers_percentiles_and_tool_mix(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _row("2026-06-25T00:00:00+00:00", 100, {"Bash": 3, "Edit": 1}, files=2, subagents=1),
        _row("2026-06-25T01:00:00+00:00", 300, {"Bash": 2}, files=1),
        _row("2026-06-25T02:00:00+00:00", 200, {"Read": 5}, subagents=2),
    ])
    agg = ls.gather(days=3650)
    assert agg["sessions"] == 3
    assert agg["duration_p50_s"] == 200          # nearest-rank of [100,200,300]
    assert agg["files_modified_total"] == 3
    assert agg["subagents_total"] == 3
    tools = {t["tool"]: t["count"] for t in agg["top_tools"]}
    assert tools["Bash"] == 5 and tools["Read"] == 5 and tools["Edit"] == 1


def test_missing_file_failsoft_empty(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    agg = ls.gather(days=30)
    assert agg["sessions"] == 0 and agg["top_tools"] == []


def test_days_filter(tmp_path, monkeypatch):
    _seed(tmp_path, monkeypatch, [
        _row("2000-01-01T00:00:00+00:00", 100, {"Bash": 1}),   # ancient
        _row("2026-06-25T00:00:00+00:00", 100, {"Bash": 1}),   # recent (within a big window)
    ])
    agg = ls.gather(days=3650)   # ~10y window includes 2026 but NOT 2000
    assert agg["sessions"] == 1
