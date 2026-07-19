"""test_dev_repo_gate.py — the dev-repo-only test gate.

A chunk of this suite asserts facts about the harness DEVELOPMENT repo (its
docs/STANDARDIZE.md provenance, the decision ledger, the dev CLAUDE.md rule
routing). install.sh runs the shipped suite on the TARGET, where those artifacts
are absent — so such tests are marked `@pytest.mark.dev_repo` and auto-skip off
the dev tree. These pin the detection that drives that skip: the build toolkit
(release/pack.py) is the tamper-free dev-tree signal (tracked in the dev repo,
never shipped in the bundle per pack.py's own contract).
"""
import importlib.util
from pathlib import Path

import pytest

# Load THIS suite's conftest by file path, not `import conftest`: release/tests
# ships its own same-named conftest, so a bare import collides under co-collection
# (pyproject documents the duplicate-basename hazard). By-path import is immune.
_spec = importlib.util.spec_from_file_location(
    "_harness_tests_conftest", Path(__file__).resolve().parent / "conftest.py")
conftest = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(conftest)


@pytest.mark.dev_repo  # only the dev checkout IS the dev tree; skips on installs
def test_dev_tree_detected_in_this_repo():
    # the live checkout is the dev tree: release/pack.py is present
    assert conftest.is_dev_tree(Path(__file__).resolve().parents[2]) is True


def test_installed_copy_is_not_dev_tree(tmp_path):
    # an installed copy has harness/ but no release/ toolkit
    (tmp_path / "harness").mkdir()
    assert conftest.is_dev_tree(tmp_path) is False


def test_dev_repo_marker_is_registered(pytestconfig):
    # the marker must be declared so a marked test is not a pytest "unknown mark"
    markers = pytestconfig.getini("markers")
    assert any(m.startswith("dev_repo") for m in markers), markers
