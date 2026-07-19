"""test_open_plan.py — open_plan flips a starting plan's frontmatter status to
in_progress, surgically and idempotently.

The open is the deterministic FIRST step of cook, the mirror of close_plan's
finalize. Without it a freshly created plan stays draft/pending, and the gate's
active-plan resolver (artifact_check.resolve_active_plan returns ONLY an
in_progress plan) never binds to the plan being cooked — its verification/review
gates are silently skipped, or the gate latches onto a stale in_progress plan
from another session. open_plan removes that failure mode, and unlike close it
fails LOUD when it cannot reach in_progress, so cook halts instead of cooking an
unresolvable plan.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import open_plan as op  # noqa: E402

_FM = (
    "---\n"
    "title: %s\n"
    "status: %s\n"
    "author: user:a@x\n"
    "---\n\n"
    "# Body\n\n"
    "An example block shows `status: completed` in prose — must NOT change.\n"
)


def _mk_plan(root: Path, name="260621-2000-demo", status="draft"):
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(_FM % (name, status), encoding="utf-8")
    return d


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    return tmp_path


def test_flips_draft_to_in_progress(root):
    d = _mk_plan(root, status="draft")
    res = op.open_plan(d, root=root)
    assert res.changed is True
    assert res.ok is True
    text = (d / "plan.md").read_text(encoding="utf-8")
    assert "status: in_progress\n" in text
    assert "status: draft\n" not in text


def test_flips_pending_to_in_progress(root):
    d = _mk_plan(root, status="pending")
    res = op.open_plan(d, root=root)
    assert res.changed is True
    assert "status: in_progress\n" in (d / "plan.md").read_text(encoding="utf-8")


def test_flips_approved_to_in_progress(root):
    # `approved` is the post-approval ready-to-cook state; cook opens it directly
    d = _mk_plan(root, status="approved")
    res = op.open_plan(d, root=root)
    assert res.changed is True
    assert "status: in_progress\n" in (d / "plan.md").read_text(encoding="utf-8")


def test_idempotent_when_already_in_progress(root):
    d = _mk_plan(root, status="in_progress")
    res = op.open_plan(d, root=root)
    assert res.changed is False
    assert res.ok is True  # no-op is success, not failure
    assert "status: in_progress\n" in (d / "plan.md").read_text(encoding="utf-8")


def test_completed_is_not_reopened_and_fails_loud(root):
    """open must NOT silently no-op a completed plan: reopening finished work is
    a mistake. It fails (ok=False) so cook halts loudly instead of cooking a
    plan the resolver will never see as active."""
    d = _mk_plan(root, status="completed")
    res = op.open_plan(d, root=root)
    assert res.ok is False
    assert res.changed is False
    assert "status: completed\n" in (d / "plan.md").read_text(encoding="utf-8")


def test_unknown_status_fails_loud(root):
    d = _mk_plan(root, status="cancelled")
    res = op.open_plan(d, root=root)
    assert res.ok is False
    assert res.changed is False
    assert "status: cancelled\n" in (d / "plan.md").read_text(encoding="utf-8")


def test_body_status_mention_untouched(root):
    d = _mk_plan(root, status="draft")
    op.open_plan(d, root=root)
    text = (d / "plan.md").read_text(encoding="utf-8")
    assert "`status: completed` in prose" in text
    assert text.count("status: in_progress") == 1


def test_preserves_everything_but_status(root):
    d = _mk_plan(root, status="pending")
    before = (d / "plan.md").read_text(encoding="utf-8")
    op.open_plan(d, root=root)
    after = (d / "plan.md").read_text(encoding="utf-8")
    assert after == before.replace("status: pending\nauthor",
                                   "status: in_progress\nauthor", 1)


def test_rejects_plan_dir_outside_plans(root, tmp_path):
    outside = tmp_path / "not_plans" / "x"
    outside.mkdir(parents=True)
    (outside / "plan.md").write_text(_FM % ("x", "draft"), encoding="utf-8")
    res = op.open_plan(outside, root=root)
    assert res.ok is False
    assert "status: draft" in (outside / "plan.md").read_text(encoding="utf-8")


def test_missing_plan_md_is_error(root):
    d = root / "plans" / "260621-2001-empty"
    d.mkdir(parents=True)
    res = op.open_plan(d, root=root)
    assert res.ok is False
    assert res.changed is False


def test_no_status_line_is_error_not_corruption(root):
    d = root / "plans" / "260621-2002-nostatus"
    d.mkdir(parents=True)
    raw = "---\ntitle: x\nauthor: user:a@x\n---\n\n# Body\n"
    (d / "plan.md").write_text(raw, encoding="utf-8")
    res = op.open_plan(d, root=root)
    assert res.ok is False
    assert (d / "plan.md").read_text(encoding="utf-8") == raw  # untouched


def test_cli_smoke_flips(root):
    d = _mk_plan(root, status="draft")
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "open_plan.py"), str(d)],
        capture_output=True, text=True, env={"HARNESS_ROOT": str(root),
                                             "PATH": ""})
    assert out.returncode == 0, out.stderr
    assert "status: in_progress\n" in (d / "plan.md").read_text(encoding="utf-8")


def test_cli_exit1_on_completed(root):
    d = _mk_plan(root, status="completed")
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "open_plan.py"), str(d)],
        capture_output=True, text=True, env={"HARNESS_ROOT": str(root),
                                             "PATH": ""})
    assert out.returncode == 1
