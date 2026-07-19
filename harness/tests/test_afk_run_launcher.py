"""test_afk_run_launcher.py — the Ralph-branch launcher's safety reflexes.

afk-run.sh is the GREEN branch: preflight already said the host is ready, so the
launcher's job is narrow but load-bearing. It must:

  - default to the isolated posture (RALPH_DOCKER_SOCK=0, the harness image) and
    REFUSE to mount the host docker socket unless the caller opts in *knowingly*
    with --i-know (an informed opt-out, not a hard block — it prints the blast
    radius);
  - probe with a single iteration first and STOP if that first iteration fails,
    rather than burning the whole N-round budget on a broken setup (bad auth, no
    egress);
  - drive only `ralph-afk` (plan-driven), never `ralph-ghafk`.

The tests stub `docker` and `ralph-afk` on PATH so the launcher's control flow is
exercised without a real container or a real model call.
"""
import os
import subprocess
from pathlib import Path


_AFK = Path(__file__).resolve().parent.parent / "afk"
LAUNCHER = _AFK / "afk-run.sh"

_DOCKER_STUB = "#!/usr/bin/env bash\n# present image, healthy daemon\nexit 0\n"
_RALPH_STUB = (
    "#!/usr/bin/env bash\n"
    'echo "ralph-afk inputs=[$1] iter=[$2]" >> "$CALLS_LOG"\n'
    'exit ${RALPH_AFK_EXIT:-0}\n'
)
_GHAFK_STUB = (
    "#!/usr/bin/env bash\n"
    'echo "ralph-ghafk inputs=[$1] iter=[$2]" >> "$CALLS_LOG"\n'
    "exit 0\n"
)


def _sandbox(tmp_path):
    """A PATH with stubbed docker + ralph-afk + ralph-ghafk and a calls log."""
    bind = tmp_path / "bin"
    bind.mkdir()
    for name, body in (("docker", _DOCKER_STUB), ("ralph-afk", _RALPH_STUB),
                       ("ralph-ghafk", _GHAFK_STUB)):
        p = bind / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    calls = tmp_path / "calls.log"
    env = dict(os.environ)
    env["PATH"] = "%s:%s" % (bind, env["PATH"])
    env["CALLS_LOG"] = str(calls)
    return env, calls


def _run(env, *args, exit_env=None):
    e = dict(env)
    if exit_env:
        e.update(exit_env)
    return subprocess.run(["bash", str(LAUNCHER), *args],
                          capture_output=True, text=True, env=e, timeout=120)


def test_safe_defaults_socket_off(tmp_path):
    env, calls = _sandbox(tmp_path)
    proc = _run(env, "./plan.md ./prd.md", "1")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout + proc.stderr
    assert "RALPH_DOCKER_SOCK=0" in out
    assert "ralph-sandbox-harness" in out
    assert calls.read_text(encoding="utf-8").count("ralph-afk") >= 1


def test_refuses_socket_on_without_iknow(tmp_path):
    env, calls = _sandbox(tmp_path)
    proc = _run(env, "./plan.md ./prd.md", "1",
                exit_env={"RALPH_DOCKER_SOCK": "1"})
    assert proc.returncode != 0
    out = (proc.stdout + proc.stderr).lower()
    assert "--i-know" in out
    assert "blast radius" in out
    # refused before launching anything
    assert not calls.exists() or "ralph-afk" not in calls.read_text("utf-8")


def test_iknow_allows_socket_on_with_warning(tmp_path):
    env, calls = _sandbox(tmp_path)
    proc = _run(env, "--i-know", "./plan.md ./prd.md", "1",
                exit_env={"RALPH_DOCKER_SOCK": "1"})
    assert proc.returncode == 0, proc.stderr
    assert "blast radius" in (proc.stdout + proc.stderr).lower()
    assert "ralph-afk" in calls.read_text(encoding="utf-8")


def test_dry_run_stops_on_first_iteration_failure(tmp_path):
    env, calls = _sandbox(tmp_path)
    proc = _run(env, "./plan.md ./prd.md", "5",
                exit_env={"RALPH_AFK_EXIT": "1"})
    assert proc.returncode != 0
    # the probe iteration ran exactly once; the remaining rounds were NOT burned
    logged = calls.read_text(encoding="utf-8").strip().splitlines()
    assert len(logged) == 1
    assert "iter=[1]" in logged[0]
    assert "iteration" in (proc.stdout + proc.stderr).lower()


def test_probe_then_remaining_on_success(tmp_path):
    env, calls = _sandbox(tmp_path)
    proc = _run(env, "./plan.md ./prd.md", "3")
    assert proc.returncode == 0, proc.stderr
    logged = calls.read_text(encoding="utf-8").strip().splitlines()
    # probe with 1, then the remaining N-1 — total budget == N
    assert len(logged) == 2
    assert "iter=[1]" in logged[0]
    assert "iter=[2]" in logged[1]


def test_drives_ralph_afk_not_ghafk(tmp_path):
    env, calls = _sandbox(tmp_path)
    _run(env, "./plan.md ./prd.md", "2")
    body = calls.read_text(encoding="utf-8")
    assert "ralph-afk" in body
    assert "ralph-ghafk" not in body
