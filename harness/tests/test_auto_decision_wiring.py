"""test_auto_decision_wiring.py — every auto-mode points at the ledger rule.

The emit is wired in PROSE, not code: one shared rule (harness/rules/auto-decision-ledger.md)
owns the emit contract + the in_plan procedure + the label rules + the writer-model +
the emit-failure-is-advisory contract; each of the 9 auto-mode SKILL.md files carries a
one-line pointer to it. This parity test is the phase's brake: a mode that forgets the
pointer fails here, and cook must say it records only plan DEVIATIONS.
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_RULE = _REPO / "harness" / "rules" / "auto-decision-ledger.md"
_SKILLS = _REPO / "harness" / "plugins" / "hs" / "skills"

# 9 SKILL.md files (code-review is one file serving 3 flags; afk/goal/loop are three files).
_MODES = ["code-review", "fix", "review-pr", "cook", "ship", "vibe", "afk", "goal", "loop"]


def _skill_text(mode):
    return (_SKILLS / mode / "SKILL.md").read_text(encoding="utf-8")


def test_ledger_rule_exists():
    assert _RULE.is_file()
    t = _RULE.read_text(encoding="utf-8")
    assert "in_plan" in t
    assert "evidence" in t


def test_all_9_modes_reference_rule():
    missing = [m for m in _MODES if "auto-decision-ledger" not in _skill_text(m)]
    assert missing == [], "modes missing the ledger-rule pointer: %s" % missing


def test_cook_prose_deviation_only():
    # cook records ONLY plan deviations — the pointer must say so.
    t = _skill_text("cook").lower()
    assert "deviat" in t


def test_fix_keeps_emit_observation():
    t = _skill_text("fix")
    assert "emit_observation" in t          # the existing signal channel is NOT removed
    assert "auto-decision-ledger" in t      # AND the ledger pointer is added alongside


def test_rule_names_label_vocab():
    t = _RULE.read_text(encoding="utf-8")
    assert "auto-decision-labels.yaml" in t
    assert "lean" in t.lower()               # lean to the HEAVIER basket when unsure


def test_rule_states_writer_model():
    # a worktree subagent REPORTS the deviation; the MAIN agent writes at the barrier.
    t = _RULE.read_text(encoding="utf-8").lower()
    assert "barrier" in t
    assert "report" in t


def test_rule_states_emit_advisory():
    # emit exit != 0 is advisory to the host — the mode LOGS and CONTINUES, never halts.
    t = _RULE.read_text(encoding="utf-8").lower()
    assert "halt" in t
    assert "continue" in t
