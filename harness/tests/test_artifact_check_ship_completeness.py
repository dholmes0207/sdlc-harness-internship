"""test_artifact_check_ship_completeness.py — P3: block ship/deploy when the
active plan has not reached N/N phases (per the declarative post obligation that
derive_plan_completion now reads).

Completeness is meaningful only at the terminal "release the work" stages, so the
branch fires for ship/deploy ONLY — push/pr/merge happen mid-cook and must NOT be
blocked on completeness (C2). The branch is fail-safe: any raise degrades to
no-block (C3), and a None plan_dir (solo require_plan:false) is guarded (C7).

Each test seeds a custom stage-policy with empty `requires:` so the ONLY thing
that can block is the completeness branch under test.
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402

_POLICY = (
    "stages:\n"
    "  ship:\n    hard: true\n    require_plan: true\n    requires: []\n"
    "  deploy:\n    hard: true\n    require_plan: true\n    requires: []\n"
    "  push:\n    hard: true\n    require_plan: true\n    requires: []\n"
)
_POLICY_NO_PLAN = (
    "stages:\n"
    "  ship:\n    hard: true\n    require_plan: false\n    requires: []\n"
)


def _mk_plan(root, name="260628-1251-x", nodes=("P1", "P2")):
    d = root / "plans" / name
    (d / "artifacts").mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: in_progress\n---\n" % name, encoding="utf-8")
    body = "subtasks:\n" + "".join(
        "  %s: {files_to_modify: [%s.py]}\n" % (n, n.lower()) for n in nodes)
    (d / "plan-graph.yaml").write_text(body, encoding="utf-8")
    return d


def _snap(plan_dir, phase, verdict="PASS"):
    rec = {"stage": "cook", "plan": plan_dir.name, "actor": "user:x",
           "ts": "2026-06-28T00:00:00+00:00",
           "checks": [{"name": "unit", "status": "PASS"}],
           "verdict": verdict, "phase": phase}
    (plan_dir / "artifacts" / ("verification-%s.json" % phase)).write_text(
        json.dumps(rec), encoding="utf-8")


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    monkeypatch.delenv("HARNESS_STAGE_POLICY", raising=False)
    policy = tmp_path / "stage-policy.yaml"
    policy.write_text(_POLICY, encoding="utf-8")
    monkeypatch.setenv("HARNESS_STAGE_POLICY", str(policy))
    return tmp_path, monkeypatch


def test_ship_blocks_when_incomplete(env):
    root, mp = env
    d = _mk_plan(root)
    _snap(d, "P1")  # only 1 of 2 nodes
    mp.setenv("HARNESS_ACTIVE_PLAN", str(d))
    reason = ac.check_stage("ship", root)
    assert reason is not None
    assert "P2" in reason
    assert "phase" in reason


def test_ship_passes_when_complete(env):
    root, mp = env
    d = _mk_plan(root)
    _snap(d, "P1"); _snap(d, "P2")
    mp.setenv("HARNESS_ACTIVE_PLAN", str(d))
    assert ac.check_stage("ship", root) is None


def test_push_not_blocked_midcook(env):
    root, mp = env
    d = _mk_plan(root)
    _snap(d, "P1")  # incomplete, but push must not demand completeness
    mp.setenv("HARNESS_ACTIVE_PLAN", str(d))
    assert ac.check_stage("push", root) is None


def test_block_then_pass(env):
    root, mp = env
    d = _mk_plan(root)
    _snap(d, "P1")
    mp.setenv("HARNESS_ACTIVE_PLAN", str(d))
    assert ac.check_stage("ship", root) is not None
    _snap(d, "P2")  # complete the plan
    assert ac.check_stage("ship", root) is None


def test_plan_dir_none_no_crash(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    policy = tmp_path / "stage-policy.yaml"
    policy.write_text(_POLICY_NO_PLAN, encoding="utf-8")
    monkeypatch.setenv("HARNESS_STAGE_POLICY", str(policy))
    # no in_progress plan exists -> plan_dir None; require_plan:false so no presence
    # block -> the completeness branch must guard None and return None, not crash.
    assert ac.check_stage("ship", tmp_path) is None


def test_checker_raises_no_block(env, monkeypatch):
    root, mp = env
    d = _mk_plan(root)
    _snap(d, "P1")
    mp.setenv("HARNESS_ACTIVE_PLAN", str(d))
    import derive_plan_completion as dpc

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(dpc, "completion_state", _boom)
    # a raise in the completeness branch degrades to no-block (fail-safe C3).
    assert ac.check_stage("ship", root) is None
