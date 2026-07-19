"""ci.sh is the single source of CI job names + commands for every CI face.

These tests pin the remote-tier job set by a HARDCODED expectation rather than by
reading ci.sh back: dropping a job from ci.sh must red this test, not silently shrink
remote coverage while a consistency-only parity check stays green. The dispatcher is
driven through a real `bash` subprocess, never imported.
"""
import subprocess
import sys
from pathlib import Path

import pytest

# Dev-repo-only: asserts CI layout (scripts/ci.sh, .github/workflows) present in the
# development checkout but not in an installed bundle — skipped on installed copies.
pytestmark = pytest.mark.dev_repo

_REPO = Path(__file__).resolve().parents[2]
_CI_SH = _REPO / "scripts" / "ci.sh"
_SCHEMA_CHECK = _REPO / "harness" / "scripts" / "ci_schema_check.py"
_CI_LOCAL = _REPO / "scripts" / "ci_local.sh"

# Hardcoded remote-tier set. Deriving this from ci.sh would let a job removed from
# ci.sh keep the test green while remote coverage shrinks — changing the remote set is
# a deliberate edit to BOTH ci.sh and this line.
_EXPECTED_REMOTE = {
    "unit-1of3",
    "unit-2of3",
    "unit-3of3",
    "orchestrator",
    "scoring-contract",
    "release-toolkit",
    "invariants",
    "schema",
    "footprint",
    "e2e",
    "eval-bootstrap",
}


def _run(*args):
    return subprocess.run(
        ["bash", str(_CI_SH), *args],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
    )


def _names(out):
    return {ln.strip() for ln in out.splitlines() if ln.strip()}


def test_list_remote_is_exact_hardcoded_set():
    res = _run("--list-remote")
    assert res.returncode == 0, res.stderr
    assert _names(res.stdout) == _EXPECTED_REMOTE


def test_list_all_is_superset_of_remote():
    alln = _names(_run("--list-all").stdout)
    remote = _names(_run("--list-remote").stdout)
    assert remote <= alln
    # preflight is a local install step, part of all/ but never a remote job.
    assert "preflight" in alln
    assert "preflight" not in remote


def test_footprint_job_runs_clean():
    res = _run("footprint")
    assert res.returncode == 0, res.stderr


def test_unknown_job_errors_to_stderr():
    res = _run("__no_such_job__")
    assert res.returncode != 0
    assert "__no_such_job__" in res.stderr or "unknown" in res.stderr.lower()


def test_schema_check_clean_and_carries_no_stale_ref():
    res = subprocess.run(
        [sys.executable, str(_SCHEMA_CHECK)],
        capture_output=True,
        text=True,
        cwd=str(_REPO),
    )
    assert res.returncode == 0, res.stderr + res.stdout
    # The stale data-file reference that rotted in the old embedded schema lists must
    # not reappear in the sole owner. Assemble the forbidden token from parts so this
    # test's own source carries no literal copy of it (the personal-first absence guard
    # git-greps the tree for exactly that string).
    stale_ref = "work-" + "ownership"
    src = _SCHEMA_CHECK.read_text(encoding="utf-8")
    assert stale_ref not in src


def test_ci_workflow_installs_all_required_preflight_deps():
    # The CI workflow runs preflight_deps.py, which exit-1s on ANY missing REQUIRED
    # dep — so the workflow's own `pip install` must cover every REQUIRED pip name or
    # every job reds at preflight before a single test runs. (Coverage is deliberately
    # NOT required on the runner — see test_github_drops_coverage_keeps_xdist — so it
    # lives in preflight OPTIONAL, and this guard covers the REQUIRED set only.)
    sys.path.insert(0, str(_REPO / "harness" / "scripts"))
    import preflight_deps
    ci_yml = (_REPO / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    installed = " ".join(ln for ln in ci_yml.splitlines() if "pip install" in ln)
    missing = [pip for pip in preflight_deps.REQUIRED.values() if pip not in installed]
    assert not missing, "ci.yml pip install missing REQUIRED preflight deps: %s" % missing


def test_ci_local_dispatches_to_ci_sh():
    # ci_local.sh routes the jobs that ci.sh owns through the dispatcher instead of
    # embedding the commands a second time — one source for all faces.
    src = _CI_LOCAL.read_text(encoding="utf-8")
    assert src.count("scripts/ci.sh") >= 6


def test_ci_local_keeps_local_only_steps():
    # The diff-scoped / local-only steps stay in ci_local.sh (kept light on purpose);
    # the rewire must not swallow any of them.
    src = _CI_LOCAL.read_text(encoding="utf-8")
    for probe in ("route_bakeoff.py", "check_report_language.py",
                  "run_afk_slice.py", "standards_strict_gate.py"):
        assert probe in src, f"local-only step {probe} was dropped from ci_local.sh"
