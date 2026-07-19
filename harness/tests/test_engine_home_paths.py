"""engine_home()/engine_current() resolution — the courier's stable install home.

Additive to harness_paths.py: it must NOT touch bin_root/data_root/self-host
semantics (a separate suite locks those). These tests pin only the new resolver:
XDG_DATA_HOME-aware, ~/.local/share default, pure (no mkdir side effect).
"""
import os
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import harness_paths  # noqa: E402


def test_engine_home_honors_xdg(monkeypatch, tmp_path):
    xdg = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    assert harness_paths.engine_home() == (xdg / "harness").resolve() or \
        harness_paths.engine_home() == xdg / "harness"
    # exact contract: <XDG_DATA_HOME>/harness
    assert harness_paths.engine_home() == xdg / "harness"


def test_engine_home_survives_xdg_symlink_loop(monkeypatch, tmp_path):
    """A symlink loop (or ELOOP) on XDG_DATA_HOME must not make engine_home() raise
    RuntimeError('Symlink loop') — the diagnostic verbs (doctor/version) call it before
    any other guard, so it degrades to the un-resolved absolute path instead of crashing."""
    loop = tmp_path / "loop"
    loop.mkdir()
    a, b = loop / "a", loop / "b"
    a.symlink_to(b)
    b.symlink_to(a)  # a -> b -> a
    monkeypatch.setenv("XDG_DATA_HOME", str(a))
    eh = harness_paths.engine_home()  # must NOT raise
    assert eh.name == "harness"


def test_engine_home_default_when_xdg_unset(monkeypatch):
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    expected = Path("~/.local/share").expanduser() / "harness"
    assert harness_paths.engine_home() == expected


def test_engine_home_default_when_xdg_empty(monkeypatch):
    # An empty XDG_DATA_HOME must fall back to the default, not resolve to "/harness".
    monkeypatch.setenv("XDG_DATA_HOME", "")
    expected = Path("~/.local/share").expanduser() / "harness"
    assert harness_paths.engine_home() == expected


def test_engine_current_points_at_current(monkeypatch, tmp_path):
    xdg = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    assert harness_paths.engine_current() == xdg / "harness" / "current"


def test_engine_home_is_pure_no_mkdir(monkeypatch, tmp_path):
    xdg = tmp_path / "xdg-data"
    monkeypatch.setenv("XDG_DATA_HOME", str(xdg))
    _ = harness_paths.engine_home()
    _ = harness_paths.engine_current()
    # Reader must NOT create what it inspects (module discipline).
    assert not (xdg / "harness").exists()


def test_engine_home_does_not_disturb_bin_root(monkeypatch, tmp_path):
    # Adding engine_home must not change bin_root resolution.
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert harness_paths.bin_root() == Path(tmp_path).resolve()
    assert harness_paths.engine_home() == tmp_path / "xdg" / "harness"


def test_engine_home_is_absolute_with_relative_xdg(monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", "relative_xdg")
    assert harness_paths.engine_home().is_absolute()


def test_engine_home_relative_xdg_is_cwd_independent(monkeypatch, tmp_path):
    # A relative XDG_DATA_HOME must NOT make the SHARED engine home CWD-dependent:
    # `.resolve()` on a relative path anchors it to CWD, so two projects would each
    # get their own engine (<cwd>/relative_xdg/harness), defeating the one-shared-
    # engine model. XDG says implementations should ignore a relative value, so it
    # falls back to the ~/.local/share default — identical from any CWD.
    monkeypatch.setenv("XDG_DATA_HOME", "relative_xdg")
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    monkeypatch.chdir(tmp_path / "a")
    from_a = harness_paths.engine_home()
    monkeypatch.chdir(tmp_path / "b")
    from_b = harness_paths.engine_home()
    assert from_a == from_b, "relative XDG made engine_home CWD-dependent: %s vs %s" % (from_a, from_b)
    assert "relative_xdg" not in str(from_a), "relative XDG_DATA_HOME was not ignored"
