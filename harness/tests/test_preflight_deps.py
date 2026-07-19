"""Tests for preflight_deps.py — the dependency gate.

The harness declares a small set of external deps (pyyaml, pytest, defusedxml).
preflight_deps.py must detect missing ones, print the exact pip install command,
and exit non-zero so hooks/install fail fast rather than surfacing an opaque
ImportError mid-run.
"""
import sys
from io import StringIO
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
PREFLIGHT = REPO_ROOT / "harness" / "scripts" / "preflight_deps.py"

# Import the module under test by path so assertions target the same code.
spec = __import__("importlib.util").util.spec_from_file_location(
    "preflight_deps", PREFLIGHT)
preflight = __import__("importlib.util").util.module_from_spec(spec)
spec.loader.exec_module(preflight)


def test_missing_required_lists_install_command():
    """If all deps are missing the user gets a single pip install command."""
    with mock.patch.object(preflight, "missing_required", return_value=["pyyaml", "pytest", "defusedxml"]):
        stderr = StringIO()
        stdout = StringIO()
        old_err, old_out = sys.stderr, sys.stdout
        sys.stderr, sys.stdout = stderr, stdout
        try:
            rc = preflight.main([])
        finally:
            sys.stderr, sys.stdout = old_err, old_out
        assert rc == 1
        msg = stderr.getvalue()
        assert "missing dependencies" in msg
        assert "pip install defusedxml pytest pyyaml" in msg


def test_no_missing_required_exits_zero():
    """When every dep is importable, preflight exits 0 and reports OK."""
    with mock.patch.object(preflight, "missing_required", return_value=[]):
        stderr = StringIO()
        stdout = StringIO()
        old_err, old_out = sys.stderr, sys.stdout
        sys.stderr, sys.stdout = stderr, stdout
        try:
            rc = preflight.main([])
        finally:
            sys.stderr, sys.stdout = old_err, old_out
        assert rc == 0
        assert "preflight OK" in stdout.getvalue()


def test_quiet_mode_outputs_nothing():
    """--quiet should be silent and only communicate via exit code."""
    with mock.patch.object(preflight, "missing_required", return_value=["defusedxml"]):
        stderr = StringIO()
        stdout = StringIO()
        old_err, old_out = sys.stderr, sys.stdout
        sys.stderr, sys.stdout = stderr, stdout
        try:
            rc = preflight.main(["--quiet"])
        finally:
            sys.stderr, sys.stdout = old_err, old_out
        assert rc == 1
        assert stderr.getvalue() == ""
        assert stdout.getvalue() == ""


def test_missing_defusedxml_drawio_skill_surfaces_clearly():
    """Regression: drawio scripts/edit_drawio.py and tests/test_drawio_edit.py
    import defusedxml; a missing dep must surface as a preflight failure with a
    concrete install command, not as a bare ModuleNotFoundError."""
    with mock.patch.object(preflight, "missing_required", return_value=["defusedxml"]):
        stderr = StringIO()
        old_err = sys.stderr
        sys.stderr = stderr
        try:
            rc = preflight.main([])
        finally:
            sys.stderr = old_err
        assert rc == 1
        err = stderr.getvalue()
        assert "defusedxml" in err
        assert "pip install defusedxml" in err


@pytest.mark.parametrize("module,pip_name", [
    ("yaml", "pyyaml"),
    ("pytest", "pytest"),
    ("defusedxml", "defusedxml"),
])
def test_missing_single_dep(module, pip_name):
    """missing_required detects each individually required module."""
    def fake_import(name, package=None):
        if name == module:
            raise ImportError(f"No module named {name!r}")
        return __import__(name)

    with mock.patch("importlib.import_module", side_effect=fake_import):
        assert preflight.missing_required() == [pip_name]
