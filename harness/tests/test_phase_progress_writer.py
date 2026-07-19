"""test_phase_progress_writer.py — P1 spike: per-phase evidence snapshot.

The hook fires PostToolUse(Write|Edit|MultiEdit). When the written file is a
plan's canonical verification.{json,yaml} carrying a PASS verdict + a phase id,
it COPIES that verification to verification-<phase>.json once (first-wins, never
overwriting an existing snapshot). It is the enforced source of the per-phase
evidence the derive/finalize stages count — agent writes one verification per
phase as it already does; the hook does the per-phase naming, so the count
cannot be gamed by a forgotten suffix.

Telemetry-class: fail-open, never raises, a non-matching write is a silent
no-op. The three [GATE] tests (3-distinct, retry-no-dup, blocked-then-pass)
are the spike's go/no-go.
"""
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _d in (str(_SCRIPTS), str(_HOOKS)):
    if _d not in sys.path:
        sys.path.insert(0, _d)

import phase_progress_writer  # noqa: E402


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


def _payload(path):
    return {"tool_name": "Write", "tool_input": {"file_path": str(path)}}


def _snapshots(pdir):
    return sorted(q.name for q in (pdir / "artifacts").glob("verification-*.json"))


# --- tests --------------------------------------------------------------------

def test_pass_write_snapshots(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    cf = _write_canonical(pdir, _verif_rec("P1"))
    phase_progress_writer.core(_payload(cf))
    assert (pdir / "artifacts" / "verification-P1.json").is_file()


def test_three_phase_cook_three_files(tmp_path, monkeypatch):
    """[GATE] cook 3 phases -> exactly 3 distinct verification-P{1,2,3}.json."""
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    for ph in ("P1", "P2", "P3"):
        cf = _write_canonical(pdir, _verif_rec(ph))
        phase_progress_writer.core(_payload(cf))
    assert _snapshots(pdir) == [
        "verification-P1.json", "verification-P2.json", "verification-P3.json"]


def test_retry_same_phase_no_duplicate(tmp_path, monkeypatch):
    """[GATE] re-writing the same phase never creates a second snapshot, and the
    FIRST evidence wins (first-wins, idempotent)."""
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    cf = _write_canonical(pdir, _verif_rec("P2"))
    cf.write_text(json.dumps(_verif_rec("P2")), encoding="utf-8")
    phase_progress_writer.core(_payload(cf))
    first = (pdir / "artifacts" / "verification-P2.json").read_text(encoding="utf-8")
    # retry with mutated content
    rec2 = _verif_rec("P2")
    rec2["checks"][0]["detail"] = "second attempt"
    cf.write_text(json.dumps(rec2), encoding="utf-8")
    phase_progress_writer.core(_payload(cf))
    assert _snapshots(pdir) == ["verification-P2.json"]
    assert (pdir / "artifacts" / "verification-P2.json").read_text(
        encoding="utf-8") == first


def test_blocked_then_pass(tmp_path, monkeypatch):
    """[GATE] a phase BLOCKED then PASS snapshots once, at PASS — no false-early."""
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    cf = _write_canonical(pdir, _verif_rec("P3", verdict="BLOCKED"))
    phase_progress_writer.core(_payload(cf))
    assert _snapshots(pdir) == []  # blocked => no snapshot
    cf.write_text(json.dumps(_verif_rec("P3")), encoding="utf-8")
    phase_progress_writer.core(_payload(cf))
    assert _snapshots(pdir) == ["verification-P3.json"]


def test_snapshot_content_matches(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    rec = _verif_rec("P1")
    cf = _write_canonical(pdir, rec)
    phase_progress_writer.core(_payload(cf))
    snap = json.loads(
        (pdir / "artifacts" / "verification-P1.json").read_text(encoding="utf-8"))
    assert snap == rec


def test_missing_phase_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    cf = _write_canonical(pdir, _verif_rec(None))
    phase_progress_writer.core(_payload(cf))
    assert _snapshots(pdir) == []


def test_unsafe_phase_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    cf = _write_canonical(pdir, _verif_rec("../evil"))
    phase_progress_writer.core(_payload(cf))
    assert _snapshots(pdir) == []
    # no path-escape: nothing written outside the plan's artifacts dir
    assert not (tmp_path / "plans" / "evil.json").exists()
    assert not (tmp_path / "evil.json").exists()


def test_non_verification_write_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    # writing plan.md -> ignored
    pm = pdir / "plan.md"
    pm.write_text("---\nstatus: in_progress\n---\n", encoding="utf-8")
    phase_progress_writer.core(_payload(pm))
    assert _snapshots(pdir) == []
    # writing an existing verification-P1.json -> hook must not self-trigger
    (pdir / "artifacts" / "verification-P1.json").write_text(
        json.dumps(_verif_rec("P1")), encoding="utf-8")
    phase_progress_writer.core(
        _payload(pdir / "artifacts" / "verification-P1.json"))
    assert _snapshots(pdir) == ["verification-P1.json"]  # unchanged, no P-of-P


def test_fail_verdict_no_snapshot(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    cf = _write_canonical(pdir, _verif_rec("P1", verdict="BLOCKED"))
    phase_progress_writer.core(_payload(cf))
    assert _snapshots(pdir) == []


def test_yaml_verification_form(tmp_path, monkeypatch):
    """A verification.yaml is snapshotted to verification-<phase>.json (the form
    derive globs), content preserved as the parsed record."""
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    rec = _verif_rec("P1")
    cf = _write_canonical(pdir, rec, ext="yaml")
    phase_progress_writer.core(_payload(cf))
    snap_path = pdir / "artifacts" / "verification-P1.json"
    assert snap_path.is_file()
    assert json.loads(snap_path.read_text(encoding="utf-8")) == rec


def test_pass_with_risk_snapshotted(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    cf = _write_canonical(pdir, _verif_rec("P1", verdict="PASS_WITH_RISK"))
    phase_progress_writer.core(_payload(cf))
    assert (pdir / "artifacts" / "verification-P1.json").is_file()


# --- P4: un-silence a PASS that forgot its `phase` -------------------------
# A PASS verification with no usable `phase` id can't be snapshotted, so the plan
# silently never reaches N/N and the ship gate will block with no clue why. The
# hook now warns (stderr, fail-open): the file, the fix, and the still-missing
# declared post — turning a silent drop into an actionable signal.

def _seed_graph(pdir, body="subtasks:\n  P1: {post: [verification-P1.json]}\n"):
    (pdir / "plan-graph.yaml").write_text(body, encoding="utf-8")


def test_warns_on_pass_without_phase(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    _seed_graph(pdir)
    cf = _write_canonical(pdir, _verif_rec(None))
    phase_progress_writer.core(_payload(cf))
    err = capsys.readouterr().err
    assert "[advisory]" in err
    assert "verification.json" in err
    assert "phase:" in err
    assert "verification-P1.json" in err  # declared post still missing
    assert _snapshots(pdir) == []  # still not snapshotted


def test_no_warn_when_phase_present(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    _seed_graph(pdir)
    cf = _write_canonical(pdir, _verif_rec("P1"))
    phase_progress_writer.core(_payload(cf))
    err = capsys.readouterr().err
    assert "[advisory]" not in err
    assert (pdir / "artifacts" / "verification-P1.json").is_file()  # regression


def test_no_warn_on_non_pass(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    _seed_graph(pdir)
    cf = _write_canonical(pdir, _verif_rec(None, verdict="BLOCKED"))
    phase_progress_writer.core(_payload(cf))
    assert "[advisory]" not in capsys.readouterr().err


def test_warn_degrades_without_graph(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)  # no plan-graph seeded
    cf = _write_canonical(pdir, _verif_rec(None))
    phase_progress_writer.core(_payload(cf))  # must not raise
    err = capsys.readouterr().err
    assert "[advisory]" in err  # generic advisory still emitted


def test_warn_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    _seed_graph(pdir)
    import derive_plan_completion as dpc

    def _boom(*a, **k):
        raise RuntimeError("boom")

    monkeypatch.setattr(dpc, "completion_state", _boom)
    cf = _write_canonical(pdir, _verif_rec(None))
    phase_progress_writer.core(_payload(cf))  # must not raise


def test_hook_never_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    pdir = _seed_plan(tmp_path)
    # garbage (non-dict) canonical verification
    (pdir / "artifacts" / "verification.json").write_text("[1, 2, 3]", encoding="utf-8")
    phase_progress_writer.core(
        _payload(pdir / "artifacts" / "verification.json"))  # must not raise
    assert _snapshots(pdir) == []
    # weird payloads
    phase_progress_writer.core({})
    phase_progress_writer.core({"tool_input": {}})
    phase_progress_writer.core({"tool_input": {"file_path": None}})
