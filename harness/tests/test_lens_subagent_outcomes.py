"""test_lens_subagent_outcomes.py — per-agent-type outcome distribution from
subagent-outcomes.jsonl (what track_subagent_outcome records). Read-only,
advisory. Honest about the classifier's coverage: most outcomes are 'unknown'
on hosts whose transcript shape the classifier can't map, so the lens reports a
definite-outcome rate rather than implying every run was adjudicated.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import lens_subagent_outcomes as lens  # noqa: E402


def _seed(tmp_path, rows):
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True)
    now = datetime.now(timezone.utc)
    lines = []
    for rec in rows:
        rec = dict(rec)
        rec.setdefault("ts", (now - timedelta(days=rec.pop("age_days", 0))).isoformat())
        lines.append(json.dumps(rec))
    (tel / "subagent-outcomes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_aggregates_by_agent_type(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [
        {"agent_type": "researcher", "outcome": "success"},
        {"agent_type": "researcher", "outcome": "unknown"},
        {"agent_type": "researcher", "outcome": "timeout"},
        {"agent_type": "coder", "outcome": "unknown"},
    ])
    agg = lens.gather(days=30, top=10)
    by = {a["agent_type"]: a for a in agg["agents"]}
    assert agg["total"] == 4
    assert by["researcher"]["runs"] == 3
    assert by["researcher"]["outcomes"]["success"] == 1
    assert by["researcher"]["outcomes"]["timeout"] == 1
    # 2 of 3 researcher outcomes are definite (success+timeout), 1 unknown
    assert abs(by["researcher"]["known_rate"] - (2 / 3)) < 1e-6


def test_overall_definite_rate_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    _seed(tmp_path, [{"agent_type": "x", "outcome": "unknown"} for _ in range(9)]
          + [{"agent_type": "x", "outcome": "success"}])
    agg = lens.gather(days=30, top=10)
    assert agg["total"] == 10
    assert abs(agg["known_rate"] - 0.1) < 1e-6   # 1/10 definite


def test_non_object_and_old_lines_handled(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    tel = tmp_path / "state" / "telemetry"
    tel.mkdir(parents=True)
    now = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    (tel / "subagent-outcomes.jsonl").write_text(
        "[1,2,3]\n"
        '{"agent_type":"r","outcome":"success","ts":"%s"}\n'
        '{"agent_type":"r","outcome":"success","ts":"%s"}\n' % (now, old),
        encoding="utf-8")
    agg = lens.gather(days=30, top=10)  # must not raise
    assert agg["total"] == 1   # non-object skipped, old excluded


def test_missing_sink_clean(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    (tmp_path / "state" / "telemetry").mkdir(parents=True)
    agg = lens.gather(days=30, top=10)
    assert agg["total"] == 0 and agg["agents"] == []


def test_deferred_reclassify_unknown_from_transcript(tmp_path, monkeypatch):
    # The hook records `unknown` when the subagent transcript isn't flushed yet at
    # SubagentStop. By lens-render time the file IS flushed, so the lens reclassifies
    # from the stored transcript path using the authoritative stop_reason.
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    sub = tmp_path / "agent-abc1.jsonl"
    sub.write_text(json.dumps({"message": {"role": "assistant",
                   "stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}}) + "\n")
    _seed(tmp_path, [
        {"agent_type": "Explore", "outcome": "unknown", "transcript": str(sub)},   # → success
        {"agent_type": "Explore", "outcome": "unknown", "transcript": str(tmp_path / "gone.jsonl")},  # missing → unknown
        {"agent_type": "Explore", "outcome": "unknown"},                            # no path → unknown
    ])
    agg = lens.gather(days=30, top=10)
    by = {a["agent_type"]: a for a in agg["agents"]}["Explore"]
    assert by["runs"] == 3
    assert by["outcomes"].get("success") == 1          # reclassified from the flushed transcript
    assert by["outcomes"].get("unknown") == 2          # missing-file + no-path stay honest
    assert abs(by["known_rate"] - (1 / 3)) < 1e-6


def test_registered_in_analyze_telemetry():
    import analyze_telemetry as at
    assert "subagent_outcomes" in at.LENS_REGISTRY
