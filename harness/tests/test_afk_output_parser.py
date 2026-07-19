"""Tests for afk_output_parser.py — parse the AFK_STATUS block from Claude stdout.

The native loop controller owns the subprocess, so it reads Claude's stdout and
pulls a delimited status block. Contract: a MISSING or MALFORMED block yields an
empty status with exit_signal=False — the loop must NEVER auto-exit on a parse
miss (fail-safe keep-going; research risk R3).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "afk"))
import afk_output_parser as op  # noqa: E402


def _block(json_str: str) -> str:
    return "<<<AFK_STATUS>>>%s<<<END_AFK_STATUS>>>" % json_str


def test_parses_a_well_formed_block():
    out = "blah blah\n" + _block(
        '{"status":"in_progress","exit_signal":false,"files_modified":2,"note":"wip"}'
    ) + "\ntrailing"
    s = op.parse(out)
    assert s.found
    assert s.status == "in_progress"
    assert s.exit_signal is False
    assert s.files_modified == 2
    assert s.note == "wip"


def test_exit_signal_true_complete():
    s = op.parse(_block('{"status":"complete","exit_signal":true,"files_modified":0}'))
    assert s.found and s.exit_signal is True
    assert op.is_complete(s)


def test_uses_last_block_when_multiple():
    out = _block('{"status":"in_progress","exit_signal":false}') + "\n" + \
        _block('{"status":"complete","exit_signal":true}')
    s = op.parse(out)
    assert s.status == "complete" and s.exit_signal is True


def test_missing_block_is_failsafe_empty():
    s = op.parse("no status here, just normal model prose")
    assert not s.found
    assert s.exit_signal is False
    assert s.status == ""
    assert s.files_modified == 0


def test_malformed_json_is_failsafe_empty():
    s = op.parse(_block('{not valid json,,,'))
    assert not s.found
    assert s.exit_signal is False


def test_unclosed_block_is_failsafe():
    s = op.parse("<<<AFK_STATUS>>>{\"status\":\"complete\",\"exit_signal\":true}")
    assert not s.found
    assert s.exit_signal is False


def test_tolerant_exit_signal_coercion():
    # accept JSON bool, and the common string/int spellings, but default False
    assert op.parse(_block('{"exit_signal":"true"}')).exit_signal is True
    assert op.parse(_block('{"exit_signal":1}')).exit_signal is True
    assert op.parse(_block('{"exit_signal":"false"}')).exit_signal is False
    assert op.parse(_block('{"exit_signal":0}')).exit_signal is False
    assert op.parse(_block('{"status":"in_progress"}')).exit_signal is False


def test_files_modified_defaults_and_coerces():
    assert op.parse(_block('{"status":"x"}')).files_modified == 0
    assert op.parse(_block('{"files_modified":"5"}')).files_modified == 5
    assert op.parse(_block('{"files_modified":"oops"}')).files_modified == 0


def test_blocked_status_does_not_exit():
    s = op.parse(_block('{"status":"blocked","exit_signal":false,"note":"need creds"}'))
    assert s.status == "blocked"
    assert op.is_blocked(s)
    assert s.exit_signal is False
