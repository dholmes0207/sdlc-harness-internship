"""Smoke test for the eval-bootstrap end-to-end dogfood slice.

Runs `harness/e2e/run_eval_bootstrap_slice.py` as a real SUBPROCESS (the slice
itself scaffolds a tree and drives the generated CLI as further subprocesses —
no import cheating in the run path) and asserts the whole run passed. Also holds
the CI invariant that no dot-claude skills/hooks runtime-path literal leaked into
the slice source.
"""

import re
import subprocess
import sys

from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
_SLICE = _HARNESS / "e2e" / "run_eval_bootstrap_slice.py"

_CLAUDE_REF_RE = re.compile(r"\.claude/(?:skills|hooks)/")


def test_slice_script_exists():
    assert _SLICE.is_file(), "run_eval_bootstrap_slice.py is missing"


def test_slice_source_has_no_dotclaude_refs():
    text = _SLICE.read_text(encoding="utf-8")
    assert not _CLAUDE_REF_RE.search(text)


def test_slice_runs_green():
    result = subprocess.run([sys.executable, str(_SLICE)], capture_output=True, text=True)
    assert result.returncode == 0, (
        "eval-bootstrap slice failed:\n%s\n%s" % (result.stdout, result.stderr))
    # every slice's own dogfood proof must show up, in order, plus the final marker
    assert "SLICE 1 OK" in result.stdout, result.stdout
    assert "SLICE 2 OK" in result.stdout, result.stdout
    assert "SLICE 3 OK" in result.stdout, result.stdout
    assert "SLICE 4 OK" in result.stdout, result.stdout
    assert "SLICE 5 OK" in result.stdout, result.stdout
    assert "SLICE 6 OK" in result.stdout, result.stdout
    assert "SLICE 7 OK" in result.stdout, result.stdout
    assert "SLICE 8 OK" in result.stdout, result.stdout
    assert "ALL SLICES PASSED" in result.stdout, result.stdout
