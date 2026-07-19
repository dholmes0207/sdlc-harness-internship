"""test_cli_wrapper.py — the on-PATH hs-cli launcher + its opt-in install.

16 docs advertise a bare `hs-cli` command; until now `harness/bin/` did not
exist, so it was command-not-found. These pin: the POSIX launcher delegates
verbatim to hs_cli.py (resolving the repo from its own location), the Windows
twin exists, and the opt-in install drops a no-clobber symlink on PATH.
"""
import os
import subprocess
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parent.parent.parent
_BIN = _ROOT / "harness" / "bin"
_INSTALL = _ROOT / "harness" / "install"
if str(_INSTALL) not in sys.path:
    sys.path.insert(0, str(_INSTALL))

import install  # noqa: E402


# --- the launchers ------------------------------------------------------------

def test_posix_wrapper_exists_and_executable():
    w = _BIN / "hs-cli"
    assert w.is_file()
    assert os.access(w, os.X_OK), "hs-cli must ship executable"


def test_windows_wrapper_exists_and_targets_cli():
    w = _BIN / "hs-cli.cmd"
    assert w.is_file()
    assert "hs_cli.py" in w.read_text()


def test_posix_wrapper_delegates_verbatim(tmp_path):
    # run from an unrelated CWD: the launcher must still find the repo and give
    # byte-identical output to the direct python invocation
    direct = subprocess.run([sys.executable, str(_ROOT / "harness/scripts/hs_cli.py"),
                             "version"], capture_output=True, text=True, cwd=str(tmp_path))
    viacli = subprocess.run([str(_BIN / "hs-cli"), "version"],
                            capture_output=True, text=True, cwd=str(tmp_path))
    assert viacli.returncode == direct.returncode
    assert viacli.stdout == direct.stdout


# --- opt-in install: no-clobber symlink on PATH -------------------------------

def _result():
    return {"actions": [], "warnings": [], "problems": [], "skipped_events": []}


def test_wire_cli_creates_symlink(tmp_path):
    bindir = tmp_path / "bin"
    r = _result()
    install._wire_cli(_ROOT, _ROOT, r, dry_run=False, bindir_override=str(bindir))
    link = bindir / "hs-cli"
    assert link.is_symlink()
    assert link.resolve() == (_ROOT / "harness" / "bin" / "hs-cli").resolve()
    assert any("hs-cli" in a for a in r["actions"])


def test_wire_cli_no_clobber(tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    existing = bindir / "hs-cli"
    existing.write_text("#!/bin/sh\necho mine\n")  # a pre-existing user launcher
    r = _result()
    install._wire_cli(_ROOT, _ROOT, r, dry_run=False, bindir_override=str(bindir))
    assert existing.read_text() == "#!/bin/sh\necho mine\n"  # untouched
    assert not existing.is_symlink()
    assert any("left as-is" in a for a in r["actions"])


def test_wire_cli_dry_run_writes_nothing(tmp_path):
    bindir = tmp_path / "bin"
    r = _result()
    install._wire_cli(_ROOT, _ROOT, r, dry_run=True, bindir_override=str(bindir))
    assert not (bindir / "hs-cli").exists()


def test_wire_cli_missing_wrapper_warns(tmp_path):
    # a target tree with no harness/bin/hs-cli → warn, do not crash
    r = _result()
    install._wire_cli(tmp_path, tmp_path, r, dry_run=False,
                      bindir_override=str(tmp_path / "bin"))
    assert any("wrapper missing" in w for w in r["warnings"])
