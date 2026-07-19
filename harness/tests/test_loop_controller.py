"""test_loop_controller.py — commit-wise progress for the native AFK loop.

The AFK loop contract is commit-per-iteration: a healthy iteration ends by
committing its work. Measuring progress from the DIRTY working tree
(`git diff HEAD`) therefore reads a freshly-committed iteration as "no progress"
(the tree is clean again) and false-trips the stagnation breaker. Progress must
instead be measured commit-wise: HEAD advanced since the previous iteration.

These tests drive a real temp git repo so the commit-vs-clean distinction is
exercised end-to-end, not mocked.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "afk"))
import loop_controller as lc  # noqa: E402


def _git(root, *args):
    # Identity passed per-command so the fixture never writes .git/config (the
    # same isolation the e2e seed uses for a bind-mounted host repo).
    return subprocess.run(
        ["git", "-c", "user.email=fixture@local", "-c", "user.name=fixture",
         *args],
        cwd=str(root), capture_output=True, text=True, check=True)


def _init_repo(root):
    root.mkdir(parents=True, exist_ok=True)
    _git(root, "init", "-q")
    (root / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "seed")


def _commit(root, name, body):
    (root / name).write_text(body, encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", name)


def test_head_sha_reads_current_commit(tmp_path):
    repo = tmp_path / "repo"
    _init_repo(repo)
    head = lc._git_head(repo)
    assert head, "HEAD on a repo with a commit must be a non-empty sha"
    _commit(repo, "a.txt", "a\n")
    assert lc._git_head(repo) != head, "a new commit must change HEAD"


def test_head_sha_empty_on_non_repo(tmp_path):
    # A directory that is not a git repo must yield "" (progress probe never raises).
    plain = tmp_path / "plain"
    plain.mkdir()
    assert lc._git_head(plain) == ""


def test_committed_iteration_counts_as_progress(tmp_path):
    # The core fix: an iteration that COMMITS its work (clean tree afterward) must
    # read as progress, even though `git diff HEAD` is empty.
    repo = tmp_path / "repo"
    _init_repo(repo)
    before = lc._git_head(repo)
    _commit(repo, "feature.txt", "work\n")
    after = lc._git_head(repo)
    assert lc._committed_progress(before, after) is True


def test_unchanged_head_counts_as_no_progress(tmp_path):
    # An iteration that did NOT commit (HEAD unchanged) is no-progress, regardless
    # of the working tree — the breaker should advance toward OPEN.
    repo = tmp_path / "repo"
    _init_repo(repo)
    head = lc._git_head(repo)
    assert lc._committed_progress(head, head) is False


def test_committed_progress_first_iteration_with_no_prior_head(tmp_path):
    # On the very first iteration there is no prior HEAD to compare to. A commit
    # produced this iteration (prior "" -> a real sha) is still progress.
    repo = tmp_path / "repo"
    _init_repo(repo)
    after = lc._git_head(repo)
    assert lc._committed_progress("", after) is True
    # but "" -> "" (no repo / no commit either side) is not progress
    assert lc._committed_progress("", "") is False


def test_live_invoker_reports_progress_only_after_a_commit(tmp_path, monkeypatch):
    # End-to-end through the live invoker: it must report diff_count > 0 on the
    # iteration whose HEAD advanced, and 0 on an idle iteration that neither
    # committed nor touched the tree — proving progress is tracked commit-wise
    # across calls, so a freshly-committed (clean-tree) turn still counts.
    import subprocess as _sp
    repo = tmp_path / "repo"
    _init_repo(repo)

    class _Proc:
        returncode = 0
        stdout = "ok"

    # Fake ONLY the Claude invocation; let git subprocess calls run for real so
    # HEAD genuinely advances. The first argv token is the binary: "git" → real,
    # anything else (the claude bin) → the fake model turn.
    real_run = _sp.run

    def _selective_run(argv, *a, **k):
        if argv and argv[0] == "git":
            return real_run(argv, *a, **k)
        return _Proc()

    monkeypatch.setattr(_sp, "run", _selective_run)
    invoke = lc.make_claude_invoker("plan", repo)

    # Iteration 1: a commit lands (HEAD advances from seed).
    _commit(repo, "i1.txt", "one\n")
    r1 = invoke(1, None)
    assert r1.diff_count > 0, "an iteration that advanced HEAD must read as progress"

    # Iteration 2: an idle turn — HEAD unchanged and the tree left clean. This is
    # the genuine no-progress case the stagnation breaker must still see.
    r2 = invoke(2, None)
    assert r2.diff_count == 0, "HEAD unchanged + clean tree => no progress"
