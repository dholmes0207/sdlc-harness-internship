"""test_close_plan.py — close_plan flips a finished plan's frontmatter status
from in_progress to completed, surgically and idempotently.

The close is the deterministic replacement for cook's hand-edit finalize step:
a plan left in_progress after cook keeps the gate's active-plan resolver pinned
to a stale plan (artifact_check.resolve_active_plan only ever returns an
in_progress plan), so a forgotten close blocks unrelated shipping. close_plan
removes that failure mode without touching anything but the status value.
"""
import subprocess
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import close_plan as cp  # noqa: E402

_FM = (
    "---\n"
    "title: %s\n"
    "status: %s\n"
    "author: user:a@x\n"
    "---\n\n"
    "# Body\n\n"
    "An example block shows `status: in_progress` in prose — must NOT change.\n"
)


def _mk_plan(root: Path, name="260621-2000-demo", status="in_progress"):
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(_FM % (name, status), encoding="utf-8")
    return d


@pytest.fixture()
def root(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    return tmp_path


def test_flips_in_progress_to_completed(root):
    d = _mk_plan(root, status="in_progress")
    res = cp.close_plan(d, root=root)
    assert res.changed is True
    text = (d / "plan.md").read_text(encoding="utf-8")
    assert "status: completed\n" in text
    assert "status: in_progress\nauthor" not in text


def test_idempotent_when_already_completed(root):
    d = _mk_plan(root, status="completed")
    res = cp.close_plan(d, root=root)
    assert res.changed is False
    assert res.ok is True  # no-op is success, not failure
    assert "status: completed\n" in (d / "plan.md").read_text(encoding="utf-8")


def test_body_status_mention_untouched(root):
    d = _mk_plan(root, status="in_progress")
    cp.close_plan(d, root=root)
    text = (d / "plan.md").read_text(encoding="utf-8")
    # the prose line that quotes status stays verbatim
    assert "`status: in_progress` in prose" in text
    # exactly one frontmatter status line, now completed
    assert text.count("status: completed") == 1


def test_preserves_everything_but_status(root):
    d = _mk_plan(root, status="in_progress")
    before = (d / "plan.md").read_text(encoding="utf-8")
    cp.close_plan(d, root=root)
    after = (d / "plan.md").read_text(encoding="utf-8")
    assert after == before.replace("status: in_progress\nauthor",
                                   "status: completed\nauthor", 1)


def test_rejects_plan_dir_outside_plans(root, tmp_path):
    outside = tmp_path / "not_plans" / "x"
    outside.mkdir(parents=True)
    (outside / "plan.md").write_text(_FM % ("x", "in_progress"), encoding="utf-8")
    res = cp.close_plan(outside, root=root)
    assert res.ok is False
    # the stray file is NOT mutated
    assert "status: in_progress" in (outside / "plan.md").read_text(encoding="utf-8")


def test_missing_plan_md_is_error(root):
    d = root / "plans" / "260621-2001-empty"
    d.mkdir(parents=True)
    res = cp.close_plan(d, root=root)
    assert res.ok is False
    assert res.changed is False


def test_no_status_line_is_error_not_corruption(root):
    d = root / "plans" / "260621-2002-nostatus"
    d.mkdir(parents=True)
    raw = "---\ntitle: x\nauthor: user:a@x\n---\n\n# Body\n"
    (d / "plan.md").write_text(raw, encoding="utf-8")
    res = cp.close_plan(d, root=root)
    assert res.ok is False
    assert (d / "plan.md").read_text(encoding="utf-8") == raw  # untouched


def test_pending_is_not_force_completed(root):
    """close only finalizes a plan that was actually running; a pending plan
    (never started) is left alone so a mis-aimed close cannot mark unstarted
    work done."""
    d = _mk_plan(root, status="pending")
    res = cp.close_plan(d, root=root)
    assert res.changed is False
    assert "status: pending\n" in (d / "plan.md").read_text(encoding="utf-8")


def test_cli_smoke(root):
    d = _mk_plan(root, status="in_progress")
    out = subprocess.run(
        [sys.executable, str(_SCRIPTS / "close_plan.py"), str(d)],
        capture_output=True, text=True, env={"HARNESS_ROOT": str(root),
                                             "PATH": ""})
    assert out.returncode == 0, out.stderr
    assert "status: completed\n" in (d / "plan.md").read_text(encoding="utf-8")
