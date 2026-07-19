"""Regression wrapper for the courier e2e slice — runs the real end-to-end script
(setup / two-zone guard block / skew advisory / uninstall / self-host silence) in
a temp HOME and asserts it exits clean. A defect in phases 2-6 reddens here.
"""
import subprocess
import sys
from pathlib import Path

_SLICE = Path(__file__).resolve().parents[1] / "e2e" / "run_courier_slice.py"


def test_courier_slice_passes():
    r = subprocess.run([sys.executable, str(_SLICE)],
                       capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stdout + "\n" + r.stderr
    assert "0 failed" in r.stdout
