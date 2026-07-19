"""test_tier_c_absence.py — the Tier-C (team/multi-owner governance) machinery is
being ripped out for personal-first. This file pins the ABSENCE of each removed
piece so it cannot silently return. Grep is scoped to the git-tracked source tree
(harness/ minus state/, release/, .github/, .claude/settings.json); prose history
(CHANGELOG / LESSONS / plans/) is exempt.
"""
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _git_grep(pattern: str, *paths: str) -> list:
    """Lines matching pattern across tracked files in the given pathspecs."""
    cmd = ["git", "-C", str(_REPO), "grep", "-n", "-I", pattern, "--", *paths]
    res = subprocess.run(cmd, capture_output=True, text=True)
    # git grep exits 1 when there are no matches — that is success here.
    return [ln for ln in res.stdout.splitlines() if ln.strip()]


# Pathspecs that legitimately keep the names (history / this very test).
_EXEMPT = (
    ":(exclude)harness/state/**",
    ":(exclude)**/CHANGELOG.md",
    ":(exclude)**/LESSONS.md",
    ":(exclude)**/decisions.yaml",
    ":(exclude)**/decisions.md",
    ":(exclude)plans/**",
    ":(exclude)harness/tests/test_tier_c_absence.py",
)


def test_ownership_machinery_gone():
    hits = _git_grep(r"ownership_guard\|ownership_gate\|work-ownership",
                     "harness/", "release/", ".github/", ".claude/settings.json",
                     *_EXEMPT)
    assert hits == [], "ownership machinery still referenced:\n" + "\n".join(hits)


def test_ownership_files_deleted():
    for rel in ("harness/hooks/ownership_guard.py",
                "harness/scripts/ownership_gate.py",
                "harness/data/work-ownership.yaml",
                "harness/tests/test_ownership_guard.py",
                "harness/tests/test_ownership_gate.py"):
        assert not (_REPO / rel).exists(), f"{rel} must be deleted"


def test_approval_chain_imports_no_team_modules():
    """plan_approval.py is fully SLIM — no team_config/role_policy import. The
    artifact_check APPROVAL region (_check_plan_approval) carries no team read
    either. NOTE (H1): the whole artifact_check.py file still imports team_config
    for _override_clears/_coverage_skips until P6 — that whole-tree assertion is
    test_no_team_modules_anywhere (P6), not here."""
    pa_src = (_REPO / "harness" / "scripts" / "plan_approval.py").read_text(encoding="utf-8")
    assert "import team_config" not in pa_src
    assert "import role_policy" not in pa_src
    assert "def check_role" not in pa_src
    assert "def approval_policy_for" not in pa_src

    ac_src = (_REPO / "harness" / "scripts" / "artifact_check.py").read_text(encoding="utf-8")
    # The deleted quorum/role helpers are gone.
    for gone in ("def _check_quorum", "def _quorum_roles", "def _load_approvals"):
        assert gone not in ac_src, f"{gone} must be deleted from artifact_check"
    # The approval check no longer routes through a team policy.
    approval = ac_src[ac_src.index("def _check_plan_approval"):]
    approval = approval[:approval.index("\n\n\n")]
    assert "team_config" not in approval and "check_role" not in approval \
        and "approval_policy_for" not in approval, \
        "the approval region must not read team/roster/quorum"


def test_no_team_modules_anywhere():
    """Whole-tree (P6): no team_config/role_policy/posture_config module file, and
    no tracked source imports or references them. This is the assertion P5 could NOT
    make (the modules lived until P6 for artifact_check's break-glass/coverage-skip)."""
    for rel in ("harness/scripts/team_config.py",
                "harness/scripts/role_policy.py",
                "harness/scripts/posture_config.py",
                "harness/install/_posture.py"):
        assert not (_REPO / rel).exists(), f"{rel} must be deleted"
    hits = _git_grep(r"team_config\|role_policy\|posture_config",
                     "harness/", "release/", ".github/", *_EXEMPT)
    assert hits == [], "team machinery still referenced:\n" + "\n".join(hits)


def test_no_team_data_files():
    for rel in ("harness/data/team.yaml", "harness/data/roles-policy.yaml",
                "harness/data/work-ownership.yaml"):
        assert not (_REPO / rel).exists(), f"{rel} must be deleted"


def test_break_glass_override_gone():
    """artifact_check no longer carries the break-glass override / coverage-skip
    machinery (H1): the functions are gone and the schema is deleted."""
    ac = (_REPO / "harness" / "scripts" / "artifact_check.py").read_text(encoding="utf-8")
    for gone in ("def _override_clears", "def _coverage_skips", "_OVERRIDE_FIELDS"):
        assert gone not in ac, f"{gone} must be removed from artifact_check"
    assert not (_REPO / "harness" / "schemas" / "artifact-override.json").exists(), \
        "the break-glass override schema must be deleted"
