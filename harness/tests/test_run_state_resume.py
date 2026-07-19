"""Resumable orchestration run-state + append-only metrics corpus.

A large fan-out that crashes mid-run should not restart from zero: state.json holds the last
transition per job so `--resume` skips completed jobs and re-dispatches in-flight ones as a
new attempt. Separately, one JSON line per finished job appends to a cross-run history corpus
(actor + ts stamped, never rewritten) that the skill reads for advisory routing suggestions.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills" / "workflow-orchestrate" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import run_state  # noqa: E402
import orchestrate_metrics  # noqa: E402


def test_state_write_read_round_trip(tmp_path):
    sp = tmp_path / "state.json"
    state = {"run_id": "orchestrate-x", "jobs": {
        "scout-api": {"id": "scout-api", "status": "success", "attempts": 1,
                      "runtime": "claude-code", "model": "haiku", "task": "scout",
                      "exitCode": 0, "durationMs": 12345, "timedOut": False, "worktree": None},
    }}
    run_state.write(str(sp), state)
    assert run_state.read(str(sp)) == state


def test_write_is_atomic_no_leftover_tmp(tmp_path):
    sp = tmp_path / "state.json"
    run_state.write(str(sp), {"run_id": "r", "jobs": {}})
    assert sp.is_file()
    # the atomic .tmp sibling must not survive a successful write
    assert not (tmp_path / "state.json.tmp").exists()


def test_read_missing_returns_empty(tmp_path):
    assert run_state.read(str(tmp_path / "nope.json")) == {}


def test_resume_skips_completed_job(tmp_path):
    state = {"run_id": "r", "jobs": {"done": {"id": "done", "status": "success", "attempts": 1}}}
    dispatch, attempt = run_state.should_dispatch(state, "done")
    assert dispatch is False
    assert attempt == 1


def test_resume_redispatches_in_flight_with_incremented_attempt(tmp_path):
    state = {"run_id": "r", "jobs": {"mid": {"id": "mid", "status": "in_progress", "attempts": 1}}}
    dispatch, attempt = run_state.should_dispatch(state, "mid")
    assert dispatch is True
    assert attempt == 2


def test_resume_dispatches_unknown_job_as_first_attempt(tmp_path):
    dispatch, attempt = run_state.should_dispatch({"run_id": "r", "jobs": {}}, "fresh")
    assert dispatch is True
    assert attempt == 1


def test_record_transition_persists(tmp_path):
    sp = tmp_path / "state.json"
    run_state.write(str(sp), {"run_id": "r", "jobs": {}})
    run_state.record_transition(str(sp), "j1", "in_progress", model="opus", task="cook")
    st = run_state.read(str(sp))
    assert st["jobs"]["j1"]["status"] == "in_progress"
    assert st["jobs"]["j1"]["model"] == "opus"


def test_state_and_metrics_resolve_under_harness_state_dir_not_reports(monkeypatch, tmp_path):
    """Run-state + the metrics corpus are machine-written runtime state — they belong under the
    harness state dir (gitignored), never under plans/reports/ (human-facing scratch)."""
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    sd = run_state.state_dir()
    assert str(sd) == str(tmp_path / "state")
    sp = run_state.run_state_path("orchestrate-abc")
    assert sp.endswith("state/orchestrate/orchestrate-abc/state.json")
    assert "plans/reports" not in sp
    hp = orchestrate_metrics.history_path()
    assert hp.endswith("state/orchestrate-history.jsonl")
    assert "plans/reports" not in hp


def test_write_token_persists_expected_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_USER", "tester")
    strategy = {"mode": "workflow", "sub_count": 6,
                "groups": [{"key": "research"}, {"key": "critique"}],
                "report_dir": str(tmp_path / "reports")}
    path = run_state.write_token("orchestrate-x", strategy, "sess-A")
    assert path.endswith("state/orchestrate/orchestrate-x/token.json")
    tok = json.loads(Path(path).read_text(encoding="utf-8"))
    assert tok["run_id"] == "orchestrate-x"
    assert tok["mode"] == "workflow"
    assert tok["sub_count"] == 6
    assert tok["groups"] == [{"key": "research"}, {"key": "critique"}]
    assert tok["report_dir"] == str(tmp_path / "reports")
    assert tok["session"] == "sess-A"
    assert isinstance(tok["ts"], (int, float))
    assert tok["expires_at"] > tok["ts"]
    assert tok["actor"]


def test_write_token_clamps_sub_count_to_cap(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_USER", "tester")
    strategy = {"mode": "workflow", "sub_count": 9999, "groups": [], "report_dir": "x"}
    path = run_state.write_token("orchestrate-y", strategy, "sess-A")
    tok = json.loads(Path(path).read_text(encoding="utf-8"))
    assert tok["sub_count"] == 32  # the default sub_count_cap


def test_write_token_atomic_no_leftover_tmp(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_USER", "tester")
    strategy = {"mode": "subagents", "sub_count": 2, "groups": [], "report_dir": "x"}
    run_state.write_token("orchestrate-z", strategy, "sess-A")
    token_dir = tmp_path / "state" / "orchestrate" / "orchestrate-z"
    assert (token_dir / "token.json").is_file()
    assert not (token_dir / "token.json.tmp").exists()


def test_metrics_append_only_stamps_actor_and_ts(tmp_path):
    hp = tmp_path / "orchestrate-history.jsonl"
    orchestrate_metrics.append(str(hp), {"run_id": "r", "job": "a", "status": "success"})
    orchestrate_metrics.append(str(hp), {"run_id": "r", "job": "b", "status": "failed"})
    lines = hp.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2, "append must not overwrite prior rows"
    for ln in lines:
        rec = json.loads(ln)
        assert rec.get("actor"), "every metric row carries an actor"
        assert rec.get("ts"), "every metric row carries a ts"
    assert json.loads(lines[0])["job"] == "a"
    assert json.loads(lines[1])["job"] == "b"
