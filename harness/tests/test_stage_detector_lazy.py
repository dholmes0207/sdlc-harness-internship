"""Perf-contract pins for stage_detector's 2-tier detect path.

The full behavior table lives in test_stage_detector.py; these tests guard only
the COST path that standards_drift_nudge.handle_commit pays on every
PreToolUse:Bash (it imports stage_detector and calls detect_stage per command):

  * the heavy stage/guess/wrapper regex batteries compile LAZILY, not at import;
  * a command carrying no known tool/wrapper token short-circuits (tier 1) and
    returns None WITHOUT compiling or running the battery.

Both assertions run in a fresh interpreter so the module's lazy caches are
pristine — an in-process check would see caches another test already warmed.
"""
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import stage_detector  # noqa: E402


def _probe(body: str):
    code = ("import sys; sys.path.insert(0, %r); import stage_detector as s\n%s\nprint('ok')"
            % (str(_SCRIPTS), body))
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "ok" in out.stdout


def test_batteries_not_compiled_at_import():
    _probe(
        "assert s._STAGE_PATTERNS is None, 'stage battery eagerly compiled at import'\n"
        "assert s._GUESS_PATTERNS is None, 'guess battery eagerly compiled at import'\n"
        "assert s._WRAPPERS is None, 'wrapper battery eagerly compiled at import'"
    )


def test_non_trigger_command_compiles_no_battery():
    # tier 1: no tool/wrapper token -> return None before the battery is compiled.
    _probe(
        "assert s.detect_stage('ls -la') is None\n"
        "assert s.detect_stage('python3 -m pytest -q') is None\n"
        "assert s._STAGE_PATTERNS is None, 'battery compiled for a non-trigger command'\n"
        "assert s._WRAPPERS is None, 'wrapper battery compiled for a non-trigger command'"
    )


def test_trigger_command_compiles_battery():
    _probe(
        "assert s.detect_stage('git push') == 'push'\n"
        "assert s._STAGE_PATTERNS is not None, 'battery not compiled on a real trigger'"
    )


def test_tier1_short_circuits_before_battery(monkeypatch):
    # A non-trigger command must not reach the lazily-compiled batteries at all.
    def _boom(*_a, **_k):
        raise AssertionError("tier-2 battery reached for a non-trigger command")
    monkeypatch.setattr(stage_detector, "_stage_patterns", _boom)
    monkeypatch.setattr(stage_detector, "_wrappers", _boom)
    for cmd in ("ls -la", "cat foo.txt", "grep release notes.md", "mkdir -p x/y"):
        assert stage_detector.detect_stage(cmd) is None
