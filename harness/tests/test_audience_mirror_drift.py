"""test_audience_mirror_drift.py — drift test ensuring the report-rendering
pointer is wired identically into all 13 report skills AND 8 report agents.

Modelled after test_handoff_deps_drift.py — count-coupling is intentional. The
contract lives in harness/rules/output-rendering.md; each of the 21 files carries
a one-line pointer. This test asserts content-consistency of that pointer (the
canonical tokens), not mere presence of the word "audience".
"""

from pathlib import Path
import pytest

SKILLS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills"
AGENTS_DIR = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "agents"
RULES_DIR = Path(__file__).resolve().parents[1] / "rules"

# The 13 report-generating skills whose render stanza must point at the rule.
# Count-coupling is intentional: adding or removing one forces this list update.
REPORT_SKILLS = [
    "code-review", "docs", "setup", "brainstorm", "plan", "research", "insights",
    "bakeoff", "remember", "critique", "compound", "discover", "agentize",
]
# The 8 report-generating agents (reports are produced by subagents that never
# receive the session inject — so they MUST read the register via the resolver).
REPORT_AGENTS = [
    "code-reviewer", "planner", "researcher", "debugger", "docs-manager",
    "brainstormer", "journal-writer", "critique-consolidator",
]

assert len(REPORT_SKILLS) == 13, "Expected exactly 13 report skills"
assert len(REPORT_AGENTS) == 8, "Expected exactly 8 report agents"

# Canonical tokens the render pointer carries in every file.
CANONICAL_TOKENS = ["output-rendering.md", "--resolved", "humanize", "audience"]

# The exact one-line pointer. The register BEHAVIOR (audience tiers, humanize
# default, evidence list) lives ONLY in output-rendering.md — a stanza carries
# this sentence verbatim and nothing more. Asserting it verbatim is true
# content-consistency: rewording the behavior in one stanza (the DRY failure mode)
# reddens the test. `setup` is exempt — it embeds the same tokens inside a bespoke
# config preamble, not this stanza.
CANONICAL_POINTER = (
    "Render reports per `harness/rules/output-rendering.md`: resolve `language` / `audience` "
    "/ `humanize` live via `python3 \"${HARNESS_BIN_ROOT:-.}\"/harness/scripts/output_config.py "
    "--resolved` (never "
    "hand-read the tracked file); the rule holds the register behavior and the "
    "evidence-invariant fence."
)
_POINTER_EXEMPT = {"skill:setup"}


def _report_files():
    for s in REPORT_SKILLS:
        yield ("skill:" + s, SKILLS_DIR / s / "SKILL.md")
    for a in REPORT_AGENTS:
        yield ("agent:" + a, AGENTS_DIR / (a + ".md"))


@pytest.mark.dev_repo
def test_all_report_files_carry_canonical_pointer():
    """The 20 dedicated report stanzas must carry the canonical pointer VERBATIM
    (content-consistency: reword the behavior in one stanza and this reddens).
    `setup` is exempt from the verbatim line but must still carry the 4 tokens."""
    problems = []
    for label, path in _report_files():
        assert path.exists(), f"report file not found: {label} ({path})"
        content = path.read_text(encoding="utf-8")
        if label in _POINTER_EXEMPT:
            for tok in CANONICAL_TOKENS:
                if tok not in content:
                    problems.append(f"{label}: missing token {tok!r}")
            continue
        if CANONICAL_POINTER not in content:
            problems.append(f"{label}: canonical pointer line missing/reworded (drift)")
    assert not problems, (
        "Report-rendering pointer drift:\n  " + "\n  ".join(problems))


def test_humanizer_rule_has_audience_fence():
    """humanizer-and-anti-ai-tells.md must contain an audience fence clause asserting
    that audience only shapes prose and never touches evidence tokens."""
    humanizer = Path(__file__).resolve().parents[1] / "data" / "output-styles" / "humanizer-and-anti-ai-tells.md"
    assert humanizer.exists(), f"humanizer rule not found at {humanizer}"
    content = humanizer.read_text(encoding="utf-8")
    assert "audience" in content, (
        "humanizer-and-anti-ai-tells.md missing 'audience' fence clause")
    assert any(tok in content for tok in ("evidence", "unchanged", "invariant")), (
        "humanizer audience mention found but no evidence-invariant clause")
