"""test_e2e_native_slice.py — opt-in wrapper for the native-transport e2e.

The native slice drives a real `claude -p` (slow, costs tokens), so it stays OUT
of the default CI: this test SKIPS unless HARNESS_E2E_NATIVE=1 is set AND claude
is on PATH. The fast simulated slice (run_vertical_slice) remains the CI gate;
this is the on-demand native-transport proof. When opted in, it asserts the slice
reports a clean block-then-pass over native-claude-p transport.
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_SLICE = Path(__file__).resolve().parent.parent / "e2e" / "run_native_slice.py"


@pytest.mark.skipif(
    os.environ.get("HARNESS_E2E_NATIVE") != "1" or not shutil.which("claude"),
    reason="native e2e is opt-in (HARNESS_E2E_NATIVE=1) and needs claude on PATH",
)
def test_native_slice_block_then_pass():
    proc = subprocess.run([sys.executable, str(_SLICE)],
                          capture_output=True, text=True, timeout=600)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "transport=native-claude-p" in proc.stdout
    assert "0 failed" in proc.stdout


def test_native_slice_skips_cleanly_without_optin(monkeypatch):
    # Without the opt-in env the slice must SKIP and exit 0 — never run a model
    # call by accident in CI.
    monkeypatch.delenv("HARNESS_E2E_NATIVE", raising=False)
    proc = subprocess.run([sys.executable, str(_SLICE)],
                          capture_output=True, text=True, timeout=60)
    assert proc.returncode == 0
    assert "SKIP" in proc.stdout
