"""Presence checks for the hs:scenario inbound wiring adopted as prose-only edits.

hs:scenario is an edge-case/test-target decomposer that other skills should reach as a
"consider/handoff" step at the moments they write or expand tests for a concrete
code-path. A sweep over the skill catalog found five inbound points worth wiring; these
live entirely in skill/reference markdown (no Python to exercise). The "test" is a
grep-for-presence playing the red->green role for a prose artifact. Marked ``dev_repo``
so it tracks the shipped skill tree on the development repo and auto-skips on installed
copies where the manifest may omit a skill.
"""
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_HS = ROOT / "harness" / "plugins" / "hs" / "skills"

pytestmark = pytest.mark.dev_repo


def _read(rel: str) -> str:
    return (_HS / rel).read_text(encoding="utf-8")


# --- tier-1: plan --tdd seeds Tests Before/After from a scenario sweep --------

def test_tdd_plan_mode_points_to_scenario_for_code_phase():
    txt = _read("plan/references/tdd-plan-mode.md")
    low = txt.lower()
    assert "hs:scenario" in txt
    # feeds the existing Tests Before/After lists, does not spawn a 4th matrix
    assert "tests before" in low and "tests after" in low
    # scoped to behavior/code phases; doc-only/trivial phases skip it
    assert "code" in low and ("doc-only" in low or "doc only" in low)
    # advisory, not a hard gate
    assert "advisory" in low or "optional" in low


# --- tier-1: test uses scenario as a 13-dimension coverage-gap finder ---------

def test_coverage_edge_cases_points_to_scenario():
    txt = _read("test/references/coverage-and-edge-cases.md")
    low = txt.lower()
    assert "hs:scenario" in txt
    # framing: the inline checklist covers a fraction of the 13 dimensions
    assert "13" in txt
    assert "coverage gap" in low


# --- tier-1: triage enumerates the defect CLASS before fix writes regression --

def test_triage_points_to_scenario_for_defect_class():
    txt = _read("triage/SKILL.md")
    low = txt.lower()
    assert "hs:scenario" in txt
    # enumerate siblings in the same class, not just the single repro
    assert "class" in low
    assert "regression" in low


# --- tier-2: better-auth seeds the auth-flow test from a security sweep --------

def test_better_auth_points_to_scenario_pre_test():
    txt = _read("better-auth/SKILL.md")
    low = txt.lower()
    assert "hs:scenario" in txt
    assert "--domain security" in txt
    # pre-test enumeration, distinct from the security-scan follow-on
    assert "before" in low and "test" in low


# --- tier-2: payment-integration enumerates money edge cases pre-test ---------

def test_payment_points_to_scenario_pre_test():
    txt = _read("payment-integration/SKILL.md")
    low = txt.lower()
    assert "hs:scenario" in txt
    assert "business-logic" in low
    # money edge cases security-scan does not cover
    assert "double-charge" in low or "webhook-replay" in low or "coupon" in low
