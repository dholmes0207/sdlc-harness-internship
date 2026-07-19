"""test_e1_rule_content_wave3.py — content-shape guards for the phase-3
(Wave-3 Should-tier) CS-13 rule file: `harness/rules/scoring-rigor-contract.md`.

CS-13 (K5): the rule pins pinned-examples to EXACTLY the 3 severity/verdict
scales already alive in the tree -- `blocker|major|minor` (critique finding
contract), `C/H/M/L` (plan red-team-gate), `PASS|PASS_WITH_RISK|BLOCKED` (gate
verdict) -- and must NEVER introduce a 4th scale vocabulary (see plan glossary:
"content-shape pytest").
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RULES = _ROOT / "harness" / "rules"
_SKILLS = _ROOT / "harness" / "plugins" / "hs" / "skills"

_CS13 = _RULES / "scoring-rigor-contract.md"


def _text(path: Path) -> str:
    assert path.is_file(), f"E1 rule missing (write it as part of this phase): {path}"
    return path.read_text(encoding="utf-8")


# --- V5: all 3 scale names present -----------------------------------------

def test_v5_three_scale_names_present():
    text = _text(_CS13)
    assert "blocker" in text and "major" in text and "minor" in text, (
        "CS-13 missing the finding-severity scale (blocker|major|minor)"
    )
    assert "C/H/M/L" in text, "CS-13 missing the red-team severity scale (C/H/M/L)"
    assert "PASS" in text and "PASS_WITH_RISK" in text and "BLOCKED" in text, (
        "CS-13 missing the gate-verdict scale (PASS|PASS_WITH_RISK|BLOCKED)"
    )


def test_v5_missing_scale_detected_synthetic():
    synthetic = "# scoring\n\nOnly blocker/major/minor here.\n"
    assert "C/H/M/L" not in synthetic


# --- V6: no 4th scale --------------------------------------------------------

_BANNED_4TH_SCALE_TOKENS = (
    "severity-5", "P0/P1/P2", "1-10 scale", "sev1", "sev-1", "priority 1-10",
)


def test_v6_no_fourth_scale_keyword():
    text = _text(_CS13)
    low = text.lower()
    for token in _BANNED_4TH_SCALE_TOKENS:
        assert token.lower() not in low, (
            f"CS-13 must not invent a 4th severity/verdict scale ({token!r} found)"
        )


def test_v6_fourth_scale_keyword_detected_synthetic():
    synthetic = "use a new scale: sev1/sev2/sev3\n"
    assert "sev1" in synthetic.lower()


def test_v6_each_pinned_example_anchors_to_one_of_the_three_scales():
    """Every '### pinned example' heading's section body must reference at
    least one of the 3 scale tokens -- proves the anchor, not just presence
    of the words somewhere in the file."""
    text = _text(_CS13)
    sections = text.split("### pinned example")[1:]
    assert len(sections) >= 3, "CS-13 must carry >=3 pinned-example sections"
    scale_tokens = ("blocker", "major", "minor", "C/H/M/L", " H ", " H)",
                    "PASS_WITH_RISK", "BLOCKED", "PASS")
    for i, section in enumerate(sections):
        body = section.split("###")[0]  # up to the next heading
        assert any(tok in body for tok in scale_tokens), (
            f"pinned example #{i + 1} does not anchor to any of the 3 scales"
        )


# --- V7: routed in critique + code-review SKILL.md body (not orphan) -------

def test_v7_routed_in_critique_skill_body():
    text = (_SKILLS / "critique" / "SKILL.md").read_text(encoding="utf-8")
    assert "harness/rules/scoring-rigor-contract.md" in text, (
        "critique/SKILL.md body must cite scoring-rigor-contract.md (R-CS: "
        "CS-10 orphan-check only scans SKILL.md body, not references/)"
    )


def test_v7_routed_in_code_review_skill_body():
    text = (_SKILLS / "code-review" / "SKILL.md").read_text(encoding="utf-8")
    assert "harness/rules/scoring-rigor-contract.md" in text, (
        "code-review/SKILL.md body must cite scoring-rigor-contract.md (R-CS)"
    )
