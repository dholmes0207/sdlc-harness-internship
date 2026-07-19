"""test_skillcreator_optimize.py — Tier-2 optimize loop for skill-creator.

The optimize loop iterates a skill description: eval its trigger behavior, ask a
model (via claude -p, not the anthropic SDK) for a structurally different
description, re-eval, and keep the best. aggregate_benchmark rolls per-run
grading files into summary stats.

These tests stub the model and the eval so no real claude -p runs.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_SC_SCRIPTS = (
    Path(__file__).resolve().parent.parent
    / "plugins" / "hs" / "skills" / "skill-creator" / "scripts"
)
if str(_SC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SC_SCRIPTS))

import improve_description as imp  # noqa: E402
import run_loop as rl  # noqa: E402
import aggregate_benchmark as agg  # noqa: E402


def _eval_results(passed, total, failed_queries=()):
    results = []
    for i in range(total):
        q = f"q{i}"
        is_fail = q in failed_queries
        results.append({
            "query": q,
            "should_trigger": True,
            "trigger_rate": 0.0 if is_fail else 1.0,
            "triggers": 0 if is_fail else 1,
            "runs": 1,
            "pass": not is_fail,
        })
    return {
        "skill_name": "demo",
        "description": "a description",
        "results": results,
        "summary": {"passed": passed, "failed": total - passed, "total": total},
    }


class _FakeProc:
    """Stand-in for subprocess.run's CompletedProcess."""

    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


# --- _claude_text error contract (claude -p failures must NOT become a desc) ---

def test_claude_text_returns_result_on_success(monkeypatch):
    monkeypatch.setattr(imp.subprocess, "run", lambda cmd, **kw: _FakeProc(
        stdout=json.dumps({"type": "result", "subtype": "success",
                           "is_error": False, "result": "hello there"})))
    assert imp._claude_text("p", "m") == "hello there"


def test_claude_text_raises_on_error_envelope(monkeypatch):
    monkeypatch.setattr(imp.subprocess, "run", lambda cmd, **kw: _FakeProc(
        stdout=json.dumps({"type": "result", "subtype": "error_during_execution",
                           "is_error": True, "result": "boom"})))
    with pytest.raises(RuntimeError):
        imp._claude_text("p", "m")


def test_claude_text_raises_on_nonzero_exit(monkeypatch):
    monkeypatch.setattr(imp.subprocess, "run", lambda cmd, **kw: _FakeProc(
        stdout="", stderr="auth failed", returncode=1))
    with pytest.raises(RuntimeError):
        imp._claude_text("p", "m")


def test_claude_text_raises_on_timeout(monkeypatch):
    def fake_run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 120)
    monkeypatch.setattr(imp.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError):
        imp._claude_text("p", "m")


def test_claude_text_raises_on_empty_result(monkeypatch):
    monkeypatch.setattr(imp.subprocess, "run", lambda cmd, **kw: _FakeProc(
        stdout=json.dumps({"result": "   "})))
    with pytest.raises(RuntimeError):
        imp._claude_text("p", "m")


def test_claude_text_raises_on_non_json(monkeypatch):
    monkeypatch.setattr(imp.subprocess, "run", lambda cmd, **kw: _FakeProc(
        stdout="not json at all"))
    with pytest.raises(RuntimeError):
        imp._claude_text("p", "m")


# --- improve_description (claude -p, no anthropic SDK) -------------------------


def test_improve_description_calls_claude_p(monkeypatch):
    captured = {}

    def fake_claude_text(prompt, model, *, timeout=120):
        captured["prompt"] = prompt
        captured["model"] = model
        return "Here you go:\n<new_description>Use this skill to do the demo thing.</new_description>"

    monkeypatch.setattr(imp, "_claude_text", fake_claude_text)
    out = imp.improve_description(
        skill_name="demo",
        skill_content="# Demo\nbody",
        current_description="old desc",
        eval_results=_eval_results(1, 2, failed_queries={"q1"}),
        history=[],
        model="claude-test",
    )
    assert out == "Use this skill to do the demo thing."
    # the failing query must reach the improvement prompt
    assert "q1" in captured["prompt"]


# --- run_loop (convergence) ---------------------------------------------------

def test_run_loop_converges(monkeypatch, tmp_path):
    # SKILL.md so parse_skill_md works
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: hs:demo\ndescription: start desc\n---\n\n# Demo\nbody\n")

    # eval improves across iterations: iter1 fails 1, iter2 all pass
    calls = {"n": 0}

    def fake_run_eval(eval_set, skill_name, description, **kw):
        calls["n"] += 1
        if calls["n"] == 1:
            return _eval_results(1, 2, failed_queries={"q1"})
        return _eval_results(2, 2)

    monkeypatch.setattr(rl, "run_eval", fake_run_eval)
    monkeypatch.setattr(rl, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(rl, "improve_description",
                        lambda **kw: "a better description")

    out = rl.run_loop(
        eval_set=[{"query": "q0", "should_trigger": True},
                  {"query": "q1", "should_trigger": True}],
        skill_path=skill,
        description_override=None,
        num_workers=1, timeout=5, max_iterations=5,
        runs_per_query=1, trigger_threshold=0.5, holdout=0.0,
        model="claude-test", verbose=False,
    )
    assert "all_passed" in out["exit_reason"]
    assert out["iterations_run"] == 2
    assert out["best_description"]  # non-empty


def test_run_loop_respects_max_iterations(monkeypatch, tmp_path):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text(
        "---\nname: hs:demo\ndescription: start\n---\n\n# Demo\n")

    monkeypatch.setattr(rl, "run_eval",
                        lambda *a, **k: _eval_results(1, 2, failed_queries={"q1"}))
    monkeypatch.setattr(rl, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(rl, "improve_description", lambda **kw: "another")

    out = rl.run_loop(
        eval_set=[{"query": "q0", "should_trigger": True},
                  {"query": "q1", "should_trigger": True}],
        skill_path=skill, description_override=None,
        num_workers=1, timeout=5, max_iterations=3,
        runs_per_query=1, trigger_threshold=0.5, holdout=0.0,
        model="claude-test", verbose=False,
    )
    assert "max_iterations" in out["exit_reason"]
    assert out["iterations_run"] == 3


# --- aggregate_benchmark (pure stats) -----------------------------------------

def test_aggregate_benchmark_calculate_stats():
    stats = agg.calculate_stats([2.0, 4.0, 6.0])
    assert stats["mean"] == 4.0
    assert stats["min"] == 2.0
    assert stats["max"] == 6.0
    assert stats["stddev"] == 2.0


def test_aggregate_benchmark_empty_stats():
    stats = agg.calculate_stats([])
    assert stats == {"mean": 0.0, "stddev": 0.0, "min": 0.0, "max": 0.0}


# --- improve_description content guard (never adopt empty / untagged output) ---

def test_improve_description_keeps_current_on_no_tag(monkeypatch):
    # A refusal or any untagged response must NOT be adopted as the description.
    monkeypatch.setattr(imp, "_claude_text", lambda *a, **k: "I cannot help with that.")
    out = imp.improve_description(
        skill_name="demo", skill_content="x", current_description="KEEP ME",
        eval_results=_eval_results(1, 2, failed_queries={"q1"}), history=[], model="m")
    assert out == "KEEP ME"


def test_improve_description_shorten_keeps_long_on_empty(monkeypatch):
    long_desc = "x" * 1100  # >1024 triggers the shorten pass
    calls = {"n": 0}

    def fake(prompt, model, *, timeout=120):
        calls["n"] += 1
        if calls["n"] == 1:
            return f"<new_description>{long_desc}</new_description>"
        return "no tag here"  # shorten fails to produce a tagged description

    monkeypatch.setattr(imp, "_claude_text", fake)
    out = imp.improve_description(
        skill_name="demo", skill_content="x", current_description="old",
        eval_results=_eval_results(1, 1, failed_queries={"q0"}), history=[], model="m")
    # the over-limit-but-usable description survives; it is never blanked
    assert out == long_desc


# --- run_loop resilience + holdout guard --------------------------------------

def test_run_loop_empty_train_raises(monkeypatch, tmp_path):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: hs:demo\ndescription: d\n---\n\n# Demo\n")
    monkeypatch.setattr(rl, "find_project_root", lambda: tmp_path)
    # 1 positive + 1 negative with holdout 0.4 → both land in test, train is empty
    with pytest.raises(ValueError):
        rl.run_loop(
            eval_set=[{"query": "q0", "should_trigger": True},
                      {"query": "q1", "should_trigger": False}],
            skill_path=skill, description_override=None,
            num_workers=1, timeout=5, max_iterations=3, runs_per_query=1,
            trigger_threshold=0.5, holdout=0.4, model="m", verbose=False)


def test_run_loop_survives_improve_error(monkeypatch, tmp_path):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: hs:demo\ndescription: d\n---\n\n# Demo\n")
    monkeypatch.setattr(rl, "run_eval",
                        lambda *a, **k: _eval_results(1, 2, failed_queries={"q1"}))
    monkeypatch.setattr(rl, "find_project_root", lambda: tmp_path)

    def boom(**kw):
        raise RuntimeError("claude -p timed out")

    monkeypatch.setattr(rl, "improve_description", boom)
    out = rl.run_loop(
        eval_set=[{"query": "q0", "should_trigger": True},
                  {"query": "q1", "should_trigger": True}],
        skill_path=skill, description_override=None,
        num_workers=1, timeout=5, max_iterations=5, runs_per_query=1,
        trigger_threshold=0.5, holdout=0.0, model="m", verbose=False)
    assert "improve_error" in out["exit_reason"]
    assert out["best_description"]  # still returns the best from history (iteration 1)
    assert out["iterations_run"] == 1


def test_run_loop_blinds_test_scores_from_improve(monkeypatch, tmp_path):
    skill = tmp_path / "demo"
    skill.mkdir()
    (skill / "SKILL.md").write_text("---\nname: hs:demo\ndescription: d\n---\n\n# Demo\n")
    eval_set = ([{"query": f"p{i}", "should_trigger": True} for i in range(4)]
                + [{"query": f"n{i}", "should_trigger": False} for i in range(4)])

    def fake_run_eval(all_queries, name, desc, **kw):
        results = []
        for q in all_queries:
            should = q["should_trigger"]
            is_fail = (q["query"] == "p0")  # one train/test positive fails → drives improve
            results.append({
                "query": q["query"], "should_trigger": should,
                "trigger_rate": 0.0 if is_fail else (1.0 if should else 0.0),
                "triggers": 0 if (is_fail or not should) else 1, "runs": 1,
                "pass": (not is_fail) if should else True,
            })
        passed = sum(1 for r in results if r["pass"])
        return {"skill_name": name, "description": desc, "results": results,
                "summary": {"passed": passed, "failed": len(results) - passed,
                            "total": len(results)}}

    def fake_improve(**kw):
        # blinded history handed to the model must never carry test_ scores
        for h in kw["history"]:
            assert not any(k.startswith("test_") for k in h), f"test_ leaked: {list(h)}"
        return "an improved description"

    monkeypatch.setattr(rl, "run_eval", fake_run_eval)
    monkeypatch.setattr(rl, "find_project_root", lambda: tmp_path)
    monkeypatch.setattr(rl, "improve_description", fake_improve)
    out = rl.run_loop(
        eval_set=eval_set, skill_path=skill, description_override=None,
        num_workers=1, timeout=5, max_iterations=2, runs_per_query=1,
        trigger_threshold=0.5, holdout=0.4, model="m", verbose=False)
    assert out["train_size"] >= 1 and out["test_size"] >= 1
    assert out["best_test_score"] is not None
