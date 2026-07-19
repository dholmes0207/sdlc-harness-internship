"""Output-style enrichment: a worked example per code register + a Required Response Structure
per prose register, without breaking the scope fence that keeps the two axes separate.

- code-style-level-{0..5}.md each carry a worked CODE example answering the SAME sample
  question, so a reader can compare how the register changes the answer across levels.
- audience-level-{0..5}.md each carry a Required Response Structure; the high levels (4/5)
  add the Risk Assessment table (Likelihood × Impact × Mitigation).
- The scope fence (code-style shapes code only; audience shapes prose only; evidence tokens
  invariant) survives on every file.
"""
from pathlib import Path

_DIR = Path(__file__).resolve().parents[1] / "data" / "output-styles"
_LEVELS = (0, 1, 2, 3, 4, 5)
_SAMPLE_Q = "API errors"  # the shared worked-example question


def _code(n):
    return (_DIR / ("code-style-level-%d.md" % n)).read_text(encoding="utf-8")


def _aud(n):
    return (_DIR / ("audience-level-%d.md" % n)).read_text(encoding="utf-8")


def test_every_code_style_has_a_worked_example_on_the_shared_question():
    for n in _LEVELS:
        text = _code(n)
        assert "Worked Example" in text, "code-style-level-%d missing a worked example" % n
        assert _SAMPLE_Q in text, "code-style-level-%d worked example not on the shared question" % n


def test_every_audience_has_a_required_response_structure():
    for n in _LEVELS:
        assert "Required Response Structure" in _aud(n), (
            "audience-level-%d missing the Required Response Structure" % n)


def test_high_audience_levels_carry_the_risk_assessment_table():
    for n in (4, 5):
        text = _aud(n)
        assert "Risk Assessment" in text, "audience-level-%d missing Risk Assessment" % n
        # the table header must name the three axes (verbatim or L/I/M abbreviations)
        has_full = all(k in text for k in ("Likelihood", "Impact", "Mitigation"))
        assert has_full, "audience-level-%d risk table missing L/I/M columns" % n


def test_scope_fence_survives_on_every_file():
    for n in _LEVELS:
        code = _code(n).lower()
        assert "not alter prose" in code or "evidence token" in code, (
            "code-style-level-%d lost its scope fence" % n)
        assert "Scope Fence" in _aud(n), "audience-level-%d lost its Scope Fence section" % n


def test_evidence_invariant_note_preserved_on_audience():
    for n in _LEVELS:
        assert "evidence" in _aud(n).lower(), (
            "audience-level-%d dropped the evidence-invariant note" % n)
