"""Nightly randomly-on wiring — separate from test_ci_job_parity.py so a later
phase (pytest-split matrix) can extend either file without contending on the
same lines.

Covers: scripts/ci.sh hot-path disables pytest-randomly reshuffle (local win +
CI defense-in-depth — pytest-randomly is NOT in the CI pip line today, see
scripts/ci.sh comments), while a new nightly workflow installs the plugin and
runs the SAME suites with randomly ON to catch order-dependence.
"""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
_CI_SH = _REPO / "scripts" / "ci.sh"
_NIGHTLY = _REPO / ".github" / "workflows" / "nightly.yml"


def _ci_sh_text():
    return _CI_SH.read_text(encoding="utf-8")


def _nightly_text():
    return _NIGHTLY.read_text(encoding="utf-8")


def test_nightly_workflow_exists_and_installs_randomly():
    assert _NIGHTLY.is_file(), "expected .github/workflows/nightly.yml to exist"
    text = _nightly_text()
    assert "schedule:" in text
    assert "cron" in text
    install_lines = [ln for ln in text.splitlines() if "pip install" in ln]
    assert install_lines, "expected at least one pip install line in nightly.yml"
    assert any("pytest-randomly" in ln for ln in install_lines)


def test_nightly_installs_pytest_split():
    # test_shard_union_equals_full_collection (test_ci_job_parity.py) importorskips
    # pytest_split -- without it installed here, the nightly job silently skips the
    # one test that proves the 3-way shard partition unions back to the full
    # collection (the importorskip-silently-skips-at-the-gate false-verify trap).
    text = _nightly_text()
    install_lines = [ln for ln in text.splitlines() if "pip install" in ln]
    assert install_lines, "expected at least one pip install line in nightly.yml"
    assert any("pytest-split" in ln for ln in install_lines), (
        "nightly.yml must install pytest-split or test_shard_union_equals_full_collection "
        "silently skips on every nightly run"
    )


def test_nightly_dispatches_ci_sh():
    text = _nightly_text()
    assert "bash scripts/ci.sh" in text
    assert "-nightly" in text, "expected a *-nightly job name dispatched"


def _case_body(case_name):
    """Return the non-comment lines of a `case_name)` ... `;;` block in ci.sh."""
    lines = _ci_sh_text().splitlines()
    body = []
    in_case = False
    for ln in lines:
        stripped = ln.strip()
        if stripped == f"{case_name})":
            in_case = True
            continue
        if in_case:
            if stripped == ";;":
                break
            if not stripped.startswith("#"):
                body.append(ln)
    return "\n".join(body)


def test_ci_sh_hot_path_disables_randomly():
    for hot_case in ("unit", "orchestrator", "release-toolkit"):
        body = _case_body(hot_case)
        assert body, f"case {hot_case}) not found in ci.sh"
        assert "no:randomly" in body or "HOT_NORANDOMLY" in body, (
            f"case {hot_case}) must disable pytest-randomly reshuffle"
        )

    for nightly_case in ("unit-nightly", "orch-nightly", "release-nightly"):
        body = _case_body(nightly_case)
        assert body, f"case {nightly_case}) not found in ci.sh"
        assert "no:randomly" not in body and "HOT_NORANDOMLY" not in body, (
            f"case {nightly_case}) must NOT disable pytest-randomly reshuffle"
        )


def test_ci_sh_tmpfs_comment_not_false_precision():
    text = _ci_sh_text()
    assert "~1.4x" not in text, "tmpfs comment still claims false-precision ~1.4x"


def test_nightly_deselects_fs_guard():
    # nightly.yml dispatches into ci.sh (single-source job commands) rather than
    # inlining pytest flags, so the deselect can live in either file — check the
    # effective wiring: nightly.yml's unit-nightly job PLUS ci.sh's unit-nightly
    # case body.
    combined = _nightly_text() + "\n" + _case_body("unit-nightly")
    assert "test_fs_guard.py" in combined
    assert "--ignore" in combined or "--deselect" in combined


def test_nightly_runs_full_equivalence_sweep():
    # The bash_safety_guard core/dispatcher equivalence sweep is skipped on the
    # hot path (HARNESS_NIGHTLY gate, see test_bash_safety_guard.py) and must
    # not be silently orphaned there — the unit-nightly case body is the one
    # place that has to turn it back on.
    body = _case_body("unit-nightly")
    assert body, "case unit-nightly) not found in ci.sh"
    assert "HARNESS_NIGHTLY=1" in body, (
        "case unit-nightly) must set HARNESS_NIGHTLY=1 or the exhaustive "
        "bash_safety_guard parity sweep never runs anywhere"
    )


def test_preflight_lists_pytest_randomly_optional():
    import sys

    sys.path.insert(0, str(_REPO / "harness" / "scripts"))
    import preflight_deps

    assert "pytest_randomly" in preflight_deps.OPTIONAL
    assert preflight_deps.OPTIONAL["pytest_randomly"] == "pytest-randomly"


def test_every_shard_case_disables_randomly():
    # Each unit-*of3 shard case runs in its own process; without
    # -p no:randomly (or the HOT_NORANDOMLY var it resolves to), a shard
    # reseeds pytest-randomly independently and --splits divides a
    # differently-ordered collection, so a test can land in two shards or
    # zero shards while every shard still reports green.
    for i in range(1, 4):
        case_name = f"unit-{i}of3"
        body = _case_body(case_name)
        assert body, f"case {case_name}) not found in ci.sh"
        assert "no:randomly" in body or "HOT_NORANDOMLY" in body, (
            f"case {case_name}) must disable pytest-randomly reshuffle"
        )
