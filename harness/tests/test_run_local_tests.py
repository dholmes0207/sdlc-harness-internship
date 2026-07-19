"""test_run_local_tests.py — pure argv-builder tests for the local test wrapper.

`build_argv` is a pure function (no subprocess) so every case here is a plain argv
comparison against a fixture strategy dict -- never a real pytest run. The one test
that touches the filesystem (`test_strategy_matches_ci_sh`) reads BOTH the real
`test-strategy.yaml` and the real `scripts/ci.sh` and cross-checks them; it is the
drift guard the wrapper exists to enforce (a local command must never diverge from
what CI actually runs), so it stays real rather than fixture-fed.
"""
import re
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "harness" / "scripts"
_CI_SH = _REPO / "scripts" / "ci.sh"
_STRATEGY_YAML = _REPO / "harness" / "data" / "test-strategy.yaml"

if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import run_local_tests  # noqa: E402


# --- fixture strategy (mirrors the shape of test-strategy.yaml, not the real file) ---

_FIXTURE_STRATEGY = {
    "harness": {"path": "harness/tests", "scheduler": "loadfile", "norandomly": True},
    "harness-fast": {
        "path": "harness/tests",
        "scheduler": "loadfile",
        "norandomly": True,
        "ignore": ["test_install.py", "test_harness_lifecycle.py"],
    },
    "orchestrator": {
        "path": "orchestrator/tests",
        "scheduler": "loadfile",
        "marker": "not real_sdk and not real_host",
    },
    "release": {"path": "release/tests", "scheduler": "load"},
    "scoring": {"path": "orchestrator/tests", "marker": "scoring_contract", "xdist": False},
}


# --- build_argv ---------------------------------------------------------------------

def test_build_argv_harness_has_loadfile_and_norandomly():
    argv = run_local_tests.build_argv("harness", _FIXTURE_STRATEGY)
    assert "harness/tests" in argv
    assert argv[argv.index("--dist") + 1] == "loadfile"
    assert "-p" in argv and argv[argv.index("-p") + 1] == "no:randomly"


def test_build_argv_release_uses_dist_load():
    argv = run_local_tests.build_argv("release", _FIXTURE_STRATEGY)
    assert argv[argv.index("--dist") + 1] == "load"


def _last_flag_value(argv, flag):
    # "-m" appears twice in every argv ("python3 -m pytest" module flag, then the
    # marker flag when a profile sets one) -- read the LAST occurrence.
    idx = len(argv) - 1 - argv[::-1].index(flag)
    return argv[idx + 1]


def test_build_argv_orchestrator_carries_marker():
    argv = run_local_tests.build_argv("orchestrator", _FIXTURE_STRATEGY)
    assert argv.count("-m") == 2
    assert _last_flag_value(argv, "-m") == "not real_sdk and not real_host"


def test_build_argv_fast_ignores_heavy_files():
    argv = run_local_tests.build_argv("harness-fast", _FIXTURE_STRATEGY)
    assert "--ignore=harness/tests/test_install.py" in argv
    assert "--ignore=harness/tests/test_harness_lifecycle.py" in argv


def test_build_argv_scoring_no_xdist():
    argv = run_local_tests.build_argv("scoring", _FIXTURE_STRATEGY)
    assert "-n" not in argv
    assert "--dist" not in argv
    assert _last_flag_value(argv, "-m") == "scoring_contract"


def test_unknown_profile_raises():
    with pytest.raises(ValueError):
        run_local_tests.build_argv("nonexistent", _FIXTURE_STRATEGY)


# --- load_strategy --------------------------------------------------------------------

def test_load_strategy_reads_real_yaml():
    strategy = run_local_tests.load_strategy(_STRATEGY_YAML)
    for name in ("harness", "harness-fast", "orchestrator", "release", "scoring"):
        assert name in strategy
        assert "path" in strategy[name]


def test_load_strategy_missing_profiles_key_raises(tmp_path):
    bad = tmp_path / "bad.yaml"
    bad.write_text("schema_version: '1.0'\n", encoding="utf-8")
    with pytest.raises(ValueError):
        run_local_tests.load_strategy(bad)


# --- drift guard: local strategy must match what ci.sh actually runs ------------------

@pytest.mark.dev_repo
def test_strategy_matches_ci_sh():
    ci_text = _CI_SH.read_text(encoding="utf-8")
    strategy = run_local_tests.load_strategy(_STRATEGY_YAML)

    # unit) -- PARALLEL default is "-n auto --dist loadfile"; HOT_NORANDOMLY is
    # "-p no:randomly" when pytest-randomly is importable. harness profile mirrors both.
    assert 'PARALLEL="-n auto --dist loadfile"' in ci_text
    assert strategy["harness"]["scheduler"] == "loadfile"
    assert 'HOT_NORANDOMLY="-p no:randomly"' in ci_text
    assert strategy["harness"]["norandomly"] is True

    # orchestrator) -- ORCH_MARKER is the single source for the marker string.
    # It also carries ${HOT_NORANDOMLY} (a hot-path case, see ci.sh cases loop
    # above), so the local profile must set norandomly too or the wrapper
    # diverges from what ci.sh actually runs.
    orch_marker_match = re.search(r'ORCH_MARKER="([^"]+)"', ci_text)
    assert orch_marker_match is not None
    assert strategy["orchestrator"]["marker"] == orch_marker_match.group(1)
    assert strategy["orchestrator"]["scheduler"] == "loadfile"
    orch_block = re.search(r"\n\s*orchestrator\)(.*?);;", ci_text, re.S)
    assert orch_block is not None
    assert "HOT_NORANDOMLY" in orch_block.group(1)
    assert strategy["orchestrator"]["norandomly"] is True

    # release-toolkit) -- explicit --dist load (NOT the loadfile default),
    # and (same as orchestrator) it carries ${HOT_NORANDOMLY}.
    release_block = re.search(r"release-toolkit\)(.*?);;", ci_text, re.S)
    assert release_block is not None
    assert "--dist load" in release_block.group(1)
    assert strategy["release"]["scheduler"] == "load"
    assert "HOT_NORANDOMLY" in release_block.group(1)
    assert strategy["release"]["norandomly"] is True

    # scoring-contract) -- no ${PARALLEL} at all, explicit -m scoring_contract.
    scoring_block = re.search(r"scoring-contract\)(.*?);;", ci_text, re.S)
    assert scoring_block is not None
    assert "${PARALLEL}" not in scoring_block.group(1)
    assert "-m scoring_contract" in scoring_block.group(1)
    assert strategy["scoring"]["marker"] == "scoring_contract"
    assert strategy["scoring"].get("xdist") is False
