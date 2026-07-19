"""test_install_ps1_parity — the Windows installer mirrors install.sh's contract.

install.ps1 is the PowerShell sibling of install.sh; the two must not drift on the
five load-bearing steps (verify -> extract -> deps -> install+verify -> test) or on
the safety properties (snapshot order, guarded cleanup, temp-dir teardown). pwsh is
not available on the dev/CI host, so this pins the wiring structurally rather than
by execution — same approach as test_install_cleanup_wiring for install.sh.
"""
from pathlib import Path
import pytest

_ROOT = Path(__file__).resolve().parents[2]
_PS1 = _ROOT / "release/install.ps1"



# asserts full-catalog / dev-tree skill provenance; auto-skipped on
# an installed default-off copy where those skills are stashed.
pytestmark = pytest.mark.dev_repo

def _text():
    return _PS1.read_text(encoding="utf-8")


def _lines():
    return _text().splitlines()


def _index_of(substr):
    for i, ln in enumerate(_lines()):
        if substr in ln:
            return i
    return -1


def test_ps1_exists():
    assert _PS1.is_file(), "release/install.ps1 must ship alongside install.sh"


def test_five_steps_present():
    """Every load-bearing step of install.sh has a counterpart here."""
    txt = _text()
    for needle in (
        "Get-FileHash",                 # 1. sha256 verify
        "tarfile",                      # 2. extract (portable, no tar.exe)
        "preflight_deps.py",            # 3. deps first
        "install.py",                   # 4. install
        "--strict",                     #    strict verify
        "cleanup_orphans.py",           # 4b. orphan cleanup
        "pytest",                       # 5. test suite
    ):
        assert needle in txt, f"install.ps1 missing load-bearing step: {needle}"


def test_snapshot_between_extract_and_install():
    """Old manifest snapshotted AFTER extract, BEFORE the install.py copy (F8)."""
    extract_i = _index_of("extractall") if _index_of("extractall") >= 0 else _index_of("Invoke-Py @($guardPy")
    snap_i = _index_of("Copy-Item -LiteralPath $targetManifest")
    install_i = _index_of("$installPy = Join-Path")
    assert extract_i >= 0 and snap_i >= 0 and install_i >= 0, \
        "missing extract/snapshot/install anchors"
    assert extract_i < snap_i < install_i, \
        "snapshot must sit AFTER extract and BEFORE the install.py copy"


def test_cleanup_failure_does_not_fail_install():
    """A non-zero cleanup exit defers to hs:cleanup instead of aborting (F3)."""
    cleanup_i = _index_of("cleanup_orphans.py")
    assert cleanup_i >= 0
    window = "\n".join(_lines()[cleanup_i:cleanup_i + 3])
    assert "deferred" in window.lower(), "cleanup must be guarded, not fatal"


def test_first_time_install_is_noop_cleanup():
    """The cleanup block only runs when an old manifest was snapshotted."""
    assert "if ($OldManifest)" in _text()


def test_python_probe_prefers_py_launcher():
    """py -3 is probed before python/python3 to dodge the Store alias stub."""
    txt = _text()
    py_i = txt.find("exe = 'py'")
    python_i = txt.find("exe = 'python'")
    assert 0 <= py_i < python_i, "py -3 must be probed before bare python"
    assert "3, 9" in txt or "3,9" in txt, "must pin Python >=3.9"


def test_tempdir_torn_down_in_finally():
    """The extract dir is removed on every exit path (set -e trap equivalent)."""
    txt = _text()
    assert "} finally {" in txt
    assert "Remove-Item -LiteralPath $Work" in txt


def test_reviewers_env_passthrough():
    """REVIEWERS env seeds the roster headlessly, mirroring install.sh."""
    assert "$env:REVIEWERS" in _text()


def test_skip_tests_switch():
    """-SkipTests / -NoTests mirrors the shell --skip-tests/--no-tests flag."""
    txt = _text()
    assert "$SkipTests" in txt and "NoTests" in txt
