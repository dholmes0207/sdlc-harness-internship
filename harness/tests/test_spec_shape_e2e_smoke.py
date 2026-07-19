"""Smoke test for the PO(hs:spec)->BA(hs:shape) end-to-end dogfood slice.

Runs `harness/e2e/run_spec_shape_slice.py` as a real SUBPROCESS (the slice
itself drives every spec/shape script as a further subprocess -- no import
cheating anywhere in the chain) and asserts the whole run passed. Also holds
the CI invariant that no dot-claude `skills/` or `hooks/` runtime-path literal
leaked into the slice source (harness/**  must stay install-slot-agnostic).
"""

import re
import subprocess
import sys
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
_SLICE = _HARNESS / "e2e" / "run_spec_shape_slice.py"

_CLAUDE_REF_RE = re.compile(r"\.claude/(?:skills|hooks)/")


def test_slice_script_exists():
    assert _SLICE.is_file(), "run_spec_shape_slice.py is missing"


def test_slice_source_has_no_dotclaude_refs():
    text = _SLICE.read_text(encoding="utf-8")
    assert not _CLAUDE_REF_RE.search(text), (
        "run_spec_shape_slice.py carries a .claude/skills or .claude/hooks literal")


def test_slice_runs_clean_subprocess_exit_0():
    proc = subprocess.run(
        [sys.executable, str(_SLICE)],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, (
        "slice exit=%s\n--- stdout ---\n%s\n--- stderr ---\n%s"
        % (proc.returncode, proc.stdout[-4000:], proc.stderr[-2000:])
    )
    out = proc.stdout
    # Spot-check the marker stages the slice is contracted to print.
    for marker in (
        "generate_templates writes story with no acceptance_criteria",
        "strict_gate BLOCKS on missing_ac",
        "strict_gate PASSES after adding acceptance_criteria",
        "serves 1-1 resolves",
        "serves 1-n resolves",
        "serves n-1 resolves",
        "roadmap effort_rollup == sum",
        "experiment verdict is deterministic",
        "POC gate closes on PASS+PASS",
        "plan-intake brief is markdown, not plan-graph.yaml",
        "dec_ledger allocates 2 unique DECs",
        "PO stories byte-unchanged after every shape op",
    ):
        assert marker in out, "missing expected marker: %r\n\nfull stdout:\n%s" % (marker, out)


def test_slice_summary_reports_zero_failures():
    proc = subprocess.run(
        [sys.executable, str(_SLICE)],
        capture_output=True, text=True, timeout=180,
    )
    assert re.search(r"\b0 failed\b", proc.stdout), proc.stdout[-2000:]
