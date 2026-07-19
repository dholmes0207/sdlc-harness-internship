"""test_verification_snapshot.py — the shared snapshot/lifecycle module.

The per-phase snapshot + plan-lifecycle logic used to live only inside the
PostToolUse hook. It is now a standalone module so BOTH the hook and the
write_verification.py script drive the exact same code path (no drift between a
Bash-written and a Write-tool-written verification). These tests pin that the
extracted public surface (snapshot / drive_lifecycle / auto_finalize_enabled /
verification_plan_dir) behaves identically to the in-hook original.
"""
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _d in (str(_SCRIPTS), str(_HOOKS)):
    if _d not in sys.path:
        sys.path.insert(0, _d)


# --- helpers ------------------------------------------------------------------

def _seed_plan(root, name="260628-0106-x"):
    pdir = root / "plans" / name
    (pdir / "artifacts").mkdir(parents=True)
    return pdir


def _verif_rec(phase, verdict="PASS", plan="260628-0106-x"):
    rec = {
        "stage": "ship",
        "plan": plan,
        "actor": "user:x",
        "ts": "2026-06-28T00:00:00+00:00",
        "checks": [{"name": "unit", "status": "PASS"}],
        "verdict": verdict,
    }
    if phase is not None:
        rec["phase"] = phase
    return rec


def _write_canonical(pdir, rec, ext="json"):
    p = pdir / "artifacts" / ("verification.%s" % ext)
    if ext == "yaml":
        import yaml
        p.write_text(yaml.safe_dump(rec), encoding="utf-8")
    else:
        p.write_text(json.dumps(rec), encoding="utf-8")
    return p


def _seed_graph(pdir, node="p1"):
    (pdir / "plan-graph.yaml").write_text(
        "subtasks:\n  %s: {post: [verification-%s.json]}\n" % (node, node),
        encoding="utf-8")


def _snapshots(pdir):
    return sorted(q.name for q in (pdir / "artifacts").glob("verification-*.json"))


# --- tests --------------------------------------------------------------------

def test_module_imports():
    import verification_snapshot as vsnap
    for name in ("snapshot", "drive_lifecycle", "auto_finalize_enabled",
                 "verification_plan_dir"):
        assert hasattr(vsnap, name), name


def test_snapshot_first_wins(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    import verification_snapshot as vsnap
    pdir = _seed_plan(tmp_path)
    _write_canonical(pdir, _verif_rec("p1"))
    vsnap.snapshot(pdir)
    first = (pdir / "artifacts" / "verification-p1.json").read_text(encoding="utf-8")
    rec2 = _verif_rec("p1")
    rec2["checks"][0]["detail"] = "second attempt"
    _write_canonical(pdir, rec2)
    vsnap.snapshot(pdir)
    assert _snapshots(pdir) == ["verification-p1.json"]
    assert (pdir / "artifacts" / "verification-p1.json").read_text(
        encoding="utf-8") == first


def test_snapshot_no_phase_warns(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    import verification_snapshot as vsnap
    pdir = _seed_plan(tmp_path)
    _seed_graph(pdir)
    _write_canonical(pdir, _verif_rec(None))
    vsnap.snapshot(pdir)
    err = capsys.readouterr().err
    assert "[advisory]" in err
    assert _snapshots(pdir) == []


def test_snapshot_non_pass_noop(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    import verification_snapshot as vsnap
    pdir = _seed_plan(tmp_path)
    _write_canonical(pdir, _verif_rec("p1", verdict="BLOCKED"))
    vsnap.snapshot(pdir)
    assert _snapshots(pdir) == []


def test_drive_lifecycle_opens_and_closes(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    import verification_snapshot as vsnap
    pdir = _seed_plan(tmp_path)
    (pdir / "plan.md").write_text("---\nstatus: pending\n---\n# x\n", encoding="utf-8")
    _seed_graph(pdir)
    _write_canonical(pdir, _verif_rec("p1"))
    vsnap.snapshot(pdir)
    vsnap.drive_lifecycle(pdir)
    assert "status: completed" in (pdir / "plan.md").read_text(encoding="utf-8")


def test_auto_finalize_kill_switch(monkeypatch):
    import verification_snapshot as vsnap
    monkeypatch.setenv("HARNESS_AUTO_FINALIZE", "0")
    assert vsnap.auto_finalize_enabled() is False
    monkeypatch.delenv("HARNESS_AUTO_FINALIZE", raising=False)
    assert vsnap.auto_finalize_enabled() is True
