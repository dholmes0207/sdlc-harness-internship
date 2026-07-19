"""test_agent_standards_sync.py — write-class agents name the standards (the second net).

The second net after the SubagentStart directive: the write-class agents that were blind
to the standards — tester, planner, debugger — each carry a one-line read directive for
docs/code-standards.md (planner also docs/system-architecture.md, since a plan shapes the
code structure). Report-only agents (researcher, red-teamer, ...) are NOT forced — the
SubagentStart directive self-gates on the task, so no allowlist is needed, and forcing
them would be scope-creep against the directive's self-gating design.
"""
from pathlib import Path

_AGENTS = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "agents"

WRITE_CLASS = ("tester", "planner", "debugger")
REPORT_ONLY = ("researcher", "red-teamer", "brainstormer", "independent-revalidator")
MAX_AGENT_CHARS = 12000


def _read(name):
    return (_AGENTS / ("%s.md" % name)).read_text(encoding="utf-8")


def test_write_class_agents_name_code_standards():
    """Each newly-synced write-class agent names docs/code-standards.md."""
    for name in WRITE_CLASS:
        assert "docs/code-standards.md" in _read(name), (
            "%s.md does not name docs/code-standards.md" % name)


def test_planner_names_system_architecture():
    """The planner also names docs/system-architecture.md — a plan shapes structure."""
    assert "docs/system-architecture.md" in _read("planner"), (
        "planner.md does not name docs/system-architecture.md")


def test_report_only_agents_not_forced():
    """SRP guard against scope-creep: the write-class set is closed and disjoint from the
    report-only set — the directive self-gates, so report-only agents are not forced to
    carry the standards line."""
    assert set(WRITE_CLASS).isdisjoint(REPORT_ONLY), "write-class and report-only overlap"
    # The sync targets exactly the three blind write-class agents, nothing wider.
    assert len(WRITE_CLASS) == 3


def test_tester_carves_run_report_no_respawn():
    """The no-write + no-re-spawn posture is carved into tester.md itself, because
    the shipped lane is not enforced under this repo's `**` overlay."""
    txt = _read("tester").lower()
    assert "does not write test" in txt or "not write test" in txt or "run + report" in txt, (
        "tester.md missing the run+report-only carve")
    assert "re-spawn" in txt or "respawn" in txt or "does not spawn" in txt, (
        "tester.md missing the no-re-spawn carve")


def test_synced_agents_within_cap():
    """Each edited agent .md stays within the thin-core agent char cap."""
    for name in WRITE_CLASS:
        n = len(_read(name))
        assert n <= MAX_AGENT_CHARS, "%s.md %d > cap %d" % (name, n, MAX_AGENT_CHARS)
