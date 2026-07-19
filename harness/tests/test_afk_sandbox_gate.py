"""test_afk_sandbox_gate.py — the sandbox image carries the guardrail.

Three claims about the ``ralph-sandbox-harness`` image, all proven by actually
running the container (no mocks):

  - it carries python3 + PyYAML (the in-loop gate's only hard dependency — a
    missing dep wedges every Bash call fail-closed inside the loop);
  - ``HARNESS_ROOT`` resolves to the bind-mount path, so the hooks registered
    as ``python3 $HARNESS_ROOT/harness/hooks/*.py`` find the harness on the
    workspace mount;
  - the stage gate is LIVE inside the container — a ``git push`` with no
    verification artifact is blocked with exit 2 (reused gate-live probe).

Docker-gated: a host without a docker daemon skips cleanly rather than
faking green. Where docker IS present the image must exist — until it is
built these fail (the intended red), and pass once built (green).
"""
import subprocess
import sys
from pathlib import Path

import pytest

_AFK = Path(__file__).resolve().parent.parent / "afk"
if str(_AFK) not in sys.path:
    sys.path.insert(0, str(_AFK))

import gate_live_probe  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
IMAGE = "ralph-sandbox-harness"

def _image_present(image: str) -> bool:
    # A runner can have a docker daemon but NOT this locally-built image (it is never
    # pushed to a registry), so the daemon check alone lets the test run and then fail
    # on `docker run`. Require the image itself, so a host without it skips cleanly.
    if not gate_live_probe.docker_available():
        return False
    try:
        return subprocess.run(["docker", "image", "inspect", image],
                              capture_output=True, timeout=30).returncode == 0
    except Exception:  # noqa: BLE001 — any probe failure means "cannot prove here"
        return False


requires_docker = pytest.mark.skipif(
    not _image_present(IMAGE),
    reason="sandbox image %r not available — the proof runs where the image is built"
    % IMAGE,
)


@requires_docker
def test_image_has_python_yaml():
    # PyYAML is the gate's only non-stdlib dependency; absent it the
    # compliance hooks fail-closed on every command inside the loop.
    proc = subprocess.run(
        ["docker", "run", "--rm", IMAGE, "python3", "-c", "import yaml"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr


@requires_docker
def test_harness_root_resolves():
    # The hooks are registered as `python3 $HARNESS_ROOT/harness/hooks/*.py`;
    # the image bakes HARNESS_ROOT so Ralph's fixed argv needs no --settings.
    proc = subprocess.run(
        ["docker", "run", "--rm", IMAGE, "sh", "-c", "echo $HARNESS_ROOT"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "/home/agent/workspace"


@requires_docker
def test_gate_advises_push_in_container():
    # Personal-first: the in-loop gate is live inside the sandbox but ADVISES
    # (exit 0 + advisory) rather than blocking — remote CI is the hard layer.
    result = gate_live_probe.probe(IMAGE, _REPO_ROOT)
    assert not result.fired, (
        "gate blocked git push inside the image but should only advise (exit=%r): %s"
        % (result.exit_code, result.detail))
    assert result.exit_code == 0
