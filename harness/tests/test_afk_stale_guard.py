"""Tests for afk_stale_guard.py — stale-loop fingerprint.

Detects thrashing the circuit breaker's git-diff progress check would miss: the
same tool call (tool + source + payload) firing N times in a row. The decision is
in-memory; an optional append-only JSONL ledger records observations for audit
(best-effort — a write error never breaks the loop).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "afk"))
import afk_stale_guard as sg  # noqa: E402


def test_fingerprint_is_deterministic_and_discriminating():
    a = sg.fingerprint("Bash", "afk", {"command": "grep x"})
    b = sg.fingerprint("Bash", "afk", {"command": "grep x"})
    c = sg.fingerprint("Bash", "afk", {"command": "grep y"})
    assert a == b
    assert a != c
    assert len(a) == 16


def test_dict_payload_is_order_independent():
    a = sg.fingerprint("T", "s", {"a": 1, "b": 2})
    b = sg.fingerprint("T", "s", {"b": 2, "a": 1})
    assert a == b


def test_three_identical_is_stale():
    g = sg.StaleGuard(threshold=3)
    g.record("Bash", "afk", {"command": "ls"})
    assert g.is_stale() is False
    g.record("Bash", "afk", {"command": "ls"})
    assert g.is_stale() is False
    g.record("Bash", "afk", {"command": "ls"})
    assert g.is_stale() is True


def test_varied_calls_are_not_stale():
    g = sg.StaleGuard(threshold=3)
    g.record("Bash", "afk", {"command": "a"})
    g.record("Bash", "afk", {"command": "b"})
    g.record("Bash", "afk", {"command": "a"})
    assert g.is_stale() is False


def test_a_break_resets_the_run():
    g = sg.StaleGuard(threshold=3)
    g.record("T", "s", {"x": 1})
    g.record("T", "s", {"x": 1})
    g.record("T", "s", {"x": 2})          # break
    g.record("T", "s", {"x": 1})
    g.record("T", "s", {"x": 1})
    assert g.is_stale() is False           # last 3 are x1,x1 preceded by x2 → not all equal
    g.record("T", "s", {"x": 1})
    assert g.is_stale() is True            # now last 3 all x1


def test_consecutive_identical_counter():
    g = sg.StaleGuard(threshold=3)
    g.record("T", "s", {"x": 1})
    g.record("T", "s", {"x": 1})
    assert g.consecutive_identical == 2
    g.record("T", "s", {"x": 9})
    assert g.consecutive_identical == 1


def test_threshold_configurable():
    g = sg.StaleGuard(threshold=2)
    g.record("T", "s", "same")
    assert g.is_stale() is False
    g.record("T", "s", "same")
    assert g.is_stale() is True


def test_ledger_is_append_only_jsonl(tmp_path):
    ledger = tmp_path / "afk" / "stale.jsonl"
    g = sg.StaleGuard(threshold=3, ledger_path=ledger)
    g.record("Bash", "afk", {"command": "ls"})
    g.record("Bash", "afk", {"command": "ls"})
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    rec = json.loads(lines[0])
    assert rec["tool"] == "Bash" and "fp" in rec


def test_ledger_records_carry_actor_and_ts(tmp_path):
    # every appended observation must carry attribution (actor) and a timestamp
    # (ts), per the store invariant — both keys present and non-empty.
    ledger = tmp_path / "afk" / "stale.jsonl"
    g = sg.StaleGuard(threshold=3, ledger_path=ledger)
    g.record("Bash", "afk", {"command": "ls"})
    rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert "actor" in rec and rec["actor"]
    assert "ts" in rec and rec["ts"]


def test_ledger_write_error_is_best_effort(tmp_path):
    # point the ledger at a path whose parent is a FILE → mkdir/open fails; the
    # guard must still record in-memory and decide, never raise.
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    g = sg.StaleGuard(threshold=2, ledger_path=blocker / "nested" / "stale.jsonl")
    g.record("T", "s", "same")
    g.record("T", "s", "same")
    assert g.is_stale() is True
