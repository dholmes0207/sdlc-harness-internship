"""test_pr_changed_plans.py — resolve the plan dirs a diff is the subject of.

Judge only dirs where the PR genuinely works on the plan; skip prunes, moved/deleted
plans, and not-yet-cooked (pending) plans."""
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "scripts"))
import pr_changed_plans as pcp  # noqa: E402


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True,
                   capture_output=True, text=True)


def _repo(tmp_path):
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@t")
    _git(r, "config", "user.name", "t")
    (r / "README.md").write_text("x\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


def _plan(r, name, status="in_progress"):
    d = r / "plans" / name / "artifacts"
    d.mkdir(parents=True)
    (r / "plans" / name / "plan.md").write_text(
        "---\ntitle: %s\nstatus: %s\n---\n\nbody\n" % (name, status), encoding="utf-8")
    (d / "verification.json").write_text('{"verdict":"PASS"}', encoding="utf-8")


def _base(r):
    return subprocess.run(["git", "-C", str(r), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def test_diff_maps_to_plan_dirs_dedup(tmp_path):
    r = _repo(tmp_path)
    base = _base(r)
    _plan(r, "260101-0000-a")
    _plan(r, "260101-0001-b")
    (r / "src.py").write_text("x\n", encoding="utf-8")  # a non-plan file
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "two plans")
    judge, _ = pcp.changed_plans(str(r), base, "HEAD")
    assert judge == ["260101-0000-a", "260101-0001-b"]


def test_reports_dir_excluded(tmp_path):
    r = _repo(tmp_path)
    base = _base(r)
    (r / "plans" / "reports").mkdir(parents=True)
    (r / "plans" / "reports" / "x-report.md").write_text("r\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "report")
    judge, _ = pcp.changed_plans(str(r), base, "HEAD")
    assert judge == []


def test_pending_plan_skipped_with_note(tmp_path):
    r = _repo(tmp_path)
    base = _base(r)
    _plan(r, "260101-0000-p", status="pending")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "pending plan")
    judge, skipped = pcp.changed_plans(str(r), base, "HEAD")
    assert judge == []
    assert any(s["dir"] == "260101-0000-p" and "pending" in s["reason"] for s in skipped)


def test_deletion_only_dir_skipped(tmp_path):
    r = _repo(tmp_path)
    _plan(r, "260101-0000-c", status="completed")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "add plan")
    base = _base(r)
    # prune only the artifacts (all deletions under the dir), keep plan.md
    (r / "plans" / "260101-0000-c" / "artifacts" / "verification.json").unlink()
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "prune artifacts")
    judge, skipped = pcp.changed_plans(str(r), base, "HEAD")
    assert "260101-0000-c" not in judge
    assert any("deletion-only" in s["reason"] for s in skipped)


def test_plan_md_absent_in_head_skipped(tmp_path):
    r = _repo(tmp_path)
    _plan(r, "260101-0000-d")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "add plan")
    base = _base(r)
    _git(r, "rm", "-rq", "plans/260101-0000-d")
    _git(r, "commit", "-qm", "remove plan")
    judge, skipped = pcp.changed_plans(str(r), base, "HEAD")
    assert "260101-0000-d" not in judge


def test_in_progress_deletion_is_judged_not_skipped(tmp_path):
    # A LIVE (in_progress) plan losing a receipt must NOT be swallowed by the
    # deletion-only skip — it is judged (and will block). Only a COMPLETED plan's
    # prune is skipped.
    r = _repo(tmp_path)
    _plan(r, "260101-0000-live", status="in_progress")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "add live plan")
    base = _base(r)
    (r / "plans" / "260101-0000-live" / "artifacts" / "verification.json").unlink()
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "prune a live plan's receipt")
    judge, _ = pcp.changed_plans(str(r), base, "HEAD")
    assert "260101-0000-live" in judge  # judged, not skipped


def test_git_error_fails_closed(tmp_path):
    # A bad base sha must FAIL-CLOSED (GitError / exit 2), never resolve an empty set.
    r = _repo(tmp_path)
    import pytest
    with pytest.raises(pcp.GitError):
        pcp.changed_plans(str(r), "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef", "HEAD")
    rc = pcp.main(["--base", "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                   "--head", "HEAD", "--root", str(r)])
    assert rc == 2


def test_no_plans_touched_empty(tmp_path):
    r = _repo(tmp_path)
    base = _base(r)
    (r / "only.py").write_text("x\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "code only")
    judge, _ = pcp.changed_plans(str(r), base, "HEAD")
    assert judge == []


def test_dotdotdot_closed_frontmatter_is_not_skipped(tmp_path):
    # plan_status supports a `...` frontmatter close as well as `---`. The
    # receipts gate must not fail-open (silently skip) an in_progress plan just
    # because its frontmatter closed with `...` — that plan must still be judged.
    r = _repo(tmp_path)
    base = _base(r)
    d = r / "plans" / "260101-0000-dots" / "artifacts"
    d.mkdir(parents=True)
    (r / "plans" / "260101-0000-dots" / "plan.md").write_text(
        "---\ntitle: dots\nstatus: in_progress\n...\n\nbody\n", encoding="utf-8")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "dot-closed plan")
    judge, skipped = pcp.changed_plans(str(r), base, "HEAD")
    assert "260101-0000-dots" in judge
    assert not any(s["dir"] == "260101-0000-dots" for s in skipped)


def test_git_timeout_fails_closed(tmp_path, monkeypatch):
    # A hung git command on the gate's diff path must FAIL-CLOSED (GitError),
    # never resolve an empty plan set that turns the gate green.
    import pytest
    r = _repo(tmp_path)
    base = _base(r)

    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="git", timeout=60)

    monkeypatch.setattr(pcp.subprocess, "run", _timeout)
    with pytest.raises(pcp.GitError):
        pcp.changed_plans(str(r), base, "HEAD")
