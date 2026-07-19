"""test_e1_rule_content.py — content-shape guards for the phase-1 E1 rule files
(S2 `port-layering-and-capability-assignment.md`, S7 `architectural-constraints.md`).

Content-shape checks the rule CONTAINS its required components (tier names, a
decision tree, a literal back-reference) -- it does not check agent behavior at
runtime (see plan glossary: "content-shape pytest").
"""
from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RULES = _ROOT / "harness" / "rules"
_S2 = _RULES / "port-layering-and-capability-assignment.md"
_S7 = _RULES / "architectural-constraints.md"

S2_TIERS = ("ORCHESTRATOR-CODE", "PURE-SCRIPT", "MODEL-JUDGMENT-SLOT", "DROP")


def _missing_s2_tiers(text: str) -> list:
    return [t for t in S2_TIERS if f"### {t}" not in text]


# --- T8: S2 must carry all 4 tier headings ------------------------------------------

def test_t8_s2_rule_has_all_four_tier_headings():
    assert _S2.is_file(), f"S2 rule missing: {_S2}"
    text = _S2.read_text(encoding="utf-8")
    missing = _missing_s2_tiers(text)
    assert not missing, f"S2 rule missing tier heading(s): {missing}"


def test_t8_missing_tier_detected_synthetic():
    # proves the shape-check logic itself catches a missing tier (not just current state)
    synthetic = "### ORCHESTRATOR-CODE\n...\n### PURE-SCRIPT\n...\n### MODEL-JUDGMENT-SLOT\n...\n"
    assert _missing_s2_tiers(synthetic) == ["DROP"]


def test_s2_rule_has_three_question_decision_tree():
    assert _S2.is_file(), f"S2 rule missing: {_S2}"
    text = _S2.read_text(encoding="utf-8")
    assert text.count("?") >= 3, "S2 rule must pose a 3-question decision tree"


def test_s2_rule_has_cartesian_grid_gate_example_and_drop_condition():
    assert _S2.is_file(), f"S2 rule missing: {_S2}"
    text = _S2.read_text(encoding="utf-8")
    assert "Cartesian-grid-gate" in text, "S2 rule missing the Cartesian-grid-gate -> ORCHESTRATOR-CODE example"
    assert "### DROP" in text, "S2 rule missing the DROP tier / condition"


def test_s2_rule_bans_code_needing_work_in_judgment_slot():
    assert _S2.is_file(), f"S2 rule missing: {_S2}"
    text = _S2.read_text(encoding="utf-8")
    assert "MODEL-JUDGMENT-SLOT" in text
    low = text.lower()
    assert "never" in low and "judgment" in low, (
        "S2 rule must explicitly ban parking code-needing work in MODEL-JUDGMENT-SLOT"
    )


# --- T9/T10: S7 must carry both constraints + a literal back-reference -------------

def test_t9_s7_rule_has_model_opacity_heading():
    assert _S7.is_file(), f"S7 rule missing: {_S7}"
    text = _S7.read_text(encoding="utf-8")
    assert "### model opacity" in text


def test_t9_missing_model_opacity_detected_synthetic():
    synthetic = "### presence gate blindness\n...\n"
    assert "### model opacity" not in synthetic


def test_s7_rule_has_presence_gate_blindness_heading():
    assert _S7.is_file(), f"S7 rule missing: {_S7}"
    text = _S7.read_text(encoding="utf-8")
    assert "### presence gate blindness" in text


def test_t10_s7_points_back_to_harness_contract_instead_of_restating():
    assert _S7.is_file(), f"S7 rule missing: {_S7}"
    text = _S7.read_text(encoding="utf-8")
    # positive check: a literal reference to the source section it must not restate
    assert "harness-contract.md" in text, (
        "S7 rule must literally reference harness/rules/harness-contract.md "
        "(the presence-gate-blindness source) instead of restating it"
    )


def test_s7_rule_has_design_consequence_and_invariant_framing():
    assert _S7.is_file(), f"S7 rule missing: {_S7}"
    text = _S7.read_text(encoding="utf-8")
    assert "design consequence" in text.lower(), (
        "each S7 constraint needs >=1 'design consequence' line tying it to a real decision"
    )
    assert "invariant" in text.lower() and "todo" in text.lower(), (
        "S7 rule must frame the constraints as invariants, not a TODO"
    )
