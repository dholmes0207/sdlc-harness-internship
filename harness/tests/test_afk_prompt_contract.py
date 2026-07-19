"""Tests for the AFK_STATUS prompt contract in loop_controller.

The output parser reads a `<<<AFK_STATUS>>>{json}<<<END_AFK_STATUS>>>` block from
Claude's response — but Claude only emits it if instructed. wrap_prompt() appends
that instruction to every iteration's prompt so the smart-exit / progress signals
actually arrive. Without it the loop still runs (fail-safe) but only ever stops at
MAX_ITERATIONS.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "afk"))
import loop_controller as lc  # noqa: E402
import afk_output_parser as op  # noqa: E402


def test_wrap_prompt_preserves_the_task():
    wrapped = lc.wrap_prompt("Implement the login flow")
    assert "Implement the login flow" in wrapped


def test_wrap_prompt_carries_the_status_block_contract():
    wrapped = lc.wrap_prompt("x")
    assert "<<<AFK_STATUS>>>" in wrapped
    assert "<<<END_AFK_STATUS>>>" in wrapped
    # the fields the parser/guards actually consume
    for field in ("status", "exit_signal", "files_modified"):
        assert field in wrapped


def test_instruction_describes_the_exit_discipline():
    w = lc.wrap_prompt("x").lower()
    # exit_signal must be the explicit, deliberate finish signal
    assert "exit_signal" in w
    assert "complete" in w


def test_an_example_emitted_per_the_instruction_round_trips():
    # a status block shaped exactly as the instruction shows must parse cleanly
    example = (
        '<<<AFK_STATUS>>>'
        '{"status":"complete","exit_signal":true,"files_modified":3,"note":"done"}'
        '<<<END_AFK_STATUS>>>'
    )
    s = op.parse("model prose ... " + example)
    assert s.found and s.exit_signal is True and s.files_modified == 3
