"""Carry untracked harness config into a fresh worktree.

`git worktree add` shares tracked files through the common git-dir but never
carries untracked / gitignored content. A per-project install can keep
`harness/` and `.claude/` untracked in the host repo, so a new worktree would
start without them. carry_untracked.py copies each carry entry into the
worktree ONLY when git cannot (the entry has no tracked files), so a
tracked `harness/` (this dogfood repo) is left to git and never re-dragged
alongside its ignored `state/`/pycache noise.
"""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "plugins/hs/skills/worktree/scripts/carry_untracked.py"
)


def _git(cwd, *args):
    subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


@pytest.fixture
def repo(tmp_path):
    """A throwaway repo: harness/ tracked, .claude/ + .harness-dev/ ignored."""
    src = tmp_path / "src"
    src.mkdir()
    _git(src, "init", "-q")
    _git(src, "config", "user.email", "t@t")
    _git(src, "config", "user.name", "t")

    # tracked harness/ — git carries this; helper must NOT copy it
    (src / "harness").mkdir()
    (src / "harness" / "core.py").write_text("tracked\n")
    # ignored runtime junk under the tracked dir — must never be dragged
    (src / "harness" / "state").mkdir()
    (src / "harness" / "state" / "run.jsonl").write_text("noise\n")

    # ignored config dirs present in any host repo / this dogfood repo
    (src / ".claude").mkdir()
    (src / ".claude" / "settings.json").write_text("{}\n")
    (src / ".harness-dev").mkdir()
    (src / ".harness-dev" / "output.yaml").write_text("language: vi\n")

    (src / ".gitignore").write_text(
        "/.claude/\n/.harness-dev/\nharness/state/\n"
    )
    _git(src, "add", "harness/core.py", ".gitignore")
    _git(src, "commit", "-q", "-m", "init")

    dest = tmp_path / "wt"  # simulates the freshly-added worktree
    dest.mkdir()
    return src, dest


def _run(src, dest, carry=None, env_carry=None, dry_run=False):
    env = dict(os.environ)
    env.pop("HARNESS_WORKTREE_CARRY", None)
    if env_carry is not None:
        env["HARNESS_WORKTREE_CARRY"] = env_carry
    cmd = [sys.executable, str(SCRIPT), "--source", str(src), "--dest", str(dest)]
    if carry is not None:
        cmd += ["--carry", carry]
    if dry_run:
        cmd += ["--dry-run"]
    return subprocess.run(cmd, env=env, capture_output=True, text=True)


def test_shipped_default_copies_claude_skips_tracked_harness(repo):
    src, dest = repo
    r = _run(src, dest)  # no --carry, no env → shipped default harness:.claude
    assert r.returncode == 0, r.stderr

    # .claude is fully ignored → git cannot carry it → copied
    assert (dest / ".claude" / "settings.json").read_text() == "{}\n"
    # harness/ has tracked files → git carries it → helper leaves it alone,
    # and must NOT drag the ignored harness/state noise
    assert not (dest / "harness").exists()
    # .harness-dev is not in the shipped default
    assert not (dest / ".harness-dev").exists()


def test_env_appends_harness_dev_for_dogfood(repo):
    src, dest = repo
    r = _run(src, dest, env_carry="harness:.claude:.harness-dev")
    assert r.returncode == 0, r.stderr
    assert (dest / ".claude" / "settings.json").exists()
    assert (dest / ".harness-dev" / "output.yaml").read_text() == "language: vi\n"


def test_flag_overrides_env(repo):
    src, dest = repo
    # explicit --carry wins over env (which would also add .harness-dev)
    r = _run(src, dest, carry=".claude", env_carry="harness:.claude:.harness-dev")
    assert r.returncode == 0, r.stderr
    assert (dest / ".claude").exists()
    assert not (dest / ".harness-dev").exists()


def test_fully_untracked_dir_is_copied(repo):
    # end-user repo where harness/ itself is untracked (never committed)
    src, dest = repo
    shutil.rmtree(src / "harness")
    _git(src, "rm", "-q", "-r", "--cached", "harness")  # drop from the index too
    (src / "harness").mkdir()
    (src / "harness" / "hook.py").write_text("installed\n")  # untracked, not ignored
    r = _run(src, dest, carry="harness")
    assert r.returncode == 0, r.stderr
    assert (dest / "harness" / "hook.py").read_text() == "installed\n"


def test_missing_entry_is_skipped_silently(repo):
    src, dest = repo
    r = _run(src, dest, carry="does-not-exist")
    assert r.returncode == 0, r.stderr
    assert not (dest / "does-not-exist").exists()


def test_path_traversal_entry_refused(repo):
    src, dest = repo
    secret = dest.parent / "outside.txt"
    secret.write_text("SECRET\n")
    r = _run(src, dest, carry="../outside.txt")
    # refused, never copied; helper stays advisory (exit 0)
    assert r.returncode == 0, r.stderr
    assert not (dest / "outside.txt").exists()
    assert not (dest / ".." / "outside.txt").exists() or (secret).read_text() == "SECRET\n"


def test_absolute_entry_refused(repo):
    src, dest = repo
    r = _run(src, dest, carry="/etc")
    assert r.returncode == 0, r.stderr
    assert not (dest / "etc").exists()


def test_dry_run_copies_nothing(repo):
    src, dest = repo
    r = _run(src, dest, dry_run=True)
    assert r.returncode == 0, r.stderr
    assert not (dest / ".claude").exists()
    assert "carry" in r.stdout.lower()
