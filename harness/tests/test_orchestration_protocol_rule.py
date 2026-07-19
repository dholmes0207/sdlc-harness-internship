"""Presence guards for the orchestration-protocol rule (dev-repo only).

The rule is the single source for the delegation-fan-out protocol. These guards
pin the four load-bearing clauses that the enforce-skills (P5) propagate from one
place instead of copying six times:

- route the `hs:workflow-orchestrate` call at the numbered execution step, never
  only in a trailing standalone section;
- distinguish a config-fixed fan-out (challenge layer) from a variable one (also
  sizes/groups);
- the reason / strategy / scope contract the script REFLECTS (advisory, not a
  gate);
- the three structural anti-waste mechanisms (group-cap / batch-consolidate /
  early-write) whose cross-cutting caps live in orchestration.yaml.

The rule stays "structural + visible", so it must NOT claim to be a hard gate:
the forbidden-wording guard keeps the wording fence (code-standards §10).
"""

from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_RULE = _ROOT / "harness" / "rules" / "orchestration-protocol.md"


def _rule_body():
    assert _RULE.is_file(), f"orchestration-protocol rule missing: {_RULE}"
    return _RULE.read_text(encoding="utf-8")


@pytest.mark.dev_repo
def test_rule_states_route_at_execution_step():
    low = _rule_body().lower()
    assert "numbered" in low, "rule must say route at the numbered step"
    assert "execution step" in low, "rule must name the execution step as the route site"
    assert ("never only" in low or "not only" in low), (
        "rule must negate the end-section / trailing-section anti-pattern"
    )


@pytest.mark.dev_repo
def test_rule_distinguishes_fixed_vs_variable_fanout():
    low = _rule_body().lower()
    assert "config-fixed" in low, "rule must name the config-fixed fan-out case"
    assert "variable" in low, "rule must name the variable fan-out case"


@pytest.mark.dev_repo
def test_rule_names_reason_strategy_scope_contract():
    body = _rule_body()
    low = body.lower()
    for field in ("reason", "strategy", "scope"):
        assert field in low, f"rule must name the {field} contract field"
    # The script reflects the fields; it does not decide. "REFLECTS" is the
    # honesty token (avoid the forbidden 'hard-gate' substring — see wording fence).
    assert "REFLECTS" in body, "rule must state the script REFLECTS the three fields"


@pytest.mark.dev_repo
def test_rule_names_three_structural_mechanisms():
    low = _rule_body().lower()
    for mech in ("group-cap", "batch-consolidate", "early-write"):
        assert mech in low, f"rule must name the {mech} structural mechanism"
    assert "orchestration.yaml" in low, (
        "rule must point cross-cutting caps at the one config source orchestration.yaml"
    )


@pytest.mark.dev_repo
def test_rule_avoids_forbidden_wording():
    low = _rule_body().lower()
    # Fragment the banned tokens so this guard file does not itself trip the
    # global wording invariant (which greps every text file for the literals).
    _d = "-"
    for banned in (f"tamper{_d}proof", f"write{_d}fence", f"hard{_d}gate"):
        assert banned not in low, (
            f"wording fence (code-standards §10): rule must not claim {banned!r} "
            "for the structural+visible enforce layer"
        )


@pytest.mark.dev_repo
def test_rule_keeps_prior_sections():
    # The four new clauses are additive — the pre-existing protocol sections must
    # survive (delegation-context, write-lane, context-isolation, parallel-safety,
    # status-protocol).
    low = _rule_body().lower()
    for section in (
        "delegation context",
        "write-lane preflight",
        "context isolation",
        "parallel work",
        "status protocol",
    ):
        assert section in low, f"pre-existing section '{section}' must not be dropped"
