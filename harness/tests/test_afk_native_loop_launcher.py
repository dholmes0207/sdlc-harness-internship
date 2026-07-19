"""test_afk_native_loop_launcher.py — afk-run.sh AFK_NATIVE_LOOP opt-in branch.

In native-loop mode the launcher drives the loop controller INSIDE the sandbox
image instead of delegating to ralph-afk. The branch must:
  - route to `docker run ... loop_controller.py`, not ralph-afk;
  - preserve isolation — never mount the host docker socket;
  - not require the ralph-afk CLI to be installed.

docker is stubbed to log its `run` invocations so the control flow is exercised
without a real container.
"""

import os
import subprocess
from pathlib import Path

_AFK = Path(__file__).resolve().parent.parent / "afk"
LAUNCHER = _AFK / "afk-run.sh"

_DOCKER_LOGGING = (
    "#!/usr/bin/env bash\n"
    'case "$1" in\n'
    '  run) echo "docker run args=[$*]" >> "$CALLS_LOG" ;;\n'
    "esac\n"
    "exit 0\n"
)
_RALPH_STUB = ("#!/usr/bin/env bash\n"
               'echo "ralph-afk inputs=[$1]" >> "$CALLS_LOG"\nexit 0\n')


def _sandbox(tmp_path, with_ralph=True):
    bind = tmp_path / "bin"
    bind.mkdir()
    stubs = [("docker", _DOCKER_LOGGING)]
    if with_ralph:
        stubs.append(("ralph-afk", _RALPH_STUB))
    for name, body in stubs:
        p = bind / name
        p.write_text(body, encoding="utf-8")
        p.chmod(0o755)
    calls = tmp_path / "calls.log"
    env = dict(os.environ)
    env["PATH"] = "%s:%s" % (bind, env["PATH"])
    env["CALLS_LOG"] = str(calls)
    env["AFK_NATIVE_LOOP"] = "1"
    return env, calls


def _run(env, *args):
    return subprocess.run(["bash", str(LAUNCHER), *args],
                          capture_output=True, text=True, env=env, timeout=60)


def test_native_mode_drives_the_controller_in_docker(tmp_path):
    env, calls = _sandbox(tmp_path)
    proc = _run(env, "./plan.md ./prd.md", "1")
    assert proc.returncode == 0, proc.stderr
    body = calls.read_text(encoding="utf-8")
    assert "loop_controller.py" in body
    assert "ralph-afk" not in body                  # native path, Ralph not driven
    assert "native loop-controller mode" in (proc.stdout + proc.stderr)


def test_native_mode_preserves_isolation_no_socket(tmp_path):
    env, calls = _sandbox(tmp_path)
    _run(env, "./plan.md ./prd.md", "1")
    body = calls.read_text(encoding="utf-8")
    assert "docker.sock" not in body                # never mounts the host socket


def test_native_mode_does_not_require_ralph_cli(tmp_path):
    # ralph-afk absent from PATH — native mode must still run, not exit 69
    env, calls = _sandbox(tmp_path, with_ralph=False)
    proc = _run(env, "./plan.md ./prd.md", "1")
    assert proc.returncode == 0, proc.stderr
    assert "loop_controller.py" in calls.read_text(encoding="utf-8")


def test_native_mode_mounts_claude_auth_when_present(tmp_path):
    # Native mode runs `claude` INSIDE the container; that in-container claude is
    # unauthenticated unless the host's ~/.claude is bind-mounted in. Without it
    # the loop fails opaquely at the first model call. When the host has a
    # ~/.claude, the launcher MUST mount it read-only into the container home.
    env, calls = _sandbox(tmp_path)
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    env["HOME"] = str(home)
    proc = _run(env, "./plan.md ./prd.md", "1")
    assert proc.returncode == 0, proc.stderr
    body = calls.read_text(encoding="utf-8")
    assert "/.claude:/home/agent/.claude:ro" in body  # read-only auth mount present


def test_native_mode_no_auth_mount_when_host_has_none(tmp_path):
    # No host ~/.claude → no mount added (docker would error on a missing source);
    # the unauthenticated run is caught by preflight's claude_auth check, not here.
    env, calls = _sandbox(tmp_path)
    home = tmp_path / "home_noauth"
    home.mkdir()
    env["HOME"] = str(home)
    proc = _run(env, "./plan.md ./prd.md", "1")
    assert proc.returncode == 0, proc.stderr
    body = calls.read_text(encoding="utf-8")
    assert "/home/agent/.claude" not in body
    assert "unauthenticated" in (proc.stdout + proc.stderr).lower()


def test_default_mode_unchanged_still_ralph(tmp_path):
    # without AFK_NATIVE_LOOP the launcher still drives ralph-afk (no regression)
    env, calls = _sandbox(tmp_path)
    env.pop("AFK_NATIVE_LOOP")
    proc = _run(env, "./plan.md ./prd.md", "1")
    assert proc.returncode == 0, proc.stderr
    assert "ralph-afk" in calls.read_text(encoding="utf-8")


# In native mode the controller's exit 1 means a HEALTHY run reached its iteration
# budget — the one-iteration probe always exhausts at N=1, so it must NOT be read
# as a probe failure. Only exit >= 2 (a guard trip / genuine error) aborts the run.
_DOCKER_EXIT = (
    "#!/usr/bin/env bash\n"
    'case "$1" in\n'
    '  run) echo "docker run args=[$*]" >> "$CALLS_LOG"; exit "${STUB_RC:-0}" ;;\n'
    "esac\n"
    "exit 0\n"
)


def _sandbox_exit_stub(tmp_path, rc):
    bind = tmp_path / "bin"
    bind.mkdir()
    p = bind / "docker"
    p.write_text(_DOCKER_EXIT, encoding="utf-8")
    p.chmod(0o755)
    calls = tmp_path / "calls.log"
    env = dict(os.environ)
    env["PATH"] = "%s:%s" % (bind, env["PATH"])
    env["CALLS_LOG"] = str(calls)
    env["AFK_NATIVE_LOOP"] = "1"
    env["STUB_RC"] = str(rc)
    return env, calls


def test_native_probe_exit_1_is_clean_max_iterations(tmp_path):
    # controller exit 1 == clean max-iterations on the probe → the launcher must
    # continue to the remaining iterations, not abort.
    env, calls = _sandbox_exit_stub(tmp_path, rc=1)
    proc = _run(env, "./plan.md ./prd.md", "2")
    assert proc.returncode == 0, proc.stderr
    # both the probe and the remaining-iteration call ran (two docker invocations)
    assert calls.read_text(encoding="utf-8").count("loop_controller.py") == 2
    assert "probe iteration failed" not in (proc.stdout + proc.stderr).replace(
        "probe) iteration failed", "")


def test_native_probe_exit_2_aborts(tmp_path):
    # controller exit >= 2 == a real guard trip / error → the launcher aborts
    # before burning the rest of the budget.
    env, calls = _sandbox_exit_stub(tmp_path, rc=2)
    proc = _run(env, "./plan.md ./prd.md", "3")
    assert proc.returncode == 1
    # only the probe ran; the remaining iterations were not started
    assert calls.read_text(encoding="utf-8").count("loop_controller.py") == 1
