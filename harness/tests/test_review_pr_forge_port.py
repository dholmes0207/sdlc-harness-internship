"""test_review_pr_forge_port.py — review-pr auto-detects the forge (gh PR vs glab MR).

hs:review-pr gains forge auto-detection: read `git remote get-url origin`, route a `gitlab`
remote to `glab`/MR and a `github` remote to `gh`/PR, and ASK on an unknown remote rather than
guessing. The gh↔glab command mapping lives in a reference (SKILL stays thin). The reframe must
NOT touch the scoring/verdict/fix-loop logic. Red before the reference + SKILL edits.
"""
import re
import shutil
import subprocess

import pytest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SKILL = _ROOT / "harness" / "plugins" / "hs" / "skills" / "review-pr" / "SKILL.md"
_REF = _ROOT / "harness" / "plugins" / "hs" / "skills" / "review-pr" / "references" / "forge-detection.md"

_DEAD_SKILLS = "." + "claude" + "/skills/"
_DEAD_HOOKS = "." + "claude" + "/hooks/"
_DEAD_BRAND = "claude" + "kit"


def test_forge_detection_reference_exists():
    assert _REF.is_file(), "references/forge-detection.md missing"
    ref = _REF.read_text(encoding="utf-8")
    assert "glab mr" in ref and "gh pr" in ref, "reference missing the gh↔glab command mapping"
    assert re.search(r"unknown", ref, re.IGNORECASE), "reference does not handle the unknown-forge (ask) case"
    # the two-tier independence note (tier-1 tool, tier-2 GitLab is a separate declared seam)
    assert re.search(r"tier-1|declared[- ]seam|two-tier", ref, re.IGNORECASE), \
        "reference missing the tier-1-independent note"


def test_skill_documents_both_forges():
    body = _SKILL.read_text(encoding="utf-8")
    assert "git remote get-url" in body, "SKILL does not detect the forge from the git remote"
    assert "glab" in body, "SKILL does not name the glab (GitLab) branch"
    assert re.search(r"forge", body, re.IGNORECASE), "SKILL has no forge-detection prose"


def test_review_scoring_untouched():
    body = _SKILL.read_text(encoding="utf-8")
    assert "mergeStateStatus" in body, "lost the merge-state check in the fix loop"
    assert "Duplicate / prior-work gate" in body, "lost the duplicate/prior-work mandatory gate"


def test_reference_avoids_broken_glab_forms():
    """glab 1.36.0 `mr view`/`mr list` have no --output/--jq, and there is no `mr note
    create`/`mr note list`. Guard the reference against those documented-but-wrong forms so
    a string-only check cannot re-introduce them (this is what the phantom-test missed)."""
    ref = _REF.read_text(encoding="utf-8")
    for bad in ("mr view \"$PR_REF\" --output json", "mr list", "mr note create",
                "mr note list"):
        if bad == "mr list":
            assert "mr list --all --search" not in ref or "--output json" not in ref, \
                "glab mr list has no --output json flag"
            continue
        assert bad not in ref, f"broken glab form documented: {bad!r}"
    assert "--output json" not in ref, "glab mr view/list have no --output json in 1.36.0"
    # the correct JSON path and comment form must be present
    assert "glab api" in ref, "reference must point to `glab api` for JSON output"
    assert re.search(r"glab mr note .*-m", ref), "reference must use `glab mr note ... -m`"


@pytest.mark.skipif(shutil.which("glab") is None, reason="glab not installed")
def test_glab_flag_shapes_are_real():
    """When glab is present, prove the flags the reference relies on actually exist — a
    behavioral guard, not a string check."""
    view_help = subprocess.run(["glab", "mr", "view", "--help"], capture_output=True, text=True).stdout
    assert "--output" not in view_help, "glab mr view unexpectedly grew --output; revisit the mapping"
    note_help = subprocess.run(["glab", "mr", "note", "--help"], capture_output=True, text=True).stdout
    assert "-m, --message" in note_help, "glab mr note lost -m/--message"
    # the api subcommand (our JSON path) must exist
    api = subprocess.run(["glab", "api", "--help"], capture_output=True, text=True)
    assert api.returncode == 0, "glab api subcommand missing — JSON path invalid"


def test_no_source_brand_leak():
    for f in (_SKILL, _REF):
        low = f.read_text(encoding="utf-8").lower()
        assert not re.search(r"\bck:", low), f"surviving ck: route in {f.name}"
        assert _DEAD_BRAND not in low, f"source brand survived in {f.name}"
        assert _DEAD_SKILLS not in low, f"install-output skills path in {f.name}"
        assert _DEAD_HOOKS not in low, f"install-output hooks path in {f.name}"
