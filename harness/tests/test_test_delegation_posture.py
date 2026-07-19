"""test_test_delegation_posture.py — hs:test delegate + verdict-route posture.

hs:test gains the Task tool so it can delegate the suite run to a @tester that RUNS AND
REPORTS ONLY (verdict + the unmapped/missing-test list) — it does not write tests. The
main thread then routes on the verdict against a DoD-anchored threshold: a missing DoD
test-type is a LARGE gap (→ @developer writes the test, test-first), a coverage nicety is
SMALL (→ main fixes inline). Delegation exists to isolate the test output from main.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import skill_frontmatter  # noqa: E402
import check_skill_structure as css  # noqa: E402

_TEST = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills" / "test"
_SKILL = _TEST / "SKILL.md"
_ROUTE = _TEST / "references" / "delegation-route.md"


def test_test_skill_allows_task_tool():
    """The Task tool is in allowed-tools so hs:test can delegate."""
    fm = skill_frontmatter.frontmatter(_SKILL.read_text(encoding="utf-8"))
    tools = fm.get("allowed-tools") or []
    assert "Task" in tools, "allowed-tools missing Task: %s" % tools


def test_test_skill_documents_tester_run_report_only():
    """The SKILL or its reference states @tester runs + reports only (does not write
    tests)."""
    body = skill_frontmatter.body(_SKILL.read_text(encoding="utf-8")).lower()
    route = _ROUTE.read_text(encoding="utf-8").lower() if _ROUTE.exists() else ""
    blob = body + "\n" + route
    assert "@tester" in blob or "tester" in blob, "tester not named"
    assert "run + report" in blob or "run and report" in blob or "run+report" in blob, (
        "@tester run+report-only posture not documented")
    assert "does not write test" in blob or "not write test" in blob, (
        "the @tester no-write-tests constraint is not stated")


def test_delegation_route_ref_has_threshold():
    """The route reference documents the verdict-route threshold (DoD-anchored + a counting
    fallback)."""
    txt = _ROUTE.read_text(encoding="utf-8").lower()
    assert "dod" in txt or "test-policy" in txt or "test_type" in txt, (
        "DoD-anchored threshold not documented")
    assert "unmapped" in txt, "the counting fallback (unmapped files) not documented"
    assert "@developer" in txt, "large-gap route to @developer not documented"


def test_test_body_within_cap():
    body = skill_frontmatter.body(_SKILL.read_text(encoding="utf-8"))
    cap = css.skill_body_cap("test")
    assert len(body) <= cap, "test body %d > cap %d" % (len(body), cap)
