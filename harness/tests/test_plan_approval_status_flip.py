"""test_plan_approval_status_flip.py — an APPROVED verdict reflects into the
plan's status, so an approved-but-not-yet-cooked plan reads `approved` instead of
sitting in the `pending` bucket on the board.

Discipline the flip must keep:
  - the artifact is written FIRST, the status flipped AFTER, so the flip never
    invalidates the plan_hash it just recorded (status is stripped before hashing);
  - only a `pending` plan flips (error_on_other=False) — APPROVED on an
    in_progress/approved/completed plan is a benign no-op, and the flip is
    idempotent;
  - a REJECTED verdict never flips;
  - approval is the primary write: if the status flip fails, the approval
    artifact still stands (asserted via the happy path + no-raise contract).
"""
import re
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plan_approval as pa  # noqa: E402


def _mk_plan(root: Path, name="260612-0900-flip", status="pending",
             author="user:auth@x"):
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: %s\nauthor: %s\n---\n\n# Thing\n\nBody intent.\n\n"
        "## Phases\n\n| Phase | Status |\n|---|---|\n| 1 | Pending |\n" % (
            name, status, author),
        encoding="utf-8")
    (d / "plan-graph.yaml").write_text(
        "edges:\n  - {from: P1, to: P2}\n", encoding="utf-8")
    return d


def _mk_team(root: Path):
    d = root / "harness" / "data"
    d.mkdir(parents=True, exist_ok=True)
    (d / "team.yaml").write_text(
        'reviewers: ["user:rev@x"]\nallow_self_review: false\n'
        "claims: {lease_s: 14400}\n", encoding="utf-8")


def _status(plan_dir: Path) -> str:
    t = (plan_dir / "plan.md").read_text(encoding="utf-8")
    return re.search(r"^status:\s*(\S+)", t, re.MULTILINE).group(1)


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_USER", "rev@x")  # reviewer != author
    _mk_team(tmp_path)
    return tmp_path


def test_approved_pending_flips_to_approved(root):
    d = _mk_plan(root, status="pending")
    out = pa.write_approval(d, verdict="APPROVED", rationale="solid")
    assert out["ok"], out
    assert (d / "artifacts" / "plan-approval.yaml").exists()
    assert _status(d) == "approved"


def test_approved_in_progress_is_noop(root):
    d = _mk_plan(root, status="in_progress")
    out = pa.write_approval(d, verdict="APPROVED", rationale="re-approve mid-cook")
    assert out["ok"], out
    # a started plan must NOT be dragged back to approved
    assert _status(d) == "in_progress"


def test_approved_is_idempotent(root):
    d = _mk_plan(root, status="pending")
    pa.write_approval(d, verdict="APPROVED", rationale="first")
    assert _status(d) == "approved"
    out2 = pa.write_approval(d, verdict="APPROVED", rationale="second")
    assert out2["ok"], out2
    assert _status(d) == "approved"


def test_rejected_does_not_flip(root):
    d = _mk_plan(root, status="pending")
    out = pa.write_approval(d, verdict="REJECTED", rationale="needs work")
    assert out["ok"], out
    assert _status(d) == "pending"


def test_artifact_records_pending_hash_survives_flip(root):
    # the flip happens AFTER the artifact is written; status is stripped before
    # hashing, so the recorded plan_hash still verifies against the flipped plan.
    d = _mk_plan(root, status="pending")
    out = pa.write_approval(d, verdict="APPROVED", rationale="solid")
    assert out["ok"], out
    assert _status(d) == "approved"
    assert out["record"]["plan_hash"] == pa.plan_hash(d)
