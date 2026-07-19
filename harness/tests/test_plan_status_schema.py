"""test_plan_status_schema.py — the canonical status vocabulary.

Before this, the status field had no schema: distinct values drifted across the
plan corpus (`done`, `implemented`, dash-vs-underscore variants, a missing
field), and the kanban reader silently mis-bucketed everything it did not
recognise. The schema declares the only valid values — pending, approved,
in_progress, completed, cancelled — and folds the lossless spelling variants. The
retired labels `draft`/`awaiting_human_approval` fold to `pending` for consumers
but are no longer canonical. It deliberately does NOT guess a semantic mapping
for an off-vocabulary value (e.g. `done`) — that is reconcile's evidence-backed
job.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plan_status as ps  # noqa: E402


def test_normalize_folds_dash_to_underscore():
    # the dash variant that broke the kanban match is the same state
    assert ps.normalize_status("in-progress") == "in_progress"
    # the retired label's dash spelling folds all the way to pending
    assert ps.normalize_status("awaiting-human-approval") == "pending"


def test_normalize_strips_quotes_and_whitespace():
    assert ps.normalize_status("  'completed' ") == "completed"
    assert ps.normalize_status('"pending"') == "pending"


def test_normalize_returns_none_for_off_vocabulary():
    # these are real drift values seen in the corpus — NOT silently remapped
    assert ps.normalize_status("done") is None
    assert ps.normalize_status("implemented") is None
    assert ps.normalize_status("") is None
    assert ps.normalize_status(None) is None


def test_retired_labels_fold_to_pending():
    # draft/awaiting are retired: a folded home (pending) but not canonical
    assert ps.normalize_status("draft") == "pending"
    assert ps.normalize_status("awaiting_human_approval") == "pending"


def test_is_canonical():
    assert ps.is_canonical("completed") is True
    assert ps.is_canonical("approved") is True
    # an off-vocab value is not canonical even though it has obvious intent
    assert ps.is_canonical("done") is False
    # retired labels are not canonical (they only have a folded home)
    assert ps.is_canonical("draft") is False
    assert ps.is_canonical("awaiting-human-approval") is False  # raw dash form
