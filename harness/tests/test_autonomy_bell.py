"""test_autonomy_bell.py — consecutive-empty counter FSM for the autonomy bell.

The autonomy bell decides when an unattended /goal-style loop should STOP: it
counts consecutive "nothing left to do" scans and trips at a threshold, so the
off-decision is deterministic instead of resting on the model remembering to stop
(verified failure mode: an autonomous loop bypasses context injection). Mirrors
the afk circuit-breaker store contract — append-only JSONL, last-record-wins
restore — so a re-invoked cron resumes the same count.

Contract under test:
  - report("empty") increments the consecutive count; report("found") resets to 0.
  - status() / --status prints STOP once count >= threshold, else CONTINUE.
  - restore_from reconstructs the count from the last ledger record (a re-fired
    cron resumes, it does not start over).
  - the ledger is append-only: every report adds a line, none rewrite prior lines.
  - --init seeds a fresh run (count 0) recording the cron id + threshold so the
    protocol text can later CronDelete that id; --status honors the seeded threshold.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import autonomy_bell  # noqa: E402

_CLI = _SCRIPTS / "autonomy_bell.py"


def _run(args, ledger):
    return subprocess.run(
        [sys.executable, str(_CLI), "--state", str(ledger), *args],
        capture_output=True, text=True, env=dict(os.environ),
    )


def _lines(ledger):
    return [ln for ln in Path(ledger).read_text(encoding="utf-8").splitlines()
            if ln.strip()]


# --- module API (BellCounter) -------------------------------------------------

class TestBellCounter:
    def test_empty_increments(self, tmp_path):
        b = autonomy_bell.BellCounter(threshold=2, ledger_path=tmp_path / "b.jsonl")
        assert b.report("empty") == "CONTINUE"
        assert b.count == 1
        assert b.report("empty") == "STOP"
        assert b.count == 2

    def test_found_resets(self, tmp_path):
        b = autonomy_bell.BellCounter(threshold=2, ledger_path=tmp_path / "b.jsonl")
        b.report("empty")
        b.report("empty")
        assert b.report("found") == "CONTINUE"
        assert b.count == 0

    def test_status_threshold(self, tmp_path):
        b = autonomy_bell.BellCounter(threshold=3, ledger_path=tmp_path / "b.jsonl")
        b.report("empty")
        assert b.status() == "CONTINUE"
        b.report("empty")
        assert b.status() == "CONTINUE"
        b.report("empty")
        assert b.status() == "STOP"

    def test_restore_keeps_count(self, tmp_path):
        led = tmp_path / "b.jsonl"
        b = autonomy_bell.BellCounter(threshold=2, ledger_path=led)
        b.report("empty")
        # a "restart" — fresh object rehydrated from the ledger keeps the count
        b2 = autonomy_bell.BellCounter.restore_from(led)
        assert b2.count == 1
        assert b2.report("empty") == "STOP"

    def test_restore_missing_ledger_is_fresh(self, tmp_path):
        b = autonomy_bell.BellCounter.restore_from(tmp_path / "absent.jsonl")
        assert b.count == 0
        assert b.status() == "CONTINUE"

    def test_ledger_append_only(self, tmp_path):
        led = tmp_path / "b.jsonl"
        b = autonomy_bell.BellCounter(threshold=2, ledger_path=led)
        b.report("empty")
        b.report("found")
        b.report("empty")
        lines = _lines(led)
        assert len(lines) == 3  # one record per report, nothing rewritten
        first = json.loads(lines[0])
        assert first["outcome"] == "empty" and first["count"] == 1

    def test_restore_honors_seeded_threshold(self, tmp_path):
        led = tmp_path / "b.jsonl"
        autonomy_bell.BellCounter(threshold=5, ledger_path=led).report("empty")
        # restore with no explicit threshold picks up the persisted one
        b = autonomy_bell.BellCounter.restore_from(led)
        assert b.threshold == 5

    def test_restore_skips_corrupt_tail(self, tmp_path):
        # A cron killed mid-append leaves a torn last line. restore must recover
        # the last PARSEABLE record, not reset the streak (and the threshold).
        led = tmp_path / "b.jsonl"
        autonomy_bell.BellCounter(threshold=5, ledger_path=led).report("empty")
        with led.open("a", encoding="utf-8") as f:
            f.write('{"outcome": "empty", "count": 2, "thr')  # truncated, no newline
        b = autonomy_bell.BellCounter.restore_from(led)
        assert b.count == 1       # recovered, not reset to 0
        assert b.threshold == 5   # persisted threshold kept, not reverted to default

    def test_append_after_torn_line_does_not_fuse(self, tmp_path):
        # The torn fragment has no trailing newline; a naive append would fuse the
        # next record onto it, corrupting BOTH into one unparseable line and losing
        # the count on the following restore. The new record must land on its own line.
        led = tmp_path / "b.jsonl"
        autonomy_bell.BellCounter(threshold=2, ledger_path=led).report("empty")  # count1
        with led.open("a", encoding="utf-8") as f:
            f.write('{"outcome": "empty", "count": 2, "thr')  # torn, no newline
        assert autonomy_bell.BellCounter.restore_from(led).report("empty") == "STOP"
        fresh = autonomy_bell.BellCounter.restore_from(led)
        assert fresh.count == 2 and fresh.status() == "STOP"  # reads the new record, not pre-fuse


# --- CLI ----------------------------------------------------------------------

class TestCli:
    def test_report_empty_then_status_stop(self, tmp_path):
        led = tmp_path / "cli.jsonl"
        assert _run(["--init", "--cron-id", "abc123", "--threshold", "2"], led).returncode == 0
        assert _run(["--report", "empty"], led).stdout.strip() == "CONTINUE"
        r = _run(["--report", "empty"], led)
        assert r.stdout.strip() == "STOP"
        assert _run(["--status"], led).stdout.strip() == "STOP"

    def test_found_resets_cli(self, tmp_path):
        led = tmp_path / "cli.jsonl"
        _run(["--init", "--threshold", "2"], led)
        _run(["--report", "empty"], led)
        _run(["--report", "found"], led)
        assert _run(["--status"], led).stdout.strip() == "CONTINUE"

    def test_init_records_cron_id(self, tmp_path):
        led = tmp_path / "cli.jsonl"
        _run(["--init", "--cron-id", "job-xyz", "--threshold", "3"], led)
        rec = json.loads(_lines(led)[-1])
        assert rec["cron_id"] == "job-xyz"
        assert rec["threshold"] == 3
        # seeded threshold 3 means two empties is not yet STOP
        _run(["--report", "empty"], led)
        assert _run(["--report", "empty"], led).stdout.strip() == "CONTINUE"
        assert _run(["--report", "empty"], led).stdout.strip() == "STOP"

    def test_reset_clears_count(self, tmp_path):
        led = tmp_path / "cli.jsonl"
        _run(["--init", "--threshold", "2"], led)
        _run(["--report", "empty"], led)
        _run(["--reset"], led)
        assert _run(["--status"], led).stdout.strip() == "CONTINUE"

    def test_init_without_cron_id_preserves_prior(self, tmp_path):
        # Re-running --init without --cron-id must not drop the seeded cron id
        # (the STOP cleanup needs it for CronDelete).
        led = tmp_path / "cli.jsonl"
        _run(["--init", "--cron-id", "job-keep", "--threshold", "2"], led)
        _run(["--init", "--threshold", "3"], led)
        rec = json.loads(_lines(led)[-1])
        assert rec["cron_id"] == "job-keep"
        assert rec["threshold"] == 3
