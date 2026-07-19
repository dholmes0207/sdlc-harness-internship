"""test_reconcile_plan_status.py — detect and (conservatively) repair the drift
between a plan's declared status and the evidence of what actually happened.

The scan is deliberately split into two confidence tiers:

  - a LOSSLESS spelling fix (`in-progress` -> the underscore form) is safe to
    apply automatically: same state, different spelling.
  - a RETIRED label (`draft`/`awaiting_human_approval`) migrates by evidence —
    an APPROVED artifact lifts it to `approved`, otherwise it folds to `pending`.
  - an EVIDENCED completion (a `pending` plan that carries a PASS verification
    artifact) is reported but NOT auto-applied by default, because marking work
    done is exactly the call the repo refuses to make silently.

Off-vocabulary values (`done`, `implemented`) and a missing field are surfaced
for a human decision; reconcile never guesses their intent from spelling alone.
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import reconcile_plan_status as rc  # noqa: E402

_FM = "---\ntitle: %s\nstatus: %s\nauthor: user:a@x\n---\n\n# Body\n"
_FM_NOSTATUS = "---\ntitle: %s\nauthor: user:a@x\n---\n\n# Body\n"


def _mk(root, name, status="pending", *, verdict=None, nostatus=False):
    d = root / "plans" / name
    (d / "artifacts").mkdir(parents=True)
    body = _FM_NOSTATUS % name if nostatus else _FM % (name, status)
    (d / "plan.md").write_text(body, encoding="utf-8")
    if verdict is not None:
        (d / "artifacts" / "verification.json").write_text(
            json.dumps({"verdict": verdict}), encoding="utf-8")
    return d


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    (tmp_path / "plans").mkdir()
    return tmp_path


def _by_name(states, name):
    return next(s for s in states if s.name == name)


def test_canonical_consistent_plan_is_ok(root):
    _mk(root, "260101-0000-clean", status="completed")
    s = _by_name(rc.scan(root), "260101-0000-clean")
    assert s.drift == "ok"


def test_dash_variant_is_a_lossless_spelling_fix(root):
    _mk(root, "260101-0001-dash", status="in-progress")
    s = _by_name(rc.scan(root), "260101-0001-dash")
    assert s.drift == "spelling"
    assert s.suggestion == "in_progress"


def test_retired_label_is_evidence_migrated(root):
    # a retired label with no approval folds to pending (not a spelling fix)
    _mk(root, "260101-0001-retired", status="awaiting-human-approval")
    s = _by_name(rc.scan(root), "260101-0001-retired")
    assert s.drift == "retired"
    assert s.suggestion == "pending"


def test_pending_with_pass_is_under_reported(root):
    _mk(root, "260101-0002-shipped", status="pending", verdict="PASS")
    s = _by_name(rc.scan(root), "260101-0002-shipped")
    assert s.drift == "under_reported"
    assert s.has_pass is True


def test_offvocab_value_needs_human(root):
    _mk(root, "260101-0003-done", status="done")
    s = _by_name(rc.scan(root), "260101-0003-done")
    assert s.drift == "vocab"
    assert s.suggestion is None  # never guessed from spelling


def test_missing_status_field(root):
    _mk(root, "260101-0004-nofield", nostatus=True)
    s = _by_name(rc.scan(root), "260101-0004-nofield")
    assert s.drift == "missing"


def test_apply_fixes_only_repairs_spelling_by_default(root):
    dash = _mk(root, "260101-0006-dash", status="in-progress")
    ship = _mk(root, "260101-0007-ship", status="pending", verdict="PASS")
    n = rc.apply_fixes(rc.scan(root), root=root)
    assert n == 1  # only the spelling fix
    assert "status: in_progress\n" in (dash / "plan.md").read_text()
    # the evidenced completion is left for a human — NOT auto-flipped
    assert "status: pending\n" in (ship / "plan.md").read_text()
