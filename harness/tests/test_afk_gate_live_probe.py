"""Tests for gate_live_probe.py — the deterministic in-image gate probe.

docker_available is a two-part check (CLI on PATH AND daemon answering); probe
shells out and must never raise on a docker failure, returning fired=False with a
detail string instead. subprocess is mocked so no real container runs.
"""

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "afk"))
import gate_live_probe as glp  # noqa: E402


class _Proc:
    def __init__(self, returncode=0, stderr="", stdout=""):
        self.returncode = returncode
        self.stderr = stderr
        self.stdout = stdout


def test_docker_available_false_when_cli_absent(monkeypatch):
    monkeypatch.setattr(glp.shutil, "which", lambda _b: None)
    assert glp.docker_available() is False


def test_docker_available_true_when_daemon_answers(monkeypatch):
    monkeypatch.setattr(glp.shutil, "which", lambda _b: "/usr/bin/docker")
    monkeypatch.setattr(glp.subprocess, "run", lambda *a, **k: _Proc(returncode=0))
    assert glp.docker_available() is True


def test_docker_available_false_when_daemon_down(monkeypatch):
    monkeypatch.setattr(glp.shutil, "which", lambda _b: "/usr/bin/docker")
    monkeypatch.setattr(glp.subprocess, "run", lambda *a, **k: _Proc(returncode=1))
    assert glp.docker_available() is False


def test_docker_available_false_on_run_error(monkeypatch):
    monkeypatch.setattr(glp.shutil, "which", lambda _b: "/usr/bin/docker")

    def _boom(*a, **k):
        raise OSError("no daemon")

    monkeypatch.setattr(glp.subprocess, "run", _boom)
    assert glp.docker_available() is False


def test_probe_fires_when_gate_blocks_push(monkeypatch, tmp_path):
    # exit 2 + a reason naming the verification artifact == gate is live in-image.
    monkeypatch.setattr(
        glp.subprocess, "run",
        lambda *a, **k: _Proc(returncode=2, stderr="blocked: missing verification.json"))
    res = glp.probe("img", tmp_path)
    assert res.fired is True
    assert res.exit_code == 2


def test_probe_does_not_fire_on_bare_image(monkeypatch, tmp_path):
    # a bare image cannot produce the fail-closed block → not live.
    monkeypatch.setattr(
        glp.subprocess, "run",
        lambda *a, **k: _Proc(returncode=127, stderr="python3: not found"))
    res = glp.probe("img", tmp_path)
    assert res.fired is False


def test_probe_never_raises_on_docker_failure(monkeypatch, tmp_path):
    def _boom(*a, **k):
        raise OSError("docker missing")

    monkeypatch.setattr(glp.subprocess, "run", _boom)
    res = glp.probe("img", tmp_path)
    assert res.fired is False
    assert res.exit_code is None
    assert "failed" in res.detail


def test_probe_handles_timeout(monkeypatch, tmp_path):
    def _timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="docker", timeout=1)

    monkeypatch.setattr(glp.subprocess, "run", _timeout)
    res = glp.probe("img", tmp_path, timeout=1)
    assert res.fired is False
    assert "timed out" in res.detail


def test_probe_creates_plans_dir_when_absent(monkeypatch, tmp_path):
    """probe() must create repo_root/plans/ before the docker run so the :ro
    /work bind-mount can be overlaid at /work/plans even on checkouts where
    plans/ is gitignored and absent."""
    # tmp_path is a repo root with NO plans/ directory
    assert not (tmp_path / "plans").exists(), "pre-condition: no plans/ dir"

    monkeypatch.setattr(
        glp.subprocess, "run",
        lambda *a, **k: _Proc(returncode=2, stderr="blocked: missing verification.json"))

    glp.probe("img", tmp_path)

    assert (tmp_path / "plans").exists(), "probe must ensure plans/ on host before docker run"


def test_probe_plans_dir_ensure_idempotent(monkeypatch, tmp_path):
    """probe() must not fail if plans/ already exists."""
    (tmp_path / "plans").mkdir()

    monkeypatch.setattr(
        glp.subprocess, "run",
        lambda *a, **k: _Proc(returncode=0, stderr=""))

    # Must not raise even though plans/ already exists
    res = glp.probe("img", tmp_path)
    assert res is not None
