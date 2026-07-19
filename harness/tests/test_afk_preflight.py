"""test_afk_preflight.py — the AFK readiness probe is fail-open by construction.

preflight() is an *advisory*, not a gate: it answers "is this host ready to run
an unattended Ralph loop?" and hands the orchestrator structured findings. The
load-bearing property it must NEVER violate is fail-open — it must not raise and
must not exit-block, no matter how broken the host is. A readiness probe that
crashes would itself become the thing standing between the user and a fallback.

So these tests inject broken hosts (no docker, missing image, arm cpu, absent
inputs, a dead gate-live probe, a check that raises) and assert two things every
time: the finding for that condition carries the right status + an actionable
fix + a fallback hint, AND the call returns a list rather than raising.
"""
import subprocess
import sys
from pathlib import Path


_AFK = Path(__file__).resolve().parent.parent / "afk"
if str(_AFK) not in sys.path:
    sys.path.insert(0, str(_AFK))

import gate_live_probe  # noqa: E402
import preflight  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _healthy_run(cmd, *a, **k):
    """A subprocess.run stub for a host where everything works."""
    joined = " ".join(map(str, cmd))
    if "info" in cmd:                       # docker info
        return FakeProc(0)
    if "inspect" in cmd:                    # docker image inspect
        return FakeProc(0, stdout="[{}]")
    if "import yaml" in joined:             # docker run <img> python3 -c import yaml
        return FakeProc(0)
    if "--version" in cmd:                  # ralph-afk --version
        return FakeProc(0, stdout="ralph-afk 1.0.0\n")
    return FakeProc(0)


def _healthy_which(binary):
    return "/usr/bin/" + binary


def _fired_probe(image, repo_root, **k):
    return gate_live_probe.ProbeResult(True, 2, "blocked: verification missing")


def _healthy_kwargs(tmp_path):
    """A fully-green host: every injectable points at a working stub."""
    plan = tmp_path / "plan.md"
    prd = tmp_path / "prd.md"
    plan.write_text("plan", encoding="utf-8")
    prd.write_text("prd", encoding="utf-8")
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    workspace = tmp_path / "ws"
    (workspace / ".git").mkdir(parents=True)
    return dict(
        plan_path=str(plan), prd_path=str(prd),
        workspace=str(workspace), repo_root=str(_REPO_ROOT),
        machine="x86_64", home=str(home),
        run=_healthy_run, which=_healthy_which, probe=_fired_probe,
        tested_with={"ralph_cli": "1.0.0"},
    )


# ---- the headline guarantee: never raise, always a list ---------------------

def test_preflight_returns_list_on_totally_broken_host():
    # No docker, bogus image, arm cpu, missing inputs, dead probe — preflight
    # must still return findings rather than raising.
    findings = preflight.preflight(
        plan_path="/does/not/exist/plan.md",
        prd_path="/does/not/exist/prd.md",
        workspace="/does/not/exist",
        repo_root=str(_REPO_ROOT),
        machine="aarch64",
        home="/does/not/exist",
        which=lambda b: None,                       # nothing on PATH
        run=lambda *a, **k: FakeProc(1, stderr="boom"),
        probe=lambda *a, **k: gate_live_probe.ProbeResult(False, 1, "no gate"),
        tested_with={"ralph_cli": "1.0.0"},
    )
    assert isinstance(findings, list) and findings
    assert all(f.status in ("ok", "warn", "fail") for f in findings)


def test_a_check_that_raises_is_contained_as_a_finding():
    # If a check's own subprocess call explodes, preflight must swallow it into
    # a finding — fail-open means the probe never becomes the blocker.
    def boom(*a, **k):
        raise OSError("simulated docker explosion")

    findings = preflight.preflight(
        run=boom, which=_healthy_which, probe=_fired_probe,
        repo_root=str(_REPO_ROOT), machine="x86_64",
    )
    assert isinstance(findings, list) and findings
    # nothing escaped as an exception; statuses are still well-formed
    assert all(f.status in ("ok", "warn", "fail") for f in findings)


# ---- per-condition findings -------------------------------------------------

def _find(findings, name):
    for f in findings:
        if f.check == name:
            return f
    raise AssertionError("no finding named %r in %r"
                         % (name, [f.check for f in findings]))


def test_docker_absent_is_fail_with_fix_and_fallback(tmp_path):
    kw = _healthy_kwargs(tmp_path)
    kw["which"] = lambda b: None if b == "docker" else "/usr/bin/" + b
    f = _find(preflight.preflight(**kw), "docker_installed")
    assert f.status == "fail"
    assert f.fix and f.fallback_hint


def test_image_absent_but_buildable_is_warn(tmp_path):
    kw = _healthy_kwargs(tmp_path)

    def run(cmd, *a, **k):
        if "inspect" in cmd:
            return FakeProc(1, stderr="No such image")
        return _healthy_run(cmd, *a, **k)

    kw["run"] = run
    f = _find(preflight.preflight(**kw), "image_present")
    # the Dockerfile exists in harness/afk, so absence is recoverable → warn,
    # and the fix must be the concrete build command.
    assert f.status == "warn"
    assert "build" in f.fix.lower()


def test_arm_arch_warns_emulation(tmp_path):
    kw = _healthy_kwargs(tmp_path)
    kw["machine"] = "aarch64"
    f = _find(preflight.preflight(**kw), "arch_amd64")
    assert f.status == "warn"
    assert "emul" in (f.detail + f.fix).lower()


def test_inputs_missing_is_fail(tmp_path):
    kw = _healthy_kwargs(tmp_path)
    kw["plan_path"] = "/no/such/plan.md"
    f = _find(preflight.preflight(**kw), "inputs_exist")
    assert f.status == "fail"
    assert f.fix


def test_gate_live_not_fired_is_bold_fail(tmp_path):
    # The whole safety argument rests on the in-loop gate being live. If the
    # probe does not fire, preflight must fail hard and steer to fallback —
    # never let the caller run a bare bypass.
    kw = _healthy_kwargs(tmp_path)
    kw["probe"] = lambda *a, **k: gate_live_probe.ProbeResult(False, 0, "silent")
    f = _find(preflight.preflight(**kw), "gate_live")
    assert f.status == "fail"
    assert f.fallback_hint


def test_gate_live_fired_is_ok(tmp_path):
    kw = _healthy_kwargs(tmp_path)
    f = _find(preflight.preflight(**kw), "gate_live")
    assert f.status == "ok"


def test_version_aware_is_warn_only_never_fail(tmp_path):
    # A version mismatch is a hint ("suspect #1 if it breaks"), never a
    # blocker. Even a wild mismatch must not produce a fail.
    kw = _healthy_kwargs(tmp_path)
    kw["tested_with"] = {"ralph_cli": "0.0.1-ancient"}
    f = _find(preflight.preflight(**kw), "version_aware")
    assert f.status in ("ok", "warn")
    assert f.status != "fail"


# ---- ralph_cli fix hint package name ---------------------------------------

def test_ralph_cli_fix_hint_names_correct_package(tmp_path):
    # The fix hint must name the actual npm package @daonhan/ralph, not the
    # wrong @ralph/cli alias that does not provide the ralph-afk binary.
    kw = _healthy_kwargs(tmp_path)
    kw["which"] = lambda b: None   # ralph-afk not on PATH → triggers the finding
    f = _find(preflight.preflight(**kw), "ralph_cli")
    assert f.status == "fail"
    assert "@daonhan/ralph" in f.fix, (
        "fix hint must name @daonhan/ralph, got: %r" % f.fix)
    assert "@ralph/cli" not in f.fix, (
        "fix hint must NOT reference the wrong @ralph/cli, got: %r" % f.fix)


# ---- green host + helpers ---------------------------------------------------

def test_all_green_host_has_no_blocker(tmp_path):
    findings = preflight.preflight(**_healthy_kwargs(tmp_path))
    assert not preflight.has_blocker(findings)


def test_has_blocker_is_true_when_any_fail(tmp_path):
    kw = _healthy_kwargs(tmp_path)
    kw["plan_path"] = "/no/such/plan.md"      # forces one fail
    assert preflight.has_blocker(preflight.preflight(**kw))


def test_cli_entrypoint_exits_zero_even_when_red():
    # Run the module as a script against a broken host: it must print and exit
    # 0 — a readiness probe never exit-blocks the workflow.
    proc = subprocess.run(
        [sys.executable, str(_AFK / "preflight.py"),
         "--plan", "/no/such/plan.md", "--prd", "/no/such/prd.md",
         "--image", "definitely-not-an-image:xyz"],
        capture_output=True, text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip()


def test_docker_info_runs_once_across_daemon_and_perm(tmp_path):
    # The daemon and perm checks share one `docker info` probe; preflight() must
    # invoke it exactly once rather than paying the ~20s call twice.
    kw = _healthy_kwargs(tmp_path)
    base_run = kw["run"]
    calls = {"info": 0}

    def counting_run(cmd, *a, **k):
        if "info" in cmd:
            calls["info"] += 1
        return base_run(cmd, *a, **k)

    kw["run"] = counting_run
    findings = preflight.preflight(**kw)
    assert calls["info"] == 1
    # and the shared probe still yields the same per-check verdicts
    assert _find(findings, "docker_daemon").status == "ok"
    assert _find(findings, "docker_perm").status == "ok"
