"""test_ladder_port.py — the Minimal Implementation Ladder must land in three places.

A proactive "before you write new code, ask whether you need it" ladder is installed at
three surfaces: the implement rule (`primary-workflow.md`), the review lens
(`code-reviewer` agent), and the simplify step (`code-simplifier` agent) — plus the
anti-slop dimension of the `code-review` skill. Each surface must carry the five rungs
(Delete / Standard-library-native / Existing-dep-utility / Tiny-change / Shrink) and the
non-negotiable caveat that the ladder never cuts scope, trust boundaries, security, a11y,
error handling, or observability. The adaptation must also shed every upstream source
brand. Red before the edits.
"""
import re
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RULE = _ROOT / "harness" / "rules" / "primary-workflow.md"
_REVIEWER = _ROOT / "harness" / "plugins" / "hs" / "agents" / "code-reviewer.md"
_SIMPLIFIER = _ROOT / "harness" / "plugins" / "hs" / "agents" / "code-simplifier.md"
_CR_SKILL = _ROOT / "harness" / "plugins" / "hs" / "skills" / "code-review" / "SKILL.md"

# the five ladder rungs, asserted as lowercase substrings on each surface
_RUNGS = ("delete", "standard library", "existing", "tiny", "shrink")

# assemble the banned brand/route tokens from parts so this test file itself never trips
# the CI invariant that bans the contiguous install-output path strings under harness/
_DEAD_SKILLS = "." + "claude" + "/skills/"
_DEAD_HOOKS = "." + "claude" + "/hooks/"
_DEAD_BRAND = "claude" + "kit"


def _has_all_rungs(text: str) -> bool:
    low = text.lower()
    return all(rung in low for rung in _RUNGS)


def test_primary_workflow_has_ladder():
    body = _RULE.read_text(encoding="utf-8")
    low = body.lower()
    assert "minimal implementation ladder" in low, "ladder heading missing in primary-workflow"
    assert _has_all_rungs(body), "not all five ladder rungs present in primary-workflow"
    assert "do not cut" in low, "missing the 'do not cut' scope/security caveat"


def test_code_reviewer_has_complexity_lens():
    body = _REVIEWER.read_text(encoding="utf-8")
    low = body.lower()
    assert "complexity" in low, "complexity-only lens missing in code-reviewer"
    assert _has_all_rungs(body), "not all five ladder rungs present in code-reviewer lens"
    # the lens must not override correctness/security and must report separately from defects
    assert "correctness" in low and "security" in low, "missing the do-not-override caveat"


def test_code_simplifier_has_ladder_step():
    body = _SIMPLIFIER.read_text(encoding="utf-8")
    low = body.lower()
    assert "minimal implementation ladder" in low, "ladder step missing in code-simplifier"
    assert _has_all_rungs(body), "not all five ladder rungs present in code-simplifier"
    # balance-guard: over-simplification must not cut validation/security/scope
    assert "over-simplif" in low, "missing over-simplification balance guard"
    assert "security" in low, "balance guard must name security"


def test_code_review_antislop_has_ladder():
    body = _CR_SKILL.read_text(encoding="utf-8")
    low = body.lower()
    assert "anti-slop" in low, "anti-slop dimension not present in code-review SKILL"
    assert "ladder" in low, "ladder language missing from code-review anti-slop dimension"
    assert _has_all_rungs(body), "not all five ladder rungs present in code-review SKILL"


def test_no_source_brand_leak_ladder():
    for f in (_RULE, _REVIEWER, _SIMPLIFIER, _CR_SKILL):
        low = f.read_text(encoding="utf-8").lower()
        assert not re.search(r"\bck:", low), f"surviving ck: route in {f.name}"
        assert _DEAD_BRAND not in low, f"source brand survived in {f.name}"
        assert _DEAD_SKILLS not in low, f"install-output skills path in {f.name}"
        assert _DEAD_HOOKS not in low, f"install-output hooks path in {f.name}"
