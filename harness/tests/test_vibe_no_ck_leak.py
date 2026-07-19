"""test_vibe_no_ck_leak.py — the vibe ADAPT must shed every upstream brand/route.

vibe is ported from the ck `vibe` skill; the whole orchestration chain re-brands to the
harness `/hs:` spine. Guard: no surviving `ck:` / `/ck:` route, no `.claude/` tree ref,
no ClaudeKit brand anywhere under vibe/. Red before the re-brand.
"""
import re
import pytest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_VIBE = _ROOT / "harness" / "plugins" / "hs" / "skills" / "vibe"

# assemble the dead tokens from parts so this test file itself never trips the
# CI invariant that bans the contiguous install-output path strings under harness/.
_DEAD_SKILLS = "." + "claude" + "/skills/"
_DEAD_HOOKS = "." + "claude" + "/hooks/"


def _sources():
    return list(_VIBE.rglob("*.md"))


@pytest.mark.dev_repo
def test_vibe_tree_present():
    assert _VIBE.is_dir(), "vibe skill dir missing"
    assert _sources(), "vibe has no markdown files"


def test_no_ck_route_or_brand():
    for f in _sources():
        low = f.read_text(encoding="utf-8").lower()
        # word-boundary ck: catches `/ck:` and `ck:` routes without matching `check:`
        assert not re.search(r"\bck:", low), f"surviving ck: route in {f.name}"
        assert "claudekit" not in low, f"ClaudeKit brand survived in {f.name}"
        assert _DEAD_SKILLS not in low, f"install-output skills path in {f.name}"
        assert _DEAD_HOOKS not in low, f"install-output hooks path in {f.name}"


@pytest.mark.dev_repo
def test_routes_to_hs_spine():
    body = (_VIBE / "SKILL.md").read_text(encoding="utf-8")
    # the orchestrated chain must name the hs: spine skills it drives
    for route in ("hs:worktree", "hs:plan", "hs:cook", "hs:fix", "hs:code-review",
                  "hs:ship", "hs:review-pr"):
        assert route in body, f"vibe SKILL.md does not route to {route}"
