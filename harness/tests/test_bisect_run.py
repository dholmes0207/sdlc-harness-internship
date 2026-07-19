"""bisect_run — automate `git bisect run` to pin the commit that introduced a regression.

Pure pieces (sha extraction, command plan) are unit-tested; one end-to-end test drives
the real script over a throwaway git repo with a planted regression and asserts it
identifies the first bad commit.
"""
import subprocess
import sys
from pathlib import Path

from conftest import _git  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "harness/plugins/hs/skills/debug/scripts/bisect_run.py"
sys.path.insert(0, str(_SCRIPT.parent))
import bisect_run as br  # noqa: E402


# ---- pure: first-bad extraction ------------------------------------------------

def test_parse_first_bad_extracts_sha():
    txt = ("Bisecting: 0 revisions left to test after this\n"
           "3f2a1b9c0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f80 is the first bad commit\n"
           "Author: someone\n    subject line\n")
    assert br.parse_first_bad(txt) == "3f2a1b9c0d4e5f6a7b8c9d0e1f2a3b4c5d6e7f80"


def test_parse_first_bad_none_when_absent():
    assert br.parse_first_bad("Bisecting: 3 revisions left to test\n") is None


def test_parse_first_bad_ignores_non_sha_lines():
    # a stray 'is the first bad commit' phrase without a leading 40-hex must not match
    assert br.parse_first_bad("note: this is the first bad commit message\n") is None


# ---- pure: command plan --------------------------------------------------------

def test_build_plan_shape():
    plan = br.build_plan("good1", "badHEAD", ["python", "-m", "pytest", "-q"])
    assert plan[0] == ["git", "bisect", "start", "badHEAD", "good1"]
    assert plan[1] == ["git", "bisect", "run", "python", "-m", "pytest", "-q"]
    assert plan[-1] == ["git", "bisect", "reset"]


# ---- arg handling --------------------------------------------------------------

def _run(args, cwd, timeout=60):
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          cwd=cwd, capture_output=True, text=True, timeout=timeout)


def test_missing_good_is_usage_error(tmp_path):
    out = _run(["--", "true"], tmp_path)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "good" in (out.stdout + out.stderr).lower()


def test_missing_test_command_is_usage_error(tmp_path):
    out = _run(["--good", "HEAD~3"], tmp_path)
    assert out.returncode == 2, out.stdout + out.stderr


def test_flag_with_no_value_is_usage_error(tmp_path):
    out = _run(["--good"], tmp_path)
    assert out.returncode == 2, out.stdout + out.stderr


# ---- end-to-end over a real git repo -------------------------------------------

def _build_repo(tmp_path):
    """A 4-commit history where val.txt crosses the regression threshold at commit 3.
    Returns (good_sha=c1, bad_introducing_sha=c3, head_sha=c4)."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@t.test")
    _git(tmp_path, "config", "user.name", "t")
    # the regression oracle: exit 0 (good) while val < 50, exit 1 (bad) once >= 50
    (tmp_path / "check.py").write_text(
        "import sys\n"
        "v = int(open('val.txt').read().strip())\n"
        "sys.exit(0 if v < 50 else 1)\n")
    _git(tmp_path, "add", "check.py")
    shas = []
    for val in (0, 1, 100, 101):  # commit 3 (val=100) is the first bad
        (tmp_path / "val.txt").write_text(str(val))
        _git(tmp_path, "add", "val.txt")
        _git(tmp_path, "commit", "-q", "-m", "set val=%d" % val)
        shas.append(subprocess.run(["git", "rev-parse", "HEAD"], cwd=tmp_path,
                                   capture_output=True, text=True).stdout.strip())
    return shas[0], shas[2], shas[3]


def test_end_to_end_finds_regression_commit(tmp_path):
    good, bad_intro, head = _build_repo(tmp_path)
    out = _run(["--good", good, "--bad", head, "--",
                sys.executable, "check.py"], tmp_path, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    # the tool prints the abbreviated %h sha — match its 7-char prefix
    assert bad_intro[:7] in out.stdout, out.stdout + out.stderr
    # bisect state must be cleaned up afterwards
    assert not (tmp_path / ".git" / "BISECT_LOG").exists(), "bisect not reset"


def test_dry_run_prints_plan_without_mutating(tmp_path):
    good, _bad_intro, head = _build_repo(tmp_path)
    out = _run(["--good", good, "--bad", head, "--dry-run", "--",
                sys.executable, "check.py"], tmp_path, timeout=60)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "git bisect start" in out.stdout
    assert not (tmp_path / ".git" / "BISECT_LOG").exists(), "dry-run must not start bisect"


def test_good_not_ancestor_of_bad_is_error(tmp_path):
    good, _bad_intro, head = _build_repo(tmp_path)
    # swap: ask to bisect with bad as the GOOD and an older commit as BAD -> not ancestor
    out = _run(["--good", head, "--bad", good, "--",
                sys.executable, "check.py"], tmp_path, timeout=60)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "ancestor" in (out.stdout + out.stderr).lower()
