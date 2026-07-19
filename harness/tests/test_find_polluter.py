"""find_polluter — bisect to the earlier test that pollutes a target test.

The bisection core is pure (takes a `run_passes` callable) so it is unit-tested
without nested pytest; one end-to-end test drives the real script over a synthetic
polluter pair via a subprocess.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "harness/plugins/hs/skills/debug/scripts/find_polluter.py"
sys.path.insert(0, str(_SCRIPT.parent))
import find_polluter as fp  # noqa: E402


def _passes_unless(polluter):
    """A fake run_passes: the target PASSES iff `polluter` is not in the prefix."""
    return lambda prefix: polluter not in prefix


def test_bisect_finds_middle_polluter():
    before = ["a", "b", "c", "d", "e"]
    assert fp.bisect_polluter(before, _passes_unless("c")) == "c"


def test_bisect_finds_first_polluter():
    before = ["a", "b", "c", "d"]
    assert fp.bisect_polluter(before, _passes_unless("a")) == "a"


def test_bisect_finds_last_polluter():
    before = ["a", "b", "c", "d"]
    assert fp.bisect_polluter(before, _passes_unless("d")) == "d"


def test_bisect_none_when_target_fails_alone():
    # run_passes([]) is False -> not a pollution case (target broken on its own)
    assert fp.bisect_polluter(["a", "b"], lambda prefix: False) is None


def test_bisect_none_when_no_pollution():
    # the full prefix still passes -> nothing pollutes it
    assert fp.bisect_polluter(["a", "b"], lambda prefix: True) is None


def test_bisect_empty_before():
    assert fp.bisect_polluter([], lambda prefix: True) is None


def test_end_to_end_finds_the_polluter(tmp_path):
    # a victim that passes alone but fails once the polluter has mutated module state
    (tmp_path / "test_pollute.py").write_text(
        "_state = {'dirty': False}\n"
        "def test_aaa_innocent():\n    assert True\n"
        "def test_bbb_pollute():\n    _state['dirty'] = True\n    assert True\n"
        "def test_ccc_victim():\n    assert not _state['dirty']\n")
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "test_pollute.py::test_ccc_victim"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert out.returncode == 1, out.stdout + out.stderr
    assert "test_bbb_pollute" in out.stdout
    assert "POLLUTER" in out.stdout


def test_oracle_exact_pass():
    assert fp._passed_from_output("PASSED a.py::test_foo\n", "a.py::test_foo") is True


def test_oracle_superstring_sibling_not_matched():
    # F2: a failing superstring sibling (test_foobar) must not flip test_foo to failing
    out = "FAILED a.py::test_foobar - boom\nPASSED a.py::test_foo\n"
    assert fp._passed_from_output(out, "a.py::test_foo") is True


def test_oracle_collection_error_is_not_a_pass():
    # F1: the target never ran (prefix broke collection) -> NOT a pass
    out = "ERROR a.py::test_other - ImportError\n!!! collection failure !!!\n"
    assert fp._passed_from_output(out, "a.py::test_foo") is False


def test_oracle_failed_target():
    assert fp._passed_from_output("FAILED a.py::test_foo - assert\n", "a.py::test_foo") is False


def test_oracle_param_id_with_spaces():
    # a parametrized node-id can contain spaces (`test_foo[a b]`); the status word must be
    # split off without truncating the id, else the result is misread as a false failure.
    tgt = "a.py::test_foo[a b]"
    assert fp._passed_from_output("PASSED a.py::test_foo[a b]\n", tgt) is True
    assert fp._passed_from_output("FAILED a.py::test_foo[a b] - boom\n", tgt) is False
    # a superstring sibling with spaces must still not match
    assert fp._passed_from_output("PASSED a.py::test_foobar[a b]\n", tgt) is False


def test_oracle_param_id_with_space_hyphen_space():
    # a parametrized id can contain ` - ` (e.g. `test_foo[user - admin]`); the reason-strip
    # must not truncate the id — the match anchors on the known target instead.
    tgt = "a.py::test_foo[user - admin]"
    assert fp._passed_from_output("PASSED a.py::test_foo[user - admin]\n", tgt) is True
    assert fp._passed_from_output("FAILED a.py::test_foo[user - admin] - boom\n", tgt) is False


def test_target_failing_in_isolation_is_reported_distinctly(tmp_path):
    # a target that FAILS on its own is not a pollution case: it must NOT print the
    # misleading "passes ... no reproducible pollution" line, but exit 2 with a clear reason.
    (tmp_path / "test_iso.py").write_text(
        "def test_aaa_ok():\n    assert True\n"
        "def test_bbb_broken():\n    assert False\n")
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "test_iso.py::test_bbb_broken"],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "isolation" in (out.stdout + out.stderr).lower()
    assert "no reproducible pollution" not in out.stdout


def test_target_passes_raises_on_a_prefix_collection_error(tmp_path):
    # a run that cannot even collect (bogus node-id / missing file) exits outside {0,1,5};
    # _target_passes must raise rather than silently read it as a target FAILURE.
    import pytest as _pytest
    (tmp_path / "conftest.py").write_text("")
    with _pytest.raises(fp._PrefixCollectionError):
        old = __import__("os").getcwd()
        __import__("os").chdir(tmp_path)
        try:
            fp._target_passes([], "no_such_file.py::test_x", [])
        finally:
            __import__("os").chdir(old)


def test_collection_error_is_not_silently_nothing_to_bisect(tmp_path):
    # a suite that cannot be collected must exit non-zero, not "nothing to bisect" 0
    (tmp_path / "test_broken.py").write_text("def test_x(:\n    pass\n")  # syntax error
    out = subprocess.run(
        [sys.executable, str(_SCRIPT), "test_broken.py::test_x"],
        cwd=tmp_path, capture_output=True, text=True, timeout=60)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "collect" in (out.stdout + out.stderr).lower()
