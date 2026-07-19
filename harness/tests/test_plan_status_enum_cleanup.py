"""test_plan_status_enum_cleanup.py — the cleaned-up status vocabulary.

The lifecycle had two redundant labels: `draft` was an exact synonym of
`pending` (no code ever branched on the difference) and `awaiting_human_approval`
was a dead label no automation ever set. This swaps them for one honest
`approved` state and retires the other two:

  canonical = (pending, approved, in_progress, completed, cancelled)

`draft`/`awaiting_human_approval` (and their dash spellings) are RETIRED: folded
to `pending` for every consumer (no board-drift window) yet dropped from the
canonical set, so `is_canonical` reports them False and reconcile can migrate
them by evidence (an APPROVED artifact lifts a retired plan to `approved`, not a
blind fold to `pending`).
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import plan_status as ps  # noqa: E402
import reconcile_plan_status as rc  # noqa: E402


# --- the enum itself ---------------------------------------------------------

def test_canonical_set_is_the_five_lifecycle_states():
    assert ps.CANONICAL_STATUSES == (
        "pending", "approved", "in_progress", "completed", "cancelled",
    )


def test_draft_and_awaiting_are_gone_from_canonical():
    assert "draft" not in ps.CANONICAL_STATUSES
    assert "awaiting_human_approval" not in ps.CANONICAL_STATUSES


def test_approved_is_canonical():
    assert ps.is_canonical("approved") is True
    assert ps.normalize_status("approved") == "approved"


# --- legacy fold: retired labels normalize to pending, but are NOT canonical -

def test_retired_labels_fold_to_pending():
    assert ps.normalize_status("draft") == "pending"
    assert ps.normalize_status("awaiting_human_approval") == "pending"
    # dash spelling of the retired label folds too
    assert ps.normalize_status("awaiting-human-approval") == "pending"


def test_retired_labels_are_not_canonical():
    # they have a folded home, but the raw label is no longer a valid value
    assert ps.is_canonical("draft") is False
    assert ps.is_canonical("awaiting_human_approval") is False


def test_offvocab_still_returns_none():
    assert ps.normalize_status("done") is None
    assert ps.normalize_status("") is None
    assert ps.normalize_status(None) is None


# --- reconcile: retired drift class, evidence-aware suggestion ---------------

_FM = "---\ntitle: %s\nstatus: %s\nauthor: user:a@x\n---\n\n# Body\n"
_APPROVAL = "schema: plan-approval/v1\nplan: %s\nverdict: %s\n"


def _mk(root, name, status, *, verdict=None, approval=None):
    d = root / "plans" / name
    (d / "artifacts").mkdir(parents=True)
    (d / "plan.md").write_text(_FM % (name, status), encoding="utf-8")
    if verdict is not None:
        (d / "artifacts" / "verification.json").write_text(
            json.dumps({"verdict": verdict}), encoding="utf-8")
    if approval is not None:
        (d / "artifacts" / "plan-approval.yaml").write_text(
            _APPROVAL % (name, approval), encoding="utf-8")
    return d


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    (tmp_path / "plans").mkdir()
    return tmp_path


def _by_name(states, name):
    return next(s for s in states if s.name == name)


def test_awaiting_with_approved_artifact_migrates_to_approved(root):
    # the F1 case: blind-folding a verified-approved plan to pending would erase
    # its approval — reconcile must read the artifact and migrate to `approved`.
    _mk(root, "260101-0001-appr", status="awaiting_human_approval",
        approval="APPROVED")
    s = _by_name(rc.scan(root), "260101-0001-appr")
    assert s.drift == "retired"
    assert s.suggestion == "approved"


def test_draft_without_approval_migrates_to_pending(root):
    _mk(root, "260101-0002-draft", status="draft")
    s = _by_name(rc.scan(root), "260101-0002-draft")
    assert s.drift == "retired"
    assert s.suggestion == "pending"


def test_awaiting_without_approval_migrates_to_pending(root):
    # a retired label whose approval is absent (or rejected) folds to pending
    _mk(root, "260101-0003-noappr", status="awaiting_human_approval",
        approval="REJECTED")
    s = _by_name(rc.scan(root), "260101-0003-noappr")
    assert s.drift == "retired"
    assert s.suggestion == "pending"


def test_apply_fixes_migrates_retired_labels(root):
    appr = _mk(root, "260101-0004-appr", status="awaiting_human_approval",
               approval="APPROVED")
    draft = _mk(root, "260101-0005-draft", status="draft")
    n = rc.apply_fixes(rc.scan(root), root=root)
    assert n == 2
    assert "status: approved\n" in (appr / "plan.md").read_text()
    assert "status: pending\n" in (draft / "plan.md").read_text()
