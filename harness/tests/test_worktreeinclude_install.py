"""test_worktreeinclude_install.py — the installer materializes .worktreeinclude
into a per-project target (F1 close), skips it under global mode (R8) and never
clobbers the dogfood source (source==target).

Behaviour asserted (R9): the produced file's CONTENT equals the template, not
just that it exists; re-install is idempotent; verify_install's presence check
holds per-project and is silent under global.
"""
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_DIR = _REPO_ROOT / "harness" / "install"
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
for _p in (str(_INSTALL_DIR), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import install as installer  # noqa: E402
import verify_install  # noqa: E402
from conftest import _git  # noqa: E402

_TEMPLATE = _REPO_ROOT / "harness" / "install" / "worktreeinclude.template"


def _template_lines():
    return [l.strip() for l in _TEMPLATE.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


@pytest.fixture()
def target_repo(tmp_path):
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


def _read_wti(repo: Path):
    p = repo / ".worktreeinclude"
    if not p.is_file():
        return None
    return [l.strip() for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


class TestMaterialize:
    def test_t6_per_project_materializes_template_content(self, target_repo):
        res = installer.install(_REPO_ROOT, target_repo)
        assert res["ok"], res["problems"]
        got = _read_wti(target_repo)
        assert got is not None, ".worktreeinclude must be materialized"
        # content carries every template line (R9 — behaviour, not just exists)
        for line in _template_lines():
            assert line in got, "missing template line: %s" % line

    def test_t7_idempotent(self, target_repo):
        installer.install(_REPO_ROOT, target_repo)
        first = (target_repo / ".worktreeinclude").read_text(encoding="utf-8")
        installer.install(_REPO_ROOT, target_repo)
        second = (target_repo / ".worktreeinclude").read_text(encoding="utf-8")
        assert first == second, "re-install must not change .worktreeinclude"

    def test_t6b_global_mode_skips_materialize(self, target_repo):
        res = installer.install(_REPO_ROOT, target_repo, mode="global")
        # R8: global uses one shared bin — no per-project .worktreeinclude, and
        # verify_install must NOT fail for its absence.
        assert not (target_repo / ".worktreeinclude").is_file(), \
            "global mode must not materialize .worktreeinclude"
        assert res["ok"], res["problems"]

    def test_t8_dogfood_source_equals_target_no_clobber(self):
        # A dogfood (source==target) dry-run install must not rewrite the
        # hand-authored root .worktreeinclude.
        before = (_REPO_ROOT / ".worktreeinclude").read_text(encoding="utf-8")
        installer.install(_REPO_ROOT, _REPO_ROOT, dry_run=True)
        after = (_REPO_ROOT / ".worktreeinclude").read_text(encoding="utf-8")
        assert before == after


class TestVerifyPresence:
    def test_presence_check_passes_after_install(self, target_repo):
        installer.install(_REPO_ROOT, target_repo)
        problems = verify_install.worktreeinclude_problems(target_repo, mode="project")
        assert problems == [], problems

    def test_presence_missing_flags_per_project(self, target_repo):
        installer.install(_REPO_ROOT, target_repo)
        (target_repo / ".worktreeinclude").unlink()
        problems = verify_install.worktreeinclude_problems(target_repo, mode="project")
        assert problems, "a missing .worktreeinclude must be flagged per-project"

    def test_presence_missing_ok_under_global(self, target_repo):
        # R8: global never materializes it, so its absence is not a problem.
        problems = verify_install.worktreeinclude_problems(target_repo, mode="global")
        assert problems == []
