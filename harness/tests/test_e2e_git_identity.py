"""test_e2e_git_identity.py — the e2e slices seed a fixture repo WITHOUT writing
a committer identity into .git/config (F4 isolation).

The AFK sandbox bind-mounts the host repo's .git read-write. A fixture builder
that runs `git config user.email ...` would persist that identity into the
bind-mounted host config — silently rewriting who the human is on their own
machine. The seed must instead pass identity per-command (`git -c user.x=...`),
so the seed commit is attributed but .git/config is never touched.
"""
import subprocess
import sys
from pathlib import Path

_E2E = Path(__file__).resolve().parent.parent / "e2e"
if str(_E2E) not in sys.path:
    sys.path.insert(0, str(_E2E))

import run_vertical_slice as vs  # noqa: E402


def _git(root, *args):
    return subprocess.run(["git", *args], cwd=str(root),
                          capture_output=True, text=True)


def test_seed_writes_no_identity_into_local_config(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "f.txt").write_text("seed\n", encoding="utf-8")
    vs.seed_git_repo(root, "fixture@local", "fixture-bot")
    # nothing persisted to .git/config — a bind-mounted host config stays clean
    assert _git(root, "config", "--local", "--get", "user.email").stdout.strip() == ""
    assert _git(root, "config", "--local", "--get", "user.name").stdout.strip() == ""


def test_seed_commit_is_still_attributed(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "f.txt").write_text("seed\n", encoding="utf-8")
    vs.seed_git_repo(root, "fixture@local", "fixture-bot")
    # the identity took effect on the commit (via -c), just not in config
    assert _git(root, "log", "-1", "--format=%ae").stdout.strip() == "fixture@local"
    assert _git(root, "log", "-1", "--format=%an").stdout.strip() == "fixture-bot"


def test_seed_produces_one_commit_on_a_real_repo(tmp_path):
    root = tmp_path / "proj"
    root.mkdir()
    (root / "f.txt").write_text("seed\n", encoding="utf-8")
    vs.seed_git_repo(root, "fixture@local", "fixture-bot")
    assert (root / ".git").is_dir()
    assert _git(root, "rev-list", "--count", "HEAD").stdout.strip() == "1"
