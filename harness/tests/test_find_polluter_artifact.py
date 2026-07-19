"""find_polluter --artifact — bisect to the test that creates a filesystem artifact.

The default mode finds a test that flips a TARGET test from pass to fail (leaked
in-process state). This harvested mode finds a different class the target-flip
mode is blind to: a test that dirties the working tree — creates an unwanted
file or directory — even when no other test fails because of it.

It keeps the pytest-native O(log n) bisection (the upstream bash original scanned
O(n) and hard-coded `npm test`). Because the bisection must reset the artifact
between probes, resetting is gated: it refuses to delete `.git`, a git-tracked
path, a symlink, or anything resolving outside the working tree — a reset must
only ever remove genuine test pollution, never a real repo file.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "harness/plugins/hs/skills/debug/scripts/find_polluter.py"
sys.path.insert(0, str(_SCRIPT.parent))
import find_polluter as fp  # noqa: E402


def _project(tmp_path):
    """A 3-test suite where the MIDDLE test dirties the tree with `junk.txt`."""
    (tmp_path / "test_dirty.py").write_text(
        "from pathlib import Path\n"
        "def test_aaa_clean():\n    assert True\n"
        "def test_bbb_dirties():\n    Path('junk.txt').write_text('x')\n    assert True\n"
        "def test_ccc_clean():\n    assert True\n")


def test_artifact_mode_finds_the_creator(tmp_path):
    _project(tmp_path)
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "--artifact", "junk.txt"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "test_bbb_dirties" in out.stdout, out.stdout + out.stderr
    assert "POLLUTER" in out.stdout


def test_artifact_mode_no_creator(tmp_path):
    (tmp_path / "test_clean.py").write_text(
        "def test_a():\n    assert True\n"
        "def test_b():\n    assert True\n")
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "--artifact", "junk.txt"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "no test creates" in out.stdout.lower()


def test_reset_guard_refuses_git_dir(tmp_path):
    ok, reason = fp._reset_safe(".git", root=tmp_path)
    assert ok is False and "git" in reason.lower()


def test_reset_guard_refuses_symlink(tmp_path):
    target = tmp_path / "real"
    target.write_text("keep")
    link = tmp_path / "link"
    link.symlink_to(target)
    ok, reason = fp._reset_safe(str(link), root=tmp_path)
    assert ok is False and "symlink" in reason.lower()


def test_reset_guard_refuses_outside_tree(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    ok, reason = fp._reset_safe(str(outside), root=tmp_path)
    assert ok is False and ("outside" in reason.lower() or "tree" in reason.lower())


def test_reset_guard_allows_plain_junk(tmp_path):
    ok, _ = fp._reset_safe("junk.txt", root=tmp_path)
    assert ok is True


def test_artifact_mode_refuses_to_reset_git(tmp_path):
    """End-to-end: --artifact .git must refuse, never wipe a real .git."""
    (tmp_path / ".git").mkdir()
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n")
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "--artifact", ".git"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2, out.stdout + out.stderr
    assert (tmp_path / ".git").is_dir(), ".git must be left intact"


def test_artifact_mode_refuses_a_preexisting_path(tmp_path):
    """A path that already exists is NOT test-created pollution — the bisection would delete
    it on the first reset, so the tool must refuse and leave it untouched (data-loss guard)."""
    keep = tmp_path / "precious.txt"
    keep.write_text("do not delete me")
    (tmp_path / "test_x.py").write_text("def test_x():\n    assert True\n")
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "--artifact", "precious.txt"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2, out.stdout + out.stderr
    assert keep.is_file() and keep.read_text() == "do not delete me", "must not delete a pre-existing file"


def test_reset_guard_refuses_a_git_tracked_file(tmp_path):
    """The most load-bearing arm: a git-TRACKED file is a real repo file, never test
    pollution — exercised in a real git repo (the tmp_path tests otherwise never reach the
    `git ls-files` branch)."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    tracked = tmp_path / "kept.txt"
    tracked.write_text("tracked")
    subprocess.run(["git", "add", "kept.txt"], cwd=tmp_path, check=True)
    ok, reason = fp._reset_safe("kept.txt", root=tmp_path)
    assert ok is False and "track" in reason.lower()
