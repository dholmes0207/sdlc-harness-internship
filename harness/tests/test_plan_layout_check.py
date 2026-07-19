"""plan_layout_check — soft advisory that a plan dir matches the scaffold layout.

The checker never blocks (exit 0 always); it surfaces three drift signals the
approval hash cannot self-report: a phase file placed OUTSIDE the hashed locations
(root / phases/), a plan split across BOTH layouts, and an approval record whose
file_hashes miss a phase file now on disk (stale approval → re-approve needed).
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plan_layout_check as plc  # noqa: E402

_PLAN_MD = "---\ntitle: t\n---\n\n# Plan\n"
_PHASE_MD = "---\nphase: 1\n---\n\n# Phase\n\nbody\n"


def _plan(tmp_path: Path) -> Path:
    (tmp_path / "plan.md").write_text(_PLAN_MD, encoding="utf-8")
    return tmp_path


def test_clean_phases_layout_no_warnings(tmp_path):
    p = _plan(tmp_path)
    (p / "phases").mkdir()
    (p / "phases" / "phase-1-scout.md").write_text(_PHASE_MD, encoding="utf-8")
    assert plc.layout_warnings(p) == []


def test_legacy_flat_layout_no_warnings(tmp_path):
    p = _plan(tmp_path)
    (p / "phase-1.md").write_text(_PHASE_MD, encoding="utf-8")
    assert plc.layout_warnings(p) == []


def test_misplaced_phase_file_warns(tmp_path):
    p = _plan(tmp_path)
    (p / "sub").mkdir()
    (p / "sub" / "phase-1-lost.md").write_text(_PHASE_MD, encoding="utf-8")
    w = plc.layout_warnings(p)
    assert any("phase-1-lost.md" in m and "outside" in m.lower() for m in w)


def test_mixed_layout_warns(tmp_path):
    p = _plan(tmp_path)
    (p / "phase-1.md").write_text(_PHASE_MD, encoding="utf-8")
    (p / "phases").mkdir()
    (p / "phases" / "phase-2-x.md").write_text(_PHASE_MD, encoding="utf-8")
    w = plc.layout_warnings(p)
    assert any("both" in m.lower() or "mixed" in m.lower() for m in w)


def test_approval_missing_phase_warns(tmp_path):
    p = _plan(tmp_path)
    (p / "phases").mkdir()
    (p / "phases" / "phase-1-x.md").write_text(_PHASE_MD, encoding="utf-8")
    (p / "artifacts").mkdir()
    # approval record that hashed only plan.md — the phase file is uncovered
    (p / "artifacts" / "plan-approval.yaml").write_text(
        "schema: plan-approval/v1\nverdict: APPROVED\n"
        "file_hashes:\n  plan.md: abc123\n", encoding="utf-8")
    w = plc.layout_warnings(p)
    assert any("phase-1-x.md" in m and "approv" in m.lower() for m in w)


def test_approval_covers_phase_no_warn(tmp_path):
    p = _plan(tmp_path)
    (p / "phases").mkdir()
    (p / "phases" / "phase-1-x.md").write_text(_PHASE_MD, encoding="utf-8")
    (p / "artifacts").mkdir()
    (p / "artifacts" / "plan-approval.yaml").write_text(
        "schema: plan-approval/v1\nverdict: APPROVED\n"
        "file_hashes:\n  plan.md: abc123\n  phase-1-x.md: def456\n",
        encoding="utf-8")
    assert plc.layout_warnings(p) == []


def test_cli_exit_zero_even_with_warnings(tmp_path):
    p = _plan(tmp_path)
    (p / "sub").mkdir()
    (p / "sub" / "phase-9-lost.md").write_text(_PHASE_MD, encoding="utf-8")
    rc = plc.main(["--plan", str(p)])
    assert rc == 0
