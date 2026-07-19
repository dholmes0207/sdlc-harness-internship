"""test_finalize_plan.py — P3: deterministic open->close gated on derived
completion + cross-plan binding.

finalize_plan moves a plan to `completed` ONLY when derive says N/N phases have a
PASS snapshot AND the canonical verification belongs to this plan. The "done"
signal is the snapshot count, never a single verification verdict (the F1 trap).
Incomplete, cross-plan, cancelled, or outside-plans inputs are benign no-ops; it
never raises.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import finalize_plan as fp  # noqa: E402


def _seed(plan_dir, status="draft", nodes=("P1", "P2", "P3"),
          snapshots=("P1", "P2", "P3"), canonical_plan=None,
          canonical=True):
    (plan_dir / "artifacts").mkdir(parents=True)
    edges = "\n".join("  - {from: %s, to: %s}" % (a, b)
                      for a, b in zip(nodes, nodes[1:]))
    (plan_dir / "plan-graph.yaml").write_text("edges:\n" + edges + "\n",
                                              encoding="utf-8")
    (plan_dir / "plan.md").write_text(
        "---\nstatus: %s\n---\n\n# Plan\n" % status, encoding="utf-8")
    cp = canonical_plan if canonical_plan is not None else plan_dir.name
    if canonical:
        _verif(plan_dir, "verification.json", "P3", plan=cp)
    for ph in snapshots:
        _verif(plan_dir, "verification-%s.json" % ph, ph, plan=plan_dir.name)


def _verif(plan_dir, fname, phase, plan, verdict="PASS"):
    rec = {"stage": "cook", "plan": plan, "actor": "user:x",
           "ts": "2026-06-28T00:00:00+00:00",
           "checks": [{"name": "unit", "status": "PASS"}],
           "verdict": verdict, "phase": phase}
    (plan_dir / "artifacts" / fname).write_text(json.dumps(rec), encoding="utf-8")


def _status(plan_dir):
    import re
    t = (plan_dir / "plan.md").read_text(encoding="utf-8")
    return re.search(r"status:\s*(\S+)", t).group(1)


def test_complete_draft_finalizes(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, status="draft")
    r = fp.finalize_plan(p, root=tmp_path)
    assert r.ok and r.changed
    assert _status(p) == "completed"


def test_complete_pending_finalizes(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, status="pending")
    r = fp.finalize_plan(p, root=tmp_path)
    assert r.ok and r.changed
    assert _status(p) == "completed"


def test_complete_approved_finalizes(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, status="approved")
    r = fp.finalize_plan(p, root=tmp_path)
    assert r.ok and r.changed
    assert _status(p) == "completed"


def test_complete_in_progress_finalizes(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, status="in_progress")
    r = fp.finalize_plan(p, root=tmp_path)
    assert r.ok and r.changed
    assert _status(p) == "completed"


def test_incomplete_is_noop(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, status="in_progress", snapshots=("P1", "P2"))
    r = fp.finalize_plan(p, root=tmp_path)
    assert r.ok and not r.changed
    assert _status(p) == "in_progress"


def test_no_snapshots_noop(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, status="in_progress", snapshots=())
    r = fp.finalize_plan(p, root=tmp_path)
    assert not r.changed
    assert _status(p) == "in_progress"


def test_cross_plan_binding_noop(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, status="in_progress", canonical_plan="some-other-plan")
    r = fp.finalize_plan(p, root=tmp_path)
    assert r.ok and not r.changed
    assert _status(p) == "in_progress"


def test_already_completed_noop(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, status="completed")
    r = fp.finalize_plan(p, root=tmp_path)
    assert r.ok and not r.changed
    assert _status(p) == "completed"


def test_cancelled_left_alone(tmp_path):
    p = tmp_path / "plans" / "x"
    _seed(p, status="cancelled")
    r = fp.finalize_plan(p, root=tmp_path)
    assert not r.changed
    assert _status(p) == "cancelled"


def test_outside_plans_error(tmp_path):
    p = tmp_path / "notplans" / "x"
    _seed(p, status="draft")
    r = fp.finalize_plan(p, root=tmp_path)
    assert r.ok is False


def test_never_raises(tmp_path):
    p = tmp_path / "plans" / "x"
    (p / "artifacts").mkdir(parents=True)
    (p / "plan.md").write_text("garbage no frontmatter", encoding="utf-8")
    r = fp.finalize_plan(p, root=tmp_path)  # must not raise
    assert r.changed is False
