"""test_artifact_check_cli.py — the receipts-gate CLI around check_stage.

Judges ONE plan dir at a stage via the plan_dir seam (no active-plan resolver); exit 2 +
reason on a missing receipt, exit 0 + JSON PASS otherwise. Runs as a subprocess (the real
CI contract)."""
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
_CLI = _SCRIPTS / "artifact_check_cli.py"


def _env(root):
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(root)
    env["HARNESS_STATE_DIR"] = str(root / "state")
    env.pop("HARNESS_ACTIVE_PLAN", None)
    return env


def _plan(root, name="260101-0000-feature"):
    d = root / "plans" / name
    (d / "artifacts").mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: x\nstatus: in_progress\n---\n\n# X\n\nBody.\n\n"
        "## Phases\n\n| 1 | Pending |\n", encoding="utf-8")
    (d / "phase-01.md").write_text(
        "---\nphase: 1\nstatus: pending\n---\n\n# P1\n\nwork\n", encoding="utf-8")
    (d / "plan-graph.yaml").write_text(
        "edges: [{from: P1, to: P2}]\nsubtasks:\n  P1: {files_to_create: []}\n",
        encoding="utf-8")
    return d


def _verification(d):
    (d / "artifacts" / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": d.name, "actor": "user:t",
        "ts": datetime.now(timezone.utc).isoformat(),
        "verdict": "PASS", "checks": [{"name": "pytest", "status": "PASS"}]}),
        encoding="utf-8")


def _review(d):
    (d / "artifacts" / "review-decision.json").write_text(json.dumps({
        "verdict": "PASS", "reviewer": "user:t", "role": "reviewer",
        "rationale": "ok"}), encoding="utf-8")


def _approval(root, d):
    sys.path.insert(0, str(_SCRIPTS))
    import plan_approval as pa
    (d / "artifacts" / "plan-approval.json").write_text(json.dumps({
        "schema": "plan-approval/v1", "plan": d.name,
        "plan_hash": pa.plan_hash(d), "file_hashes": pa.file_hashes(d),
        "author": "user:t", "reviewer": "user:t", "verdict": "APPROVED",
        "rationale": "reviewed", "ts": datetime.now(timezone.utc).isoformat()}),
        encoding="utf-8")


def _run(root, *args):
    return subprocess.run([sys.executable, str(_CLI), *args],
                          capture_output=True, text=True, env=_env(root))


def test_missing_artifact_exit2_reason_stderr(tmp_path):
    d = _plan(tmp_path)  # no receipts at all
    proc = _run(tmp_path, "--stage", "pr", "--plan-dir", str(d), "--root", str(tmp_path))
    assert proc.returncode == 2
    assert "receipts-gate BLOCK" in proc.stderr


def test_pass_json_stdout_exit0(tmp_path):
    d = _plan(tmp_path)
    _verification(d)
    _review(d)
    _approval(tmp_path, d)
    proc = _run(tmp_path, "--stage", "pr", "--plan-dir", str(d), "--root", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["result"] == "PASS" and out["plan"] == d.name


def test_bad_plan_dir_exit2_actionable(tmp_path):
    proc = _run(tmp_path, "--stage", "pr", "--plan-dir",
                str(tmp_path / "plans" / "nope"), "--root", str(tmp_path))
    assert proc.returncode == 2
    assert "plan.md" in proc.stderr


def test_plan_dir_bypasses_active_resolver(tmp_path):
    # A NEWER in_progress plan B exists but A is passed explicitly → A is judged
    # (its receipts), never B (red-team H3). A is complete → PASS.
    a = _plan(tmp_path, "260101-0000-a")
    _verification(a); _review(a); _approval(tmp_path, a)
    _plan(tmp_path, "260101-0009-b-newer")  # newer, receiptless — must NOT be judged
    proc = _run(tmp_path, "--stage", "pr", "--plan-dir", str(a), "--root", str(tmp_path))
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["plan"] == "260101-0000-a"
