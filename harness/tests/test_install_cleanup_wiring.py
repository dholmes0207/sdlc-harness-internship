"""test_install_cleanup_wiring — install.sh wires the cleanup engine safely.

Two load-bearing properties of the shell wiring, pinned structurally (running the
full installer is covered by test_install_seam):
  - F8: the OLD manifest is snapshotted from $TARGET, AFTER extract but BEFORE the
        install.py copy overwrites it.
  - F3: a cleanup failure must never fail the install (the harness is already in
        place) — the call is guarded with `|| echo ... deferred`.
Plus a behavioral check that a modified orphan is deferred, not deleted.
"""
import sys
import pytest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_SH = _ROOT / "release/install.sh"
_SCRIPTS = _ROOT / "harness/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import hashlib  # noqa: E402

import cleanup_orphans as co  # noqa: E402


def _lines():
    return _INSTALL_SH.read_text(encoding="utf-8").splitlines()


def _index_of(substr):
    for i, ln in enumerate(_lines()):
        if substr in ln:
            return i
    return -1


@pytest.mark.dev_repo
def test_install_snapshots_before_copy():
    """Snapshot the old manifest from $TARGET, between extract and install.py."""
    tar_i = _index_of("tar -xzf")
    snap_i = _index_of('cp "$TARGET/harness/manifest.json"')
    install_i = _index_of("install/install.py")
    assert tar_i >= 0 and snap_i >= 0 and install_i >= 0, \
        "missing tar/snapshot/install.py lines"
    assert tar_i < snap_i < install_i, \
        "snapshot must sit AFTER tar -xzf and BEFORE install.py copy"
    # reads from the TARGET's existing manifest, not the freshly-extracted $WORK
    snap_line = _lines()[snap_i]
    assert "$TARGET/harness/manifest.json" in snap_line


@pytest.mark.dev_repo
def test_cleanup_failure_does_not_fail_install():
    """The cleanup_orphans call is guarded so a non-zero exit can't abort install."""
    txt = _INSTALL_SH.read_text(encoding="utf-8")
    assert "cleanup_orphans.py" in txt
    # find the cleanup invocation and confirm a `|| echo ... deferred` guard
    cleanup_i = _index_of("cleanup_orphans.py")
    window = "\n".join(_lines()[cleanup_i:cleanup_i + 3])
    assert "|| echo" in window and "deferred" in window.lower()


@pytest.mark.dev_repo
def test_install_first_time_noop():
    """The cleanup block only runs when an old manifest was snapshotted."""
    txt = _INSTALL_SH.read_text(encoding="utf-8")
    assert 'if [ -n "$OLD_MANIFEST" ]' in txt


def test_install_defers_modified(tmp_path, capsys):
    """A modified orphan is reported for hs:cleanup, never auto-removed."""
    import json
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness/manifest.json").write_text(json.dumps({"files": {}}))
    (tmp_path / "harness/mod.py").write_bytes(b"changed")
    old_hash = hashlib.sha256(b"original").hexdigest()
    snap = tmp_path / "old-manifest.json"
    snap.write_text(json.dumps({"files": {"harness/mod.py": old_hash}}))

    co.main(["--target", str(tmp_path), "--old-manifest", str(snap), "--apply"])
    out = capsys.readouterr().out
    assert "hs:cleanup" in out
    assert (tmp_path / "harness/mod.py").read_bytes() == b"changed"  # not deleted


def _index_of_exec_strict_gate():
    """Index of the EXECUTED strict-verify gate line (a run line), not the
    trailing echo hint."""
    for i, ln in enumerate(_lines()):
        if "verify_install.py" in ln and "--strict" in ln and "echo" not in ln:
            return i
    return -1


@pytest.mark.dev_repo
def test_durable_snapshot_persisted_before_install():
    """BUG3: the durable snapshot (harness/state/cleanup-prev-manifest.json) must
    be written BEFORE install.py — else a strict abort in the install step skips
    the persist and the first upgrade after a fresh install has nothing to diff."""
    persist_i = _index_of("cleanup-prev-manifest.json")
    install_i = _index_of("install/install.py")
    assert persist_i >= 0 and install_i >= 0, "missing persist or install.py line"
    assert persist_i < install_i, \
        "durable snapshot must persist before the (abortable) install step"


@pytest.mark.dev_repo
def test_cleanup_runs_before_strict_verify_gate():
    """BUG2: cleanup_orphans must run BEFORE the strict verify gate, so orphans a
    dropped version left behind are removed before the gate that would otherwise
    abort on them under set -e (the fresh copy is valid; orphans are the drift)."""
    cleanup_i = _index_of("cleanup_orphans.py")
    gate_i = _index_of_exec_strict_gate()
    assert cleanup_i >= 0 and gate_i >= 0, "missing cleanup or executed strict gate"
    assert cleanup_i < gate_i, "cleanup must run before the strict verify gate"


@pytest.mark.dev_repo
def test_no_reviewers_flag():
    """--reviewers / REVIEWERS was removed in v4.0.0 (personal-first). install.sh
    passing it makes install.py abort on an unrecognized argument."""
    txt = _INSTALL_SH.read_text(encoding="utf-8")
    assert "--reviewers" not in txt, "install.sh still passes the removed --reviewers flag"
    assert "REVIEWERS" not in txt, "install.sh still references the removed REVIEWERS env"


@pytest.mark.dev_repo
def test_installed_suite_uses_confcutdir():
    """BUG4: step 5 runs the suite from $TARGET (a host repo). Without --confcutdir a
    host conftest.py ABOVE the installed harness/ tree gets loaded and can abort
    collection (e.g. it imports a package the host has but the harness does not).
    Pin the conftest boundary at the installed harness/ tree."""
    line = next((l for l in _lines() if "pytest" in l and "harness/tests" in l), "")
    assert "--confcutdir" in line, \
        "installed-target pytest run must set --confcutdir to isolate host conftest"


@pytest.mark.dev_repo
def test_ci_layout_tests_marked_dev_repo():
    """BUG4: dev-repo-layout tests (assert .github/workflows, scripts/ci.sh — absent
    on an installed target) must be dev_repo-marked so conftest skips them there."""
    for name in ("test_ci_dispatcher", "test_ci_job_parity", "test_receipts_gate_wiring"):
        src = (_ROOT / "harness/tests" / (name + ".py")).read_text(encoding="utf-8")
        assert "pytest.mark.dev_repo" in src, name + " is not marked dev_repo"
