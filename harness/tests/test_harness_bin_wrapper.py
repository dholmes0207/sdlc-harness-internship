"""harness/bin/harness — the POSIX-sh dispatcher wrapper.

It resolves its own engine via $0 realpath (a cache layout puts the engine at
<plugin>/engine beside <plugin>/bin/harness) or via HARNESS_BIN_ROOT / the
engine_home 'current' pointer, then execs harness_lifecycle.py. With no engine
resolvable it must fail with an actionable message, not a silent no-op.
"""
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WRAPPER = _REPO_ROOT / "harness" / "bin" / "harness"


def _make_stub_engine(engine_harness: Path):
    """A stub harness_lifecycle.py that echoes its args, so we can assert the
    wrapper found + execed the engine without running the real lifecycle."""
    scripts = engine_harness / "scripts"
    scripts.mkdir(parents=True, exist_ok=True)
    (scripts / "harness_lifecycle.py").write_text(
        "import sys\nprint('LIFECYCLE ' + ' '.join(sys.argv[1:]))\n")


def test_wrapper_execs_sibling_engine(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    wrapper = plugin / "bin" / "harness"
    shutil.copy(_WRAPPER, wrapper)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    _make_stub_engine(plugin / "engine" / "harness")

    env = dict(os.environ)
    env.pop("HARNESS_BIN_ROOT", None)
    env["HOME"] = str(tmp_path / "emptyhome")  # no engine_home here
    out = subprocess.run([str(wrapper), "version", "--x"],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "LIFECYCLE version --x" in out.stdout


def test_wrapper_uses_harness_bin_root(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    wrapper = plugin / "bin" / "harness"
    shutil.copy(_WRAPPER, wrapper)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    # NO sibling engine — resolve via HARNESS_BIN_ROOT instead
    engine_root = tmp_path / "elsewhere"
    _make_stub_engine(engine_root / "harness")

    env = dict(os.environ)
    env["HARNESS_BIN_ROOT"] = str(engine_root)
    env["HOME"] = str(tmp_path / "emptyhome")
    out = subprocess.run([str(wrapper), "doctor"],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "LIFECYCLE doctor" in out.stdout


def test_wrapper_no_engine_actionable(tmp_path):
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    wrapper = plugin / "bin" / "harness"
    shutil.copy(_WRAPPER, wrapper)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    # no sibling engine, no HARNESS_BIN_ROOT, empty HOME → nothing resolves
    env = dict(os.environ)
    env.pop("HARNESS_BIN_ROOT", None)
    env["HOME"] = str(tmp_path / "emptyhome")
    env.pop("XDG_DATA_HOME", None)
    out = subprocess.run([str(wrapper), "version"],
                         capture_output=True, text=True, env=env)
    assert out.returncode != 0
    msg = (out.stderr + out.stdout).lower()
    assert "setup" in msg or "install" in msg, "no actionable guidance in error"


def test_wrapper_resolves_sibling_without_home(tmp_path):
    """Round-12 #2: with HOME and XDG_DATA_HOME both unset, the wrapper must still
    resolve the sibling engine, not abort on `set -u` at the HOME reference."""
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    wrapper = plugin / "bin" / "harness"
    shutil.copy(_WRAPPER, wrapper)
    wrapper.chmod(wrapper.stat().st_mode | stat.S_IEXEC)
    _make_stub_engine(plugin / "engine" / "harness")
    env = {"PATH": os.environ.get("PATH", "")}  # NO HOME, NO XDG_DATA_HOME
    out = subprocess.run([str(wrapper), "version"], capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    assert "LIFECYCLE version" in out.stdout
