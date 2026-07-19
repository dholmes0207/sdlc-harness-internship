#!/usr/bin/env python3
"""Trust is granted ONLY by a deliberate operator action (`hs-cli trust`, run by
/hs:setup), NEVER as a silent side effect of installing the harness.

This is the F1 invariant: installing the harness into a repo must not auto-trust
that repo, or installing into a hostile checkout would let its standards.user.yaml
shell detectors auto-fire (RCE). The trust grant lives behind the explicit setup
verb instead — the operator saying "this repo is mine".
"""

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
_INSTALL_DIR = _REPO_ROOT / "harness" / "install"
for _p in (_SCRIPTS, _INSTALL_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import hs_cli  # noqa: E402
import install as installer  # noqa: E402
import trust_store  # noqa: E402


@pytest.fixture
def store(tmp_path, monkeypatch):
    """An isolated per-test trust store."""
    p = tmp_path / "trust.json"
    monkeypatch.setenv("HARNESS_TRUST_STORE", str(p))
    return p


# 1 — the verb /hs:setup invokes records the repo as trusted.
def test_trust_verb_records_repo(tmp_path, store):
    repo = tmp_path / "repo"
    repo.mkdir()
    rc = hs_cli.main(["trust", str(repo)])
    assert rc == 0
    assert trust_store.is_trusted(str(repo)) is True


# 2 — trusting twice is idempotent (one entry, no error).
def test_trust_is_idempotent(tmp_path, store):
    repo = tmp_path / "repo"
    repo.mkdir()
    assert hs_cli.main(["trust", str(repo)]) == 0
    assert hs_cli.main(["trust", str(repo)]) == 0
    assert trust_store.load_trust() == {str(Path(repo).resolve())}


# 3 — a symlinked root is refused: exit 2 + a stderr reason, no crash.
def test_trust_symlink_refused(tmp_path, store, capsys):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    rc = hs_cli.main(["trust", str(link)])
    assert rc == 2
    assert "refused" in capsys.readouterr().err.lower()
    assert trust_store.is_trusted(str(link)) is False


# 4 — F1 static lock: the install path never grants trust.


# 5 — F1 functional lock: installing into a fresh repo does NOT trust it.
def test_install_does_not_auto_trust(tmp_path, store):
    target = tmp_path / "target"
    target.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=target, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=target, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=target, check=True)

    res = installer.install(_REPO_ROOT, target, strict=False)
    assert res["ok"], res.get("problems")
    assert trust_store.is_trusted(str(target)) is False, \
        "install must not auto-trust the target (F1 — RCE guard)"
