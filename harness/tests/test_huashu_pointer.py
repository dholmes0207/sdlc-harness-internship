"""huashu-design pointer guard: a see-also that never becomes a hard route.

The policy is pointer-not-vendor: hs:ui-ux names the external huashu-design
skill and its strengths, without vendoring a byte of it. Two things this guards:

1. The canonical block exists in hs:ui-ux and its install text is literal-free —
   it must NOT print the banned ClaudeKit skills-path literal
   (test_bug_class_invariants scans .md for it; describing the skills directory
   in prose is the durable fix). This test assembles that literal at runtime so
   THIS file does not itself carry it and trip the same invariant.
2. The 5 soft one-liners ({frontend-design, html-video, show-off, design, stitch})
   stay ADVISORY: each names huashu, and none turns into a declared hs:ui-ux
   handoff edge — so html-video / show-off (which do NOT dep on ui-ux) are not
   forced to grow a bogus dep. Reuses the handoff-drift edge parser as the oracle.
"""
import sys
import pytest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parent))  # sibling test import

from test_handoff_deps_drift import _declared_edges  # noqa: E402

_HS = REPO_ROOT / "harness" / "plugins" / "hs" / "skills"
_UI_UX = _HS / "ui-ux" / "SKILL.md"
_POINTER_SKILLS = ("frontend-design", "html-video", "show-off", "design", "stitch")
# These have no `ui-ux` dep, so a pointer leaking into a hard route would break drift.
_NO_UI_UX_DEP = ("html-video", "show-off")


@pytest.mark.dev_repo
def test_canonical_block_present():
    text = _UI_UX.read_text(encoding="utf-8")
    assert "huashu-design" in text, "hs:ui-ux must name the external huashu-design skill"
    assert "## Reach further" in text, "hs:ui-ux must carry a '## Reach further' heading"


@pytest.mark.dev_repo
def test_install_block_literal_free():
    # Assemble the banned ClaudeKit skills-path literal at runtime so this test
    # file does not itself carry it (which would trip test_bug_class_invariants).
    banned = ".claude/" + "skills/"
    text = _UI_UX.read_text(encoding="utf-8")
    assert banned not in text, \
        "install text must be literal-free (no ClaudeKit skills-path literal); describe the dir in prose"


@pytest.mark.dev_repo
def test_five_clusters_name_huashu():
    for s in _POINTER_SKILLS:
        text = (_HS / s / "SKILL.md").read_text(encoding="utf-8")
        assert "huashu" in text, f"{s} SKILL.md must carry a huashu see-also pointer"


def test_pointer_stays_advisory_no_new_hard_edge():
    """The pointer must sit under an advisory heading so the drift parser strips
    it: skills without a ui-ux dep must NOT declare a ui-ux handoff edge."""
    edges = _declared_edges()
    for s in _NO_UI_UX_DEP:
        assert "ui-ux" not in edges.get(s, set()), \
            f"{s} pointer leaked into a hard hs:ui-ux route (must live under ## See also)"
