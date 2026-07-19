"""End-to-end test of loop_controller.main() with a STUB claude binary.

The unit tests cover the loop logic via an injected fake invoker. This e2e
exercises the live seam the unit tests don't: main() → make_claude_invoker →
build_claude_command → real subprocess → output_parser → guards → exit code.
A stub `claude` (emits an AFK_STATUS block, ignores its args) stands in for the
model, so the whole CLI path runs for real without a live Claude or Docker.
"""

import os
import subprocess
import sys
from pathlib import Path

_CONTROLLER = Path(__file__).resolve().parent.parent / "afk" / "loop_controller.py"

_STUB = """#!/usr/bin/env bash
# stub claude: ignore args; emit one AFK_STATUS block per call by mode.
# A monotonic counter makes each call's stdout unique so the stale-loop guard
# (which fires on identical consecutive turns) does not pre-empt the path under
# test — the circuit breaker's no-progress detection is what we exercise here.
COUNT_FILE="${STUB_COUNT_FILE:-/tmp/afk-e2e-stub-count}"
n=$(( $(cat "$COUNT_FILE" 2>/dev/null || echo 0) + 1 ))
echo "$n" > "$COUNT_FILE"
if [ "$STUB_MODE" = "exit" ]; then
  echo "turn $n"
  echo '<<<AFK_STATUS>>>{"status":"complete","exit_signal":true}<<<END_AFK_STATUS>>>'
else
  echo "turn $n"
  echo '<<<AFK_STATUS>>>{"status":"in_progress","exit_signal":false}<<<END_AFK_STATUS>>>'
fi
"""


def _stub_bin(tmp_path: Path) -> Path:
    b = tmp_path / "bin"
    b.mkdir(parents=True, exist_ok=True)
    claude = b / "claude"
    claude.write_text(_STUB, encoding="utf-8")
    claude.chmod(0o755)
    return claude


def _run(tmp_path: Path, mode: str, iterations: int):
    env = dict(os.environ)
    env["CLAUDE_BIN"] = str(_stub_bin(tmp_path))
    env["STUB_MODE"] = mode
    env["STUB_COUNT_FILE"] = str(tmp_path / "stub-count")
    return subprocess.run(
        [sys.executable, str(_CONTROLLER), "do the work", str(iterations),
         "--repo-root", str(tmp_path), "--state-dir", str(tmp_path / "state")],
        capture_output=True, text=True, env=env, timeout=60,
    )


def test_e2e_dual_condition_exit_is_clean(tmp_path):
    # complete+exit_signal every turn → dual-condition gate fires on the 2nd →
    # EXIT_SIGNAL_CONFIRMED → exit 0.
    r = _run(tmp_path, "exit", iterations=10)
    assert r.returncode == 0, r.stderr
    assert "exit_signal_confirmed" in r.stderr


def test_e2e_stall_trips_circuit_breaker(tmp_path):
    # in_progress with no git diff → no progress → circuit opens at 3 →
    # CIRCUIT_OPEN → exit 2.
    r = _run(tmp_path, "stall", iterations=8)
    assert r.returncode == 2, r.stderr
    assert "circuit_open" in r.stderr


def test_e2e_writes_append_only_state(tmp_path):
    _run(tmp_path, "stall", iterations=8)
    cb = tmp_path / "state" / "cb.jsonl"
    assert cb.is_file()
    lines = [ln for ln in cb.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) >= 3            # one append per iteration, no overwrite


_STUB_IDENTICAL = """#!/usr/bin/env bash
# stub claude: emit the EXACT same output every call (no counter) so two
# consecutive turns fingerprint-match — exercises the live stale-loop guard.
echo 'identical every single turn'
echo '<<<AFK_STATUS>>>{"status":"in_progress","exit_signal":false}<<<END_AFK_STATUS>>>'
"""


def test_e2e_identical_turns_trip_stale_loop(tmp_path):
    # Same stdout each iteration → the stale guard must fire live (it previously
    # never did because the iteration number was mixed into the fingerprint).
    b = tmp_path / "bin"
    b.mkdir(parents=True, exist_ok=True)
    claude = b / "claude"
    claude.write_text(_STUB_IDENTICAL, encoding="utf-8")
    claude.chmod(0o755)
    env = dict(os.environ)
    env["CLAUDE_BIN"] = str(claude)
    r = subprocess.run(
        [sys.executable, str(_CONTROLLER), "do the work", "8",
         "--repo-root", str(tmp_path), "--state-dir", str(tmp_path / "state")],
        capture_output=True, text=True, env=env, timeout=60,
    )
    assert r.returncode == 1, r.stderr
    assert "stale_loop" in r.stderr
