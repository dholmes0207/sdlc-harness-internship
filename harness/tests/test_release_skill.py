"""test_release_skill — the hs:release skill is well-formed and registered.

Workstream A's human-facing interface: a thin-core SKILL.md wrapping the
release_orchestrator engine. These assert it passes the structural lint, is
registered in the skill graph without disturbing the immutable spine, and
leaves no dangling cross-references.
"""
import subprocess
import pytest
import sys
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[2]
_SKILL_DIR = _ROOT / "harness/plugins/hs/skills/release"
_SCRIPTS = _ROOT / "harness/scripts"


@pytest.mark.dev_repo
def test_release_skill_structure():
    """The skill exists and check_skill_structure --strict passes on it."""
    assert (_SKILL_DIR / "SKILL.md").exists(), "hs:release SKILL.md missing"
    res = subprocess.run(
        [sys.executable, str(_SCRIPTS / "check_skill_structure.py"), "--strict",
         str(_SKILL_DIR)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr


def test_skill_registered():
    """hs:release is in skill-deps.yaml and the immutable floor is untouched."""
    deps = yaml.safe_load((_ROOT / "harness/data/skill-deps.yaml").read_text())
    assert "release" in deps["skills"], "release not registered in skill-deps"
    # the always-on floor: 13 spine + the off-skill proxy trio (use/find-skills/cleanup)
    floor = {"plan", "cook", "test", "ship", "fix", "debug", "code-review",
             "review-pr", "git", "scout", "understand", "setup", "triage",
             "use", "find-skills", "cleanup"}
    assert set(deps["core_immutable"]) == floor, "core-immutable floor changed"
    # release is a leaf owner-tool: every declared dep must be a known skill
    for d in deps["skills"]["release"].get("deps", []):
        assert d in deps["skills"], "release deps on unknown skill %r" % d


def test_crossrefs_clean():
    """hs:release introduces no broken cross-reference.

    The validator's exit code reflects globally-missing workflow edges (advisory,
    pre-existing), so we don't gate on it — we gate on the Broken References
    section not naming release as the source of a dangling ref.
    """
    import json
    res = subprocess.run(
        [sys.executable, str(_SCRIPTS / "validate_skill_crossrefs.py"),
         str(_ROOT / "harness/plugins/hs"), "--json"],
        capture_output=True, text=True,
    )
    data = json.loads(res.stdout)
    broken = data.get("broken_references", data.get("broken", []))
    offending = [b for b in broken
                 if "release" in str(b).lower()
                 and "release-pr" not in str(b).lower()]
    assert not offending, "release broken refs: %r" % offending
