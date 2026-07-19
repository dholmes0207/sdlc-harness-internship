"""Review-discipline hybrid: the detailed review/audit/decision block lives in an
on-demand rule (folded into verification-mechanism.md), leaving a one-line pointer — while
the ★ probe-first habit STAYS always-load (it is a posture habit, not a review rule) and is
NOT re-homed under the review pointer.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1].parent  # repo root (CLAUDE.md lives here)
_RULE = _ROOT / "harness" / "rules" / "verification-mechanism.md"
_CLAUDE = _ROOT / "CLAUDE.md"
_CONTRACT = _ROOT / "harness" / "rules" / "harness-contract.md"


def test_rule_exists_with_four_review_items():
    assert _RULE.is_file(), "verification-mechanism.md rule is missing"
    text = _RULE.read_text(encoding="utf-8").lower()
    for item in ("verified decision", "user decision", "scout first", "threat-model"):
        assert item in text, "rule missing the %r item" % item


def test_claude_md_keeps_only_a_pointer_not_the_detailed_block():
    text = _CLAUDE.read_text(encoding="utf-8")
    assert "verification-mechanism" in text, "CLAUDE.md lost the pointer to the rule"
    # the verbatim detailed bullet lives in the rule; CLAUDE.md must not carry it inline
    assert "once verified by source, tests, or an empirical check" not in text, (
        "the detailed review block is still inline in CLAUDE.md")


def test_probe_first_stays_always_load():
    """★ probe-first must remain always-load — in the CLAUDE.md always-load line or in the
    always-load harness-contract.md."""
    claude = _CLAUDE.read_text(encoding="utf-8").lower()
    contract = _CONTRACT.read_text(encoding="utf-8").lower()
    assert ("probe before you build on a guess" in claude
            or "probe before you build on a guess" in contract), (
        "probe-first dropped out of the always-load layer")


def test_probe_first_not_rehomed_under_the_review_pointer():
    """The review pointer line must not re-attach ★ probe-first to review discipline."""
    for line in _CLAUDE.read_text(encoding="utf-8").splitlines():
        if "reviewing code, applying audit feedback" in line.lower():
            low = line.lower()
            assert "probe-first" not in low and "probe before you build" not in low, (
                "the review pointer wrongly re-homes probe-first: %s" % line)


def test_no_content_loss_all_review_items_survive_in_the_rule():
    rule = _RULE.read_text(encoding="utf-8").lower()
    # each concept the old CLAUDE.md block carried must exist somewhere in the rule
    for concept in ("do not reverse", "silently und", "present", "scout before asking",
                    "what the code actually stores"):
        assert concept in rule, "review concept %r lost in the port" % concept
