"""test_bench_suite.py — pure-logic tests for the controlled wall-clock bench harness.

Every case here is pure computation or a fixture-fed parser/seam — never a real
pytest run (that 100s+ probe belongs to cook, not to this suite). Seams (glob
pattern, meminfo/diskstats text, the subprocess runner) are plain function
parameters, not global monkeypatching, per the project's seam convention.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import bench_suite  # noqa: E402


# --- median ---------------------------------------------------------------

def test_median_odd():
    assert bench_suite.median([30, 10, 20]) == 20


def test_median_even():
    assert bench_suite.median([10, 20, 30, 40]) == 25


def test_median_single():
    assert bench_suite.median([15]) == 15


def test_median_empty_raises():
    with pytest.raises(ValueError):
        bench_suite.median([])


# --- parse_pytest_wall ------------------------------------------------------

def test_parse_pytest_wall_passed_only():
    assert bench_suite.parse_pytest_wall("=== 12 passed in 3.45s ===") == (3.45, 12, 0)


def test_parse_pytest_wall_with_failed():
    # pytest prints failed BEFORE passed in the summary line.
    assert bench_suite.parse_pytest_wall("=== 1 failed, 11 passed in 3.4s ===") == (3.4, 11, 1)


def test_parse_pytest_wall_no_match_returns_none():
    assert bench_suite.parse_pytest_wall("collected 0 items") is None


def test_parse_pytest_wall_long_run_with_hms_suffix():
    # pytest appends " (H:MM:SS)" once a run crosses 60s -- the regex must not
    # require "s" to sit immediately before the trailing "=".
    assert bench_suite.parse_pytest_wall(
        "=== 7056 passed in 127.34s (0:02:07) ==="
    ) == (127.34, 7056, 0)


# --- read_thermal ------------------------------------------------------------

def test_read_thermal_none_when_no_sysfs(tmp_path):
    empty_glob = str(tmp_path / "thermal_zone*" / "temp")
    assert bench_suite.read_thermal(glob_pattern=empty_glob) is None


# --- read_snapshot -----------------------------------------------------------

_MEMINFO_FIXTURE = (
    "MemTotal:       16337408 kB\n"
    "MemFree:         1234567 kB\n"
    "MemAvailable:    8000000 kB\n"
)

_DISKSTATS_FIXTURE = (
    "   8       0 sda 100 5 2000 50 80 3 1600 40 0 30 90\n"
    "   8       1 sda1 10 0 200 5 8 0 160 4 0 3 9\n"
)


def test_read_snapshot_parses_fixture(tmp_path):
    missing_glob = str(tmp_path / "nope*" / "temp")
    missing_path = str(tmp_path / "nope")
    snap = bench_suite.read_snapshot(
        thermal_glob=missing_glob,
        cpufreq_glob=missing_glob,
        cpuinfo_path=missing_path,
        meminfo_text=_MEMINFO_FIXTURE,
        diskstats_text=_DISKSTATS_FIXTURE,
    )
    assert snap["temp_c"] is None
    assert snap["cpu_freq_khz"] is None
    assert snap["mem_free_kb"] == 1234567
    assert snap["disk_read_sectors"] == 2000 + 200
    assert snap["disk_write_sectors"] == 1600 + 160


# --- should_cooldown ----------------------------------------------------------

def test_should_cooldown_above_threshold():
    assert bench_suite.should_cooldown(70, 60) is True


def test_should_cooldown_below_threshold():
    assert bench_suite.should_cooldown(50, 60) is False


def test_should_cooldown_none_temp():
    assert bench_suite.should_cooldown(None, 60) is False


# --- noise_band ----------------------------------------------------------------

def test_noise_band_spread():
    assert bench_suite.noise_band([10, 20, 30]) == 20


def test_noise_band_single_rep():
    assert bench_suite.noise_band([15]) == 0


# --- run_bench -------------------------------------------------------------------

def test_run_bench_drops_warmup_and_medians():
    walls = iter([99.0, 10.0, 20.0, 30.0])  # warmup, then 3 measured reps

    def fake_runner(cmd):
        return next(walls)

    result = bench_suite.run_bench(
        "fake-cmd",
        reps=3,
        warmup=1,
        runner=fake_runner,
        sleep_fn=lambda s: None,
        thermal_reader=lambda: None,
        snapshot_reader=lambda: {},
    )
    assert result["median_wall"] == 20
    assert "noise_band" in result
    assert result["noise_band"] == 20
    assert len(result["reps"]) == 3
