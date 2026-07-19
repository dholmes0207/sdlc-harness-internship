"""Tests for afk_termination.py — the AFK loop's reason→exit-code taxonomy.

A shared vocabulary so the loop controller, telemetry, and CI parse the same named
termination reasons and stable exit codes instead of ad-hoc integers.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "afk"))
import afk_termination as t  # noqa: E402


def test_exit_codes_are_stable():
    assert t.Termination.COMPLETED.exit_code == 0
    assert t.Termination.EXPLICIT_STOP.exit_code == 0
    assert t.Termination.EXIT_SIGNAL_CONFIRMED.exit_code == 0
    assert t.Termination.MAX_ITERATIONS.exit_code == 1
    assert t.Termination.STALE_LOOP.exit_code == 1
    assert t.Termination.CIRCUIT_OPEN.exit_code == 2
    assert t.Termination.WORKSPACE_GONE.exit_code == 2
    assert t.Termination.RESTART_REQUESTED.exit_code == 3
    assert t.Termination.AWAITING_USER.exit_code == 42


def test_reason_strings_are_unique_and_snake_case():
    reasons = [m.reason for m in t.Termination]
    assert len(reasons) == len(set(reasons)), "duplicate reason strings"
    for r in reasons:
        assert r == r.lower() and " " not in r


def test_by_reason_roundtrip():
    for m in t.Termination:
        assert t.Termination.by_reason(m.reason) is m


def test_by_reason_unknown_raises():
    with pytest.raises(ValueError):
        t.Termination.by_reason("not_a_reason")


def test_is_success_only_for_zero_code():
    assert t.Termination.COMPLETED.is_success
    assert t.Termination.EXIT_SIGNAL_CONFIRMED.is_success
    assert not t.Termination.STALE_LOOP.is_success
    assert not t.Termination.CIRCUIT_OPEN.is_success
    assert not t.Termination.RESTART_REQUESTED.is_success
    assert not t.Termination.AWAITING_USER.is_success


def test_restart_is_distinct_from_stop():
    # exit-3 restart (supervisor re-invokes) must not collide with clean stop (0)
    assert t.Termination.RESTART_REQUESTED.exit_code == 3
    assert t.Termination.EXPLICIT_STOP.exit_code == 0


def test_awaiting_user_is_handoff_code_42():
    # exit-42 = needs-human handoff, distinct from every terminal/failure code
    codes = {m.exit_code for m in t.Termination if m is not t.Termination.AWAITING_USER}
    assert 42 not in codes
