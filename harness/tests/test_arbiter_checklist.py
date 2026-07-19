"""Arbiter done-gate: both orchestration skills carry the independent 7-question checklist
before a fan-out may be declared done.

The checklist is ported verbatim from the upstream orchestrate skill: seven questions the
integrator must answer before the final report ships, so a fan-out cannot be called complete
while a job silently failed, outputs contradict, or a claim rides on no evidence.
"""
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_SKILLS = _ROOT / "plugins" / "hs" / "skills"
_TARGETS = (
    _SKILLS / "workflow-orchestrate" / "SKILL.md",
    _SKILLS / "coding-agent-orchestration" / "SKILL.md",
)

# The seven verbatim questions (distinctive fragments of each).
_SEVEN = (
    "produce the requested artifact",
    "fail, timeout, or emit an uncertainty marker",
    "outputs contradict each other",
    "all listed checks run, and did they pass",
    "supported by file paths, command output, citations, or tests",
    "destructive actions proposed but not approved",
    "unresolved questions listed plainly",
)


def test_both_skills_carry_the_full_seven_question_arbiter():
    for target in _TARGETS:
        assert target.is_file(), "missing skill file %s" % target
        text = target.read_text(encoding="utf-8")
        assert "rbiter" in text, "%s carries no arbiter section" % target.parent.name
        missing = [q for q in _SEVEN if q not in text]
        assert not missing, "%s arbiter missing questions: %s" % (target.parent.name, missing)


def test_evidence_question_is_verbatim():
    """The load-bearing evidence question must stay word-for-word — the whole gate turns on
    'is this claim backed by real evidence'."""
    phrase = "supported by file paths, command output, citations, or tests"
    for target in _TARGETS:
        assert phrase in target.read_text(encoding="utf-8"), (
            "%s dropped the verbatim evidence question" % target.parent.name)
