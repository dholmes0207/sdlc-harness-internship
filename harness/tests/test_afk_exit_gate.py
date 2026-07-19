"""Tests for afk_exit_gate.py — the dual-condition exit gate.

Exit only when BOTH hold: (a) >= N consecutive completion indicators (rolling),
and (b) the current iteration carries an explicit exit_signal. Neither alone
exits — this defeats a one-shot false positive from verbose completion language.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "afk"))
import afk_exit_gate as eg  # noqa: E402
import afk_output_parser as op  # noqa: E402


def _st(status="in_progress", exit_signal=False):
    return op.Status(found=True, status=status, exit_signal=exit_signal)


def test_single_completion_does_not_exit():
    g = eg.ExitGate()  # default threshold 2
    assert g.observe(_st("complete", exit_signal=True)) is False
    assert g.consecutive == 1


def test_two_consecutive_exit_signals_exit():
    g = eg.ExitGate()
    assert g.observe(_st("complete", exit_signal=True)) is False
    assert g.observe(_st("complete", exit_signal=True)) is True


def test_non_completion_resets_the_run():
    g = eg.ExitGate()
    g.observe(_st("complete", exit_signal=True))      # count 1
    assert g.observe(_st("in_progress")) is False      # reset to 0
    assert g.consecutive == 0
    assert g.observe(_st("complete", exit_signal=True)) is False  # count 1 again
    assert g.observe(_st("complete", exit_signal=True)) is True   # count 2 → exit


def test_count_reached_but_no_explicit_signal_on_decider():
    # two "complete" statuses build the count, but if the deciding iteration has
    # no exit_signal, do NOT exit (explicit flag is required on the decider).
    g = eg.ExitGate()
    g.observe(_st("complete", exit_signal=False))      # indicator (complete), count 1
    assert g.observe(_st("complete", exit_signal=False)) is False  # count 2 but no signal
    # now an explicit signal with the count already >= 2 → exit
    assert g.observe(_st("complete", exit_signal=True)) is True


def test_blocked_status_resets():
    g = eg.ExitGate()
    g.observe(_st("complete", exit_signal=True))
    assert g.observe(_st("blocked")) is False
    assert g.consecutive == 0


def test_threshold_configurable():
    g = eg.ExitGate(threshold=3)
    assert g.observe(_st("complete", exit_signal=True)) is False
    assert g.observe(_st("complete", exit_signal=True)) is False
    assert g.observe(_st("complete", exit_signal=True)) is True


def test_empty_status_never_exits():
    g = eg.ExitGate()
    for _ in range(5):
        assert g.observe(op.Status()) is False
