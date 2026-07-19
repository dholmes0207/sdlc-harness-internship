"""test_auto_finalize_wire.py — P4: wire snapshot + auto-open + auto-close into
the writer hook, plus the ship-belt backstop.

Two seams, one finalize policy:
  * phase_progress_writer (PostToolUse:verification write) — snapshot, then
    auto-open the plan it just wrote to, then finalize (close only at N/N).
  * auto_finalize_ship (PreToolUse:Skill hs:ship) — finalize the SINGLE active
    plan as a backstop; never sweeps the corpus.

Both gated by HARNESS_AUTO_FINALIZE (kill-switch leaves snapshots, drops flips);
both fail-open. Neither ever touches a plan other than the one in hand (F2).
"""
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _d in (str(_SCRIPTS), str(_HOOKS)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import phase_progress_writer as ppw  # noqa: E402
import auto_finalize_ship as afs  # noqa: E402


def _seed_plan(root, name="260628-0106-x", status="in_progress",
               nodes=("P1", "P2", "P3")):
    p = root / "plans" / name
    (p / "artifacts").mkdir(parents=True)
    edges = "\n".join("  - {from: %s, to: %s}" % (a, b)
                      for a, b in zip(nodes, nodes[1:]))
    (p / "plan-graph.yaml").write_text("edges:\n" + edges + "\n", encoding="utf-8")
    (p / "plan.md").write_text("---\nstatus: %s\n---\n\n# Plan\n" % status,
                               encoding="utf-8")
    return p


def _rec(name, phase, verdict="PASS"):
    return {"stage": "cook", "plan": name, "actor": "user:x",
            "ts": "2026-06-28T00:00:00+00:00",
            "checks": [{"name": "unit", "status": "PASS"}],
            "verdict": verdict, "phase": phase}


def _write_phase(p, phase, verdict="PASS", plan=None):
    """Simulate cook writing the canonical verification for a phase, then the
    PostToolUse hook firing."""
    cf = p / "artifacts" / "verification.json"
    cf.write_text(json.dumps(_rec(plan or p.name, phase, verdict)),
                  encoding="utf-8")
    ppw.core({"tool_name": "Write", "tool_input": {"file_path": str(cf)}})


def _seed_snapshots(p, phases):
    for ph in phases:
        (p / "artifacts" / ("verification-%s.json" % ph)).write_text(
            json.dumps(_rec(p.name, ph)), encoding="utf-8")


def _status(p):
    import re
    return re.search(r"status:\s*(\S+)",
                     (p / "plan.md").read_text(encoding="utf-8")).group(1)


def _skill(name):
    return {"tool_name": "Skill", "tool_input": {"skill": name}}


# --- writer wiring ------------------------------------------------------------

def test_final_phase_write_closes_plan(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_AUTO_FINALIZE", raising=False)
    p = _seed_plan(tmp_path, status="in_progress")
    _write_phase(p, "P1"); assert _status(p) == "in_progress"
    _write_phase(p, "P2"); assert _status(p) == "in_progress"
    _write_phase(p, "P3"); assert _status(p) == "completed"


def test_mid_phase_write_opens_not_closes(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_AUTO_FINALIZE", raising=False)
    p = _seed_plan(tmp_path, status="draft")
    _write_phase(p, "P1")
    assert _status(p) == "in_progress"  # opened, not closed


def test_draft_trap_fixed(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_AUTO_FINALIZE", raising=False)
    p = _seed_plan(tmp_path, status="pending")
    _write_phase(p, "P1")
    assert _status(p) == "in_progress"


def test_kill_switch_no_flip_but_snapshot_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("HARNESS_AUTO_FINALIZE", "0")
    p = _seed_plan(tmp_path, status="draft")
    _write_phase(p, "P1"); _write_phase(p, "P2"); _write_phase(p, "P3")
    snaps = sorted(q.name for q in (p / "artifacts").glob("verification-*.json"))
    assert snaps == ["verification-P1.json", "verification-P2.json",
                     "verification-P3.json"]
    assert _status(p) == "draft"  # snapshots kept, status untouched


# --- ship-belt ----------------------------------------------------------------

def test_ship_belt_closes_complete_active(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_AUTO_FINALIZE", raising=False)
    p = _seed_plan(tmp_path, status="in_progress")
    monkeypatch.setenv("HARNESS_ACTIVE_PLAN", p.name)
    _seed_snapshots(p, ("P1", "P2", "P3"))
    (p / "artifacts" / "verification.json").write_text(
        json.dumps(_rec(p.name, "P3")), encoding="utf-8")
    afs.core(_skill("hs:ship"))
    assert _status(p) == "completed"


def test_ship_belt_noop_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_AUTO_FINALIZE", raising=False)
    p = _seed_plan(tmp_path, status="in_progress")
    monkeypatch.setenv("HARNESS_ACTIVE_PLAN", p.name)
    _seed_snapshots(p, ("P1", "P2"))
    afs.core(_skill("hs:ship"))
    assert _status(p) == "in_progress"


def test_ship_belt_ignores_hs_git(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_AUTO_FINALIZE", raising=False)
    p = _seed_plan(tmp_path, status="in_progress")
    monkeypatch.setenv("HARNESS_ACTIVE_PLAN", p.name)
    _seed_snapshots(p, ("P1", "P2", "P3"))
    (p / "artifacts" / "verification.json").write_text(
        json.dumps(_rec(p.name, "P3")), encoding="utf-8")
    afs.core(_skill("hs:git"))
    assert _status(p) == "in_progress"  # commit mid-cook must not close


def test_no_corpus_sweep(tmp_path, monkeypatch):
    """A verification write for plan-A never touches another cooked-open plan."""
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_AUTO_FINALIZE", raising=False)
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    other = _seed_plan(tmp_path, name="260627-0339-other", status="in_progress")
    _seed_snapshots(other, ("P1", "P2", "P3"))  # other is complete-but-open
    a = _seed_plan(tmp_path, name="260628-0106-a", status="in_progress")
    _write_phase(a, "P1"); _write_phase(a, "P2"); _write_phase(a, "P3")
    assert _status(a) == "completed"
    assert _status(other) == "in_progress"  # untouched — no sweep


def test_cross_plan_binding(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.delenv("HARNESS_AUTO_FINALIZE", raising=False)
    p = _seed_plan(tmp_path, status="in_progress")
    _seed_snapshots(p, ("P1", "P2"))
    # final phase write whose canonical verification names a DIFFERENT plan
    _write_phase(p, "P3", plan="some-other-plan")
    assert _status(p) == "in_progress"  # binding blocks the close


def test_hooks_never_exit_2(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    # garbage payloads -> no raise, no flip
    ppw.core({})
    ppw.core({"tool_input": {"file_path": 123}})
    afs.core({})
    afs.core({"tool_input": {"skill": None}})
    afs.core(_skill("hs:ship"))  # no active plan resolvable -> no-op
