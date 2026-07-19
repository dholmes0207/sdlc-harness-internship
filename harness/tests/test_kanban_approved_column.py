"""test_kanban_approved_column.py — the kanban board must teach the cleaned-up
status vocabulary: an `approved` column exists, `approved` is no longer listed as
a DRIFT value (it is canonical now), and the dead `awaiting_human_approval`
column is gone.

This locks the board prose against the schema: leaving `approved` in the DRIFT
example would make the board flag every reviewed-but-not-cooked plan as drift —
the exact mis-bucketing this cleanup removes.
"""
from pathlib import Path
import pytest

_SKILL = (Path(__file__).resolve().parent.parent / "plugins" / "hs" / "skills"
          / "plans-kanban" / "SKILL.md")



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _text():
    return _SKILL.read_text(encoding="utf-8")


def test_approved_column_present():
    text = _text()
    # the column/row mapping table names an APPROVED column for the approved state
    assert "APPROVED" in text
    assert "`approved`" in text


def test_approved_not_listed_as_drift():
    # find the DRIFT row and assert `approved` is not among its example values
    drift_lines = [ln for ln in _text().splitlines()
                   if "DRIFT" in ln and "canonical" in ln]
    assert drift_lines, "no DRIFT example row found"
    for ln in drift_lines:
        assert "approved" not in ln, (
            "`approved` is canonical now; it must not appear as a DRIFT example")


def test_awaiting_human_approval_column_removed():
    # the dead label must no longer name an active board column: it may only
    # appear as a retired/fold note, never mapped to IN PROGRESS.
    in_progress_rows = [ln for ln in _text().splitlines()
                        if "IN PROGRESS |" in ln or "| IN PROGRESS" in ln]
    for ln in in_progress_rows:
        assert "awaiting_human_approval" not in ln, (
            "awaiting_human_approval must not map to the IN PROGRESS column")


def test_active_mode_covers_approved():
    # --active should surface approved plans (reviewed-and-ready) alongside in-progress
    text = _text()
    active_lines = [ln for ln in text.splitlines() if "--active" in ln]
    assert active_lines
    assert any("approved" in ln for ln in active_lines)
