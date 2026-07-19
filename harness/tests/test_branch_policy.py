"""test_branch_policy.py — which branches are protected.

branch_policy answers one question: is this branch name protected (so a push to
it must clear the merge-grade gate)? The patterns live in
protected-branches.yaml; fnmatch `*` spans `/` so `release/*` owns the family.
A missing/empty list protects nothing (additive). A malformed file is a typed
error so a broken policy is loud, not silently permissive.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import branch_policy  # noqa: E402


def _policy(tmp_path, body):
    p = tmp_path / "protected-branches.yaml"
    p.write_text(body, encoding="utf-8")
    return p


_SEED = 'protected:\n  - main\n  - master\n  - "release/*"\n'


def test_main_is_protected(tmp_path):
    assert branch_policy.is_protected("main", _policy(tmp_path, _SEED)) is True


def test_feature_branch_is_not_protected(tmp_path):
    assert branch_policy.is_protected(
        "feature/x", _policy(tmp_path, _SEED)) is False


def test_release_family_matches_glob(tmp_path):
    assert branch_policy.is_protected(
        "release/1.2", _policy(tmp_path, _SEED)) is True


def test_missing_file_protects_nothing(tmp_path):
    missing = tmp_path / "nope.yaml"
    assert branch_policy.load_protected(missing) == []
    assert branch_policy.is_protected("main", missing) is False


def test_empty_list_protects_nothing(tmp_path):
    p = _policy(tmp_path, "protected: []\n")
    assert branch_policy.is_protected("main", p) is False


def test_malformed_document_raises(tmp_path):
    p = _policy(tmp_path, "- just\n- a\n- list\n")
    with pytest.raises(branch_policy.BranchPolicyError):
        branch_policy.load_protected(p)


def test_non_string_pattern_raises(tmp_path):
    p = _policy(tmp_path, "protected:\n  - 123\n")
    with pytest.raises(branch_policy.BranchPolicyError):
        branch_policy.load_protected(p)
