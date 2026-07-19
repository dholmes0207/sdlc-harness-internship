"""test_portback_p8.py — P8 polish + learn-from-new surfaces are present and brand-clean.

Guards the prose restorations of the MED/LOW sweep: the partnership/check-in tone on the
plain audience profile, the lead build-vs-buy framing, the developer agent's phase-file read
protocol + Phase Implementation Report template, the agent-browser "Why" section, the
mcp-builder automated-evaluation prose, the problem-solving Amplifier credit, the two restored
context-engineering reference drawers, and the statusline context-fullness indicator. Red
before the edits (markers absent). evaluation.md / eval scripts are restored in P9, so they are
NOT asserted here.
"""
import json
import re
from pathlib import Path

import pytest  # noqa: F401

_REPO = Path(__file__).resolve().parents[2]
_OUT = _REPO / "harness" / "data" / "output-styles"
_SKILLS = _REPO / "harness" / "plugins" / "hs" / "skills"
_AGENTS = _REPO / "harness" / "plugins" / "hs" / "agents"


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_audience_level0_has_partnership_and_checkin_tone():
    src = _read(_OUT / "audience-level-0.md")
    assert "Partnership Voice" in src
    assert "Check-In" in src and "Does this make sense" in src
    assert '"we"' in src or "we\"/\"let's" in src or "let's" in src.lower()


def test_audience_level4_has_build_vs_buy_framing():
    src = " ".join(_read(_OUT / "audience-level-4.md").split())  # whitespace-agnostic
    assert "build-vs-buy-vs-partner" in src
    assert "technical-debt trajectory" in src


def test_developer_agent_has_phase_protocol_and_report():
    src = _read(_AGENTS / "developer.md")
    assert "## Phase Implementation Report" in src
    assert "phase-XX-*.md" in src, "phase-file read protocol marker missing"


@pytest.mark.dev_repo
def test_agent_browser_has_why_section():
    src = _read(_SKILLS / "agent-browser" / "SKILL.md")
    assert "## Why agent-browser" in src
    assert "accessibility-tree snapshots" in src.lower() or "Accessibility-tree snapshots" in src


@pytest.mark.dev_repo
def test_mcp_builder_phase4_describes_automated_eval():
    src = _read(_SKILLS / "mcp-builder" / "SKILL.md")
    assert "automated evaluation run" in src
    # P8 added the prose-only description (no dangling link); P9 then restored the runner and
    # wired the now-real references/evaluation.md drawer + scripts/evaluation.py. Assert the P9
    # end-state: the precise reference link now exists and resolves to a shipped file.
    assert "references/evaluation.md" in src
    assert (_SKILLS / "mcp-builder" / "references" / "evaluation.md").exists()


def test_problem_solving_credits_amplifier():
    src = _read(_SKILLS / "problem-solving" / "SKILL.md")
    assert "Amplifier" in src and "microsoft/amplifier" in src
    assert "2adb63f" in src


def test_context_engineering_drawers_restored_and_wired():
    refs = _SKILLS / "context-engineering" / "references"
    assert (refs / "tool-design.md").exists()
    assert (refs / "memory-systems.md").exists()
    skill = _read(_SKILLS / "context-engineering" / "SKILL.md")
    assert "references/tool-design.md" in skill
    assert "references/memory-systems.md" in skill


def test_statusline_has_context_fullness_indicator():
    cfg = json.loads(_read(_REPO / "harness" / "data" / "ccstatusline-default.json"))
    types = {w["type"] for line in cfg["lines"] for w in line}
    assert "context-percentage-usable" in types, "approaching-autocompact indicator missing"


@pytest.mark.dev_repo
def test_p8_touched_files_are_brand_clean():
    touched = [
        _OUT / "audience-level-0.md",
        _OUT / "audience-level-4.md",
        _AGENTS / "developer.md",
        _SKILLS / "agent-browser" / "SKILL.md",
        _SKILLS / "mcp-builder" / "SKILL.md",
        _SKILLS / "problem-solving" / "SKILL.md",
        _SKILLS / "context-engineering" / "references" / "tool-design.md",
        _SKILLS / "context-engineering" / "references" / "memory-systems.md",
    ]
    dead = "." + "claude" + "/"  # assembled so this test never trips the no-ck-tree invariant
    for p in touched:
        low = _read(p).lower()
        # word-boundary so the ck: namespace prefix is caught but "check:" is not a false hit
        assert not re.search(r"\bck:", low), f"{p.name} still has a ck: reference"
        assert "claudekit" not in low, f"{p.name} still names ClaudeKit"
        assert dead not in low, f"{p.name} references the dead .claude tree"
