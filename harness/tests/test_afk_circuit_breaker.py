"""Tests for afk_circuit_breaker.py — the three-state stagnation FSM.

CLOSED → HALF_OPEN (>= half_open_at consecutive no-progress) → OPEN
(>= open_at, OR a permission denial immediately). Progress (git diff / explicit
completion / files_modified — OR'd by the caller into one bool) resets to CLOSED.
A pending question SUPPRESSES the no-progress counter so the loop is not tripped
merely because Claude is blocked asking the operator something.

State persists append-only JSONL (last-record-wins on restore), per the store
invariant — no JSON overwrite.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "afk"))
import afk_circuit_breaker as cb  # noqa: E402


def test_starts_closed():
    b = cb.CircuitBreaker()
    assert b.state == cb.CLOSED
    assert b.is_open() is False


def test_half_open_then_open_on_no_progress():
    b = cb.CircuitBreaker(half_open_at=2, open_at=3)
    assert b.update(progress=False) == cb.CLOSED        # count 1
    assert b.update(progress=False) == cb.HALF_OPEN     # count 2
    assert b.update(progress=False) == cb.OPEN          # count 3
    assert b.is_open() is True


def test_progress_resets_to_closed():
    b = cb.CircuitBreaker(half_open_at=2, open_at=3)
    b.update(progress=False)
    b.update(progress=False)                            # HALF_OPEN
    assert b.update(progress=True) == cb.CLOSED
    assert b.is_open() is False
    # and the counter is cleared — needs a fresh run to OPEN
    assert b.update(progress=False) == cb.CLOSED        # count 1 again


def test_question_suppresses_counter():
    b = cb.CircuitBreaker(half_open_at=2, open_at=3)
    b.update(progress=False)                            # count 1
    assert b.update(progress=False, question=True) == cb.CLOSED   # suppressed, still 1
    assert b.update(progress=False, question=True) == cb.CLOSED   # still 1
    assert b.update(progress=False) == cb.HALF_OPEN     # count 2


def test_permission_denied_opens_immediately():
    b = cb.CircuitBreaker(half_open_at=2, open_at=3)
    assert b.update(progress=False, permission_denied=True) == cb.OPEN
    assert b.is_open() is True


def test_ledger_is_append_only_jsonl(tmp_path):
    ledger = tmp_path / "afk" / "cb.jsonl"
    b = cb.CircuitBreaker(half_open_at=2, open_at=3, ledger_path=ledger)
    b.update(progress=False)
    b.update(progress=False)
    b.update(progress=True)
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3                              # one appended record per update
    last = json.loads(lines[-1])
    assert last["state"] == cb.CLOSED


def test_ledger_records_carry_actor_and_ts(tmp_path):
    # every appended record must carry attribution (actor) and a timestamp (ts),
    # per the store invariant — both keys present and non-empty.
    ledger = tmp_path / "afk" / "cb.jsonl"
    b = cb.CircuitBreaker(half_open_at=2, open_at=3, ledger_path=ledger)
    b.update(progress=False)
    rec = json.loads(ledger.read_text(encoding="utf-8").splitlines()[0])
    assert "actor" in rec and rec["actor"]
    assert "ts" in rec and rec["ts"]


def test_restore_last_record_wins(tmp_path):
    ledger = tmp_path / "afk" / "cb.jsonl"
    b1 = cb.CircuitBreaker(half_open_at=2, open_at=3, ledger_path=ledger)
    b1.update(progress=False)
    b1.update(progress=False)                           # HALF_OPEN, count 2
    # a fresh breaker restored from the ledger continues where it left off
    b2 = cb.CircuitBreaker.restore_from(ledger, half_open_at=2, open_at=3)
    assert b2.state == cb.HALF_OPEN
    assert b2.update(progress=False) == cb.OPEN         # count 3 → OPEN


def test_restore_from_missing_ledger_is_fresh(tmp_path):
    b = cb.CircuitBreaker.restore_from(tmp_path / "nope.jsonl")
    assert b.state == cb.CLOSED


def test_ledger_write_error_is_best_effort(tmp_path):
    blocker = tmp_path / "afile"
    blocker.write_text("x", encoding="utf-8")
    b = cb.CircuitBreaker(open_at=2, half_open_at=1,
                          ledger_path=blocker / "nested" / "cb.jsonl")
    b.update(progress=False)
    assert b.update(progress=False) == cb.OPEN          # still decides in-memory
