"""test_resolver_excludes_approved.py — the new `approved` status stays INVISIBLE
to the gate's active-plan resolver, preserving the DEC-158 invariant that only an
`in_progress` plan is ever 'active'.

Adding a third status-writer (plan_approval flips pending->approved) only widens
the set of who may write status; it must not widen what the gate treats as the
active plan. An approved-but-not-yet-cooked plan must gate exactly like the old
pending/draft did — present on the board, but not resolved as active — so it
cannot accidentally clear or block a hard stage before cook opens it.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import artifact_check as ac  # noqa: E402


def _mk(root: Path, name, status):
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: %s\nstatus: %s\n---\n\n# Body\n" % (name, status),
        encoding="utf-8")
    return d


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.delenv("HARNESS_ACTIVE_PLAN", raising=False)
    (tmp_path / "plans").mkdir()
    return tmp_path


def test_approved_plan_is_not_active(root):
    _mk(root, "260101-0000-appr", status="approved")
    assert ac.resolve_active_plan(root) is None


def test_in_progress_resolves_over_approved(root):
    # an approved plan sits alongside an in_progress one; only the in_progress
    # plan is the active one — the approved plan never shadows or competes.
    _mk(root, "260101-0001-appr", status="approved")
    ip = _mk(root, "260101-0002-inprog", status="in_progress")
    assert ac.resolve_active_plan(root) == ip


def test_approved_does_not_trigger_ambiguity(root):
    # two approved plans + one in_progress: the resolver must NOT see the two
    # approved as competing actives (that would wrongly trip the ambiguity guard).
    _mk(root, "260101-0001-appr-a", status="approved")
    _mk(root, "260101-0002-appr-b", status="approved")
    ip = _mk(root, "260101-0003-inprog", status="in_progress")
    assert ac.resolve_active_plan(root) == ip
