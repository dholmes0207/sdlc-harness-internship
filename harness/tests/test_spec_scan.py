"""Contract for spec_scan.py — the OPTIONAL R10b helper that lists candidate
independent-spec docs (AC/DEC/requirements/README/ADR) in the target repo so
the main agent can ask the user which is the real 'exam question'. It only
LISTS; it never reads a doc's contents nor decides which is authoritative (that
would re-introduce the self-grading trap R10b exists to break)."""

import subprocess
import sys

from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "plugins" / "hs" / "skills" / "eval-bootstrap" / "scripts" / "spec_scan.py"


def _run(target):
    return subprocess.run([sys.executable, str(_SCRIPT), "--target", str(target)],
                          capture_output=True, text=True)


def test_spec_scan_lists_candidates(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "decisions.md").write_text("# DEC-1\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# proj\n", encoding="utf-8")
    (tmp_path / "requirements.md").write_text("AC-1\n", encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "foo.py").write_text("x=1\n", encoding="utf-8")

    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "decisions.md" in out
    assert "README.md" in out
    assert "requirements.md" in out
    # a plain source file is not a spec candidate
    assert "foo.py" not in out


def test_spec_scan_ignores_absolute_ancestor_skip_dirs(tmp_path):
    # a repo checked out UNDER a dir named like a skip-dir (build/, dist/, ...)
    # must still find its spec docs -- the skip-dir filter is for dirs INSIDE
    # the repo, not the absolute ancestors of the target path.
    repo = tmp_path / "build" / "myrepo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "requirements.md").write_text("AC-1\n", encoding="utf-8")
    (repo / "README.md").write_text("# x\n", encoding="utf-8")
    r = _run(repo)
    assert r.returncode == 0, r.stderr
    assert "requirements.md" in r.stdout
    assert "README.md" in r.stdout


def test_spec_scan_finds_extensionless_readme(tmp_path):
    # a bare README (no extension) is a common authoritative spec candidate
    (tmp_path / "README").write_text("# proj\n", encoding="utf-8")
    (tmp_path / "Makefile").write_text("all:\n", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "README" in r.stdout
    assert "Makefile" not in r.stdout   # stem filter still gates non-spec files


def test_spec_scan_empty_is_loud(tmp_path):
    # no candidate docs -> say so explicitly, do not silently pass
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("x=1\n", encoding="utf-8")
    r = _run(tmp_path)
    assert r.returncode == 0, r.stderr
    assert "no" in r.stdout.lower() and "candidate" in r.stdout.lower()
