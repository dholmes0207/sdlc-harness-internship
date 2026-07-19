"""The live lanes (real gemini/agy/sdk/host CLIs) must be OFF by default.

A bare `pytest` run — the CLAUDE.md command `python3 -m pytest harness/tests/ -q`
and the ci.sh harness step — must NOT collect a `real_*`-marked test: those spawn a
live CLI (browser OAuth for agy, token+network for gemini) and are opt-in only. The
gate is `addopts = -m "not real_* ..."` in pyproject.toml; naming the marker on the
CLI (`pytest -m real_gemini`) OVERRIDES addopts and runs exactly that lane.

Behavioral, not config-reading: a real `--collect-only` subprocess proves the
default deselects the lane and the explicit opt-in re-selects it, so deleting the
addopts line reds this test instead of silently re-arming the live lanes.
"""
import subprocess
import sys
from pathlib import Path

import pytest

# Dev-repo-only: pins the repo-root pyproject addopts contract; an installed bundle
# runs under its own config and need not carry this gate.
pytestmark = pytest.mark.dev_repo

_REPO = Path(__file__).resolve().parents[2]
_LIVE_NODE = "test_t7_real_gemini_print_handshake"  # a @pytest.mark.real_gemini test
_GEMINI_FILE = "harness/tests/test_gemini_print_transport_gemini.py"


def _collect(*extra):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-p", "no:cacheprovider", _GEMINI_FILE, *extra],
        capture_output=True, text=True, cwd=str(_REPO),
    )


def test_default_run_deselects_live_gemini_lane():
    res = _collect()
    assert _LIVE_NODE not in res.stdout, (
        "a real_gemini live-lane test is collected by a bare pytest run — the "
        "pyproject addopts default-deselect is missing:\n" + res.stdout)


def test_explicit_marker_opt_in_selects_the_lane():
    res = _collect("-m", "real_gemini")
    assert _LIVE_NODE in res.stdout, (
        "naming -m real_gemini must OVERRIDE addopts and collect the live lane:\n"
        + res.stdout + res.stderr)
