"""test_e1_rule_content_wave2.py — content-shape guards for the phase-2 (Wave-2 core
Must) E1 rule files: S1a `intake-and-interview-discipline.md`, S1b
`plain-language-phrasing.md`, S3a `testability-triad.md`, S3b
`scope-and-contract-discipline.md`, S4 `plan-quality-goodhart-premortem.md`.

Content-shape checks the rule CONTAINS its required components (mechanism headings, an
IN/OUT example pair, a literal keyword) -- it does not check agent behavior at runtime
(see plan glossary: "content-shape pytest").

S3 LANDMINE (K7): the testability triad's third branch is `invariant`, deliberately NOT
`rule` -- a term-collision fix, not a port error. Every S3 assertion below checks for
the literal string `invariant` and checks the OLD name `rule` is absent from the branch
naming, proving the rename actually landed (not just "preserved by accident").
"""
from __future__ import annotations

import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_RULES = _ROOT / "harness" / "rules"

_S1A = _RULES / "intake-and-interview-discipline.md"
_S1B = _RULES / "plain-language-phrasing.md"
_S3A = _RULES / "testability-triad.md"
_S3B = _RULES / "scope-and-contract-discipline.md"
_S4 = _RULES / "plan-quality-goodhart-premortem.md"


def _text(path: Path) -> str:
    assert path.is_file(), f"E1 rule missing (write it as part of this phase): {path}"
    return path.read_text(encoding="utf-8")


# --- S3a testability-triad -----------------------------------------------------------

S3_BRANCH_HEADINGS = ("### test", "### invariant", "### manual")


def _s3_branch_headings_present(text: str) -> list:
    return [h for h in S3_BRANCH_HEADINGS if h in text]


def test_s3_triad_uses_invariant_not_rule_branch_name():
    text = _text(_S3A)
    present = _s3_branch_headings_present(text)
    assert present == list(S3_BRANCH_HEADINGS), (
        f"S3 triad must carry exactly the 3 branch headings {S3_BRANCH_HEADINGS}, "
        f"found {present}"
    )
    # the branch heading is never the old collided name -- K7 landmine check
    assert "### rule" not in text, (
        "S3 triad branch-3 must be named `invariant`, NOT `rule` (K7 term-collision "
        "fix is deliberate -- do not restore the old port name)"
    )


def test_s3_branch_names_synthetic_negative_test_rule_manual_is_wrong():
    # proves the check above actually catches the wrong (pre-K7) naming, not just
    # passing on current content by coincidence
    synthetic = "### test\n...\n### rule\n...\n### manual\n...\n"
    present = _s3_branch_headings_present(synthetic)
    assert present != list(S3_BRANCH_HEADINGS), (
        "fixture using the old `test/rule/manual` naming must NOT match the "
        "required `test/invariant/manual` heading set"
    )


def test_s3_manual_branch_binds_manual_test_anchor_literal():
    text = _text(_S3A)
    assert "manual_test_anchor.py" in text, (
        "S3 manual branch must bind the literal keyword `manual_test_anchor.py` -- "
        "missing it fails the DoD gate Lớp B check later (artifact_check.py)"
    )


def test_s3_manual_branch_missing_anchor_detected_synthetic():
    synthetic = "### manual\nNeeds a human in the loop.\n"
    assert "manual_test_anchor.py" not in synthetic


def test_s3_decision_tree_three_steps_in_order():
    text = _text(_S3A)
    idx1 = text.find("1. **Can this be exercised")
    idx2 = text.find("2. **Is it a property")
    idx3 = text.find("3. **Does it require a human judgment")
    assert idx1 != -1 and idx2 != -1 and idx3 != -1, "S3 decision tree missing a step"
    assert idx1 < idx2 < idx3, "S3 decision tree steps must appear in order 1 -> 2 -> 3"


def test_s3_decision_tree_out_of_order_detected_synthetic():
    synthetic = "3. third\n1. first\n2. second\n"
    i1, i2, i3 = synthetic.find("1."), synthetic.find("2."), synthetic.find("3.")
    assert not (i1 < i2 < i3), "fixture must actually be out of order"


def test_s3_two_side_rules_present():
    text = _text(_S3A).lower()
    assert "different shapes" in text or "split them into two" in text, (
        "S3 missing side-rule: universal+behavioral invariants must be split in two"
    )
    assert "sampling" in text or "sample" in text, (
        "S3 missing side-rule: an invariant must not be satisfied by sampling"
    )


def test_s3_anti_tautology_present():
    text = _text(_S3A).lower()
    assert "tautology" in text, "S3 missing an anti-tautology line/heading"


def test_s3_out_to_in_example_present():
    text = _text(_S3A)
    assert "the system must be fast" in text.lower() or "must be fast" in text.lower(), (
        "S3 missing the OUT->IN example ('the system must be fast' -> measurable)"
    )
    assert "<200ms" in text or "measurable" in text.lower() or "p95" in text, (
        "S3 OUT->IN example missing the measurable IN side"
    )


# --- S3b scope-and-contract-discipline ------------------------------------------------

def test_s3b_not_doing_empty_list_pushback_trigger():
    text = _text(_S3B)
    assert "are you truly excluding nothing" in text.lower(), (
        "S3b missing the Not-Doing push-back sample question"
    )


def test_s3b_contract_delta_four_fields():
    text = _text(_S3B)
    for field in ("**before**", "**after**", "**who-affected**", "**migration-path**"):
        assert field in text, f"S3b contract-delta template missing field {field}"


def test_s3b_contract_delta_missing_field_detected_synthetic():
    synthetic = "**before** ... **after** ... **who-affected** ...\n"
    assert "**migration-path**" not in synthetic


# --- S4 plan-quality-goodhart-premortem -----------------------------------------------

S4_LENSES = ("Technical", "UX", "Adoption", "Organizational", "External", "Security")


def test_s4_all_six_lenses_present():
    text = _text(_S4)
    missing = [lens for lens in S4_LENSES if lens not in text]
    assert not missing, f"S4 pre-mortem missing lens(es): {missing}"


def test_s4_missing_lens_detected_synthetic():
    synthetic = "| Technical | q |\n| UX | q |\n| Adoption | q |\n"
    missing = [lens for lens in S4_LENSES if lens not in synthetic]
    assert missing == ["Organizational", "External", "Security"]


def test_s4_each_lens_has_a_seed_question_row():
    text = _text(_S4)
    # every lens row is a table row "| Lens | question ending in ? |"
    for lens in S4_LENSES:
        pattern = re.compile(rf"\|\s*{re.escape(lens)}\s*\|[^|]*\?[^|]*\|")
        assert pattern.search(text), f"S4 lens {lens!r} missing a seed question (row with '?')"


def test_s4_runs_before_red_team_wording():
    text = _text(_S4).lower()
    assert "before red-team" in text or "before `@red-teamer`" in text.lower(), (
        "S4 must state it runs BEFORE red-team"
    )


def test_s4_no_new_severity_scale_keyword():
    text = _text(_S4)
    assert "C/H/M/L" in text, "S4 must reference the existing C/H/M/L severity scale"
    # banned tokens are literal NEW scale vocabularies -- "new scale" itself is exempt
    # because the rule legitimately says "do not invent a new scale" (a negation, not
    # an invented scale)
    banned = ("severity-5", "P0/P1/P2", "1-10 scale", "sev1", "sev-1")
    low = text.lower()
    for token in banned:
        assert token.lower() not in low, f"S4 must not invent a new severity scale ({token!r} found)"


def test_s4_no_new_scale_keyword_detected_synthetic():
    synthetic = "use a new scale: sev1/sev2/sev3\n"
    assert "sev1" in synthetic.lower()


# --- S1a intake-and-interview-discipline ----------------------------------------------

S1A_MECHANISMS = (
    "### band-driven posture ladder",
    "### P0 check",
    "### skip-already-answered",
    "### JTBD filter",
    "### no-fabrication + surface-conflicts-before-missing",
    "### precedence knob-vs-band",
)


def test_s1a_six_mechanism_headings_present():
    text = _text(_S1A)
    missing = [h for h in S1A_MECHANISMS if h not in text]
    assert not missing, f"S1a missing mechanism heading(s): {missing}"


def test_s1a_missing_mechanism_detected_synthetic():
    synthetic = "### band-driven posture ladder\n...\n### P0 check\n...\n"
    missing = [h for h in S1A_MECHANISMS if h not in synthetic]
    assert len(missing) == 4


def test_s1a_in_out_example_pair_present():
    text = _text(_S1A)
    assert "IN:" in text and "OUT:" in text, "S1a missing an IN/OUT example pair"


def test_s1a_wire_point_line_present():
    text = _text(_S1A)
    assert "hs:plan" in text and "hs:discover" in text, (
        "S1a missing a wire-point line naming where it loads (hs:plan / hs:discover)"
    )


def test_s1a_precedence_line_present():
    text = _text(_S1A).lower()
    assert "interview_rigor" in text and "invariant" in text, (
        "S1a missing the precedence line: knob wins on harshness, 5-facts floor invariant"
    )


def test_s1a_precedence_missing_detected_synthetic():
    synthetic = "no precedence discussion here at all\n"
    low = synthetic.lower()
    assert not ("interview_rigor" in low and "invariant" in low)


# --- S1b plain-language-phrasing -------------------------------------------------------

def test_s1b_jargon_table_at_least_eight_rows():
    text = _text(_S1B)
    rows = [
        line for line in text.splitlines()
        if line.strip().startswith("|") and "---" not in line and "Jargon" not in line
    ]
    assert len(rows) >= 8, f"S1b jargon-swap table needs >=8 rows, found {len(rows)}"


def test_s1b_jargon_table_row_undercount_detected_synthetic():
    synthetic = "| Jargon | Plain swap |\n|---|---|\n| a | b |\n| c | d |\n"
    rows = [
        line for line in synthetic.splitlines()
        if line.strip().startswith("|") and "---" not in line and "Jargon" not in line
    ]
    assert len(rows) < 8


def test_s1b_exactly_one_vi_sample_marked_evidence():
    text = _text(_S1B)
    markers = text.count("<!-- evidence: vi sample -->")
    assert markers == 1, f"S1b must carry EXACTLY 1 vi-sample evidence marker, found {markers}"


def test_s1b_zero_or_two_vi_markers_detected_synthetic():
    zero_marker_text = "no vietnamese sample here\n"
    assert zero_marker_text.count("<!-- evidence: vi sample -->") == 0
    two_marker_text = "<!-- evidence: vi sample -->\n...\n<!-- evidence: vi sample -->\n"
    assert two_marker_text.count("<!-- evidence: vi sample -->") == 2
