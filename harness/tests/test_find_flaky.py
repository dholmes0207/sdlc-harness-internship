"""find_flaky — confirm/quantify a flaky test by re-running it.

The verdict logic is pure (unit-tested); one end-to-end test drives the real script
over a deterministic counter-file flaky test (alternating pass/fail) via subprocess.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "harness/plugins/hs/skills/debug/scripts/find_flaky.py"
sys.path.insert(0, str(_SCRIPT.parent))
import find_flaky as ff  # noqa: E402


def test_classify_stable_pass():
    assert ff.classify(6, 6) == "STABLE_PASS"


def test_classify_stable_fail():
    assert ff.classify(0, 6) == "STABLE_FAIL"


def test_classify_flaky():
    assert ff.classify(3, 6) == "FLAKY"
    assert ff.classify(1, 6) == "FLAKY"
    assert ff.classify(5, 6) == "FLAKY"


def test_classify_none_when_no_runs():
    assert ff.classify(0, 0) == "NONE"


def test_end_to_end_detects_flaky(tmp_path):
    # a test that alternates pass/fail across separate runs via a persistent counter
    (tmp_path / "test_flap.py").write_text(
        "import pathlib\n"
        "_c = pathlib.Path('counter.txt')\n"
        "def test_flap():\n"
        "    n = int(_c.read_text()) if _c.exists() else 0\n"
        "    _c.write_text(str(n + 1))\n"
        "    assert n % 2 == 0\n")
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "test_flap.py::test_flap", "-n", "6"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "FLAKY" in out.stdout


def test_end_to_end_stable_pass_is_clean(tmp_path):
    (tmp_path / "test_ok.py").write_text(
        "def test_ok():\n    assert True\n")
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "test_ok.py::test_ok", "-n", "4"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "STABLE_PASS" in out.stdout


def test_never_run_node_id_is_an_error_not_stable_fail(tmp_path):
    (tmp_path / "test_real.py").write_text("def test_real():\n    assert True\n")
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "test_real.py::test_does_not_exist", "-n", "3"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "STABLE_FAIL" not in out.stdout


def test_bare_dash_n_is_a_usage_error(tmp_path):
    (tmp_path / "test_real.py").write_text("def test_real():\n    assert True\n")
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "test_real.py::test_real", "-n"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "STABLE_FAIL" not in out.stdout
