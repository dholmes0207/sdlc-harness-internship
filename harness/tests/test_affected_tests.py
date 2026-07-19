"""affected_tests — pick the test files a change can break (import-graph reverse-BFS).

The graph core (import parsing, reverse-dependency BFS) is pure and unit-tested; one
end-to-end test drives the real script over a synthetic tree and asserts only the
transitively-importing test is selected.
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = ROOT / "harness/plugins/hs/skills/test/scripts/affected_tests.py"
sys.path.insert(0, str(_SCRIPT.parent))
import affected_tests as at  # noqa: E402


# ---- pure: import parsing ------------------------------------------------------

def test_parse_imports_plain_and_from():
    src = ("import os\n"
           "import hook_runtime\n"
           "from trace_log import append_event\n"
           "from pkg.sub import thing\n"
           "import a, b\n"
           "from . import sibling\n")  # relative → skipped
    mods = at.parse_imports(src)
    assert "hook_runtime" in mods
    assert "trace_log" in mods
    assert "pkg" in mods           # first dotted segment
    assert {"a", "b"} <= mods
    assert "sibling" not in mods   # relative import not resolvable by stem


def test_parse_imports_dynamic_load_string_names():
    # the harness loads hooks dynamically: a static `import X` parser would miss the
    # edge, so the string module name in these loaders is also captured.
    src = ('spec = importlib.util.spec_from_file_location("agent_rbac_guard", HOOK_PATH)\n'
           'm = importlib.import_module("trace_log")\n'
           '__import__("hook_runtime")\n')
    mods = at.parse_imports(src)
    assert {"agent_rbac_guard", "trace_log", "hook_runtime"} <= mods


# ---- pure: reverse-dependency BFS ----------------------------------------------

def test_affected_is_transitive():
    # c imports b, b imports a  →  changing a affects {b, c}
    reverse = {"a.py": {"b.py"}, "b.py": {"c.py"}}
    assert at.affected({"a.py"}, reverse) == {"b.py", "c.py"}


def test_affected_no_dependents_is_empty():
    assert at.affected({"lonely.py"}, {}) == set()


def test_build_reverse_deps_links_importer_to_target():
    files = {
        "src.py": "x = 1\n",
        "test_src.py": "import src\n",
    }
    reverse = at.build_reverse_deps(files.keys(),
                                    lambda p: files[p],
                                    at.build_stem_index(files.keys()))
    assert reverse.get("src.py") == {"test_src.py"}


# ---- end-to-end over a synthetic tree ------------------------------------------

def _run(args, cwd, timeout=60):
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          cwd=cwd, capture_output=True, text=True, timeout=timeout)


def test_end_to_end_selects_only_affected_test(tmp_path):
    (tmp_path / "core.py").write_text("VALUE = 1\n")
    (tmp_path / "mid.py").write_text("import core\n")
    (tmp_path / "test_mid.py").write_text("import mid\ndef test_x():\n    assert mid\n")
    (tmp_path / "test_other.py").write_text("def test_y():\n    assert True\n")
    out = _run(["--root", str(tmp_path), "--changed", "core.py"], tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    selected = set(out.stdout.split())
    assert "test_mid.py" in selected     # imports mid → imports core
    assert "test_other.py" not in selected


def test_changed_test_file_selects_itself(tmp_path):
    (tmp_path / "test_self.py").write_text("def test_z():\n    assert True\n")
    out = _run(["--root", str(tmp_path), "--changed", "test_self.py"], tmp_path)
    assert "test_self.py" in out.stdout


def test_no_changed_is_usage_error(tmp_path):
    out = _run(["--root", str(tmp_path)], tmp_path)
    assert out.returncode == 2, out.stdout + out.stderr


def test_base_that_git_cannot_diff_is_a_loud_error(tmp_path):
    # --base in a non-git dir must NOT silently print "no tests" (read as 'safe') — error.
    out = _run(["--root", str(tmp_path), "--base", "HEAD"], tmp_path)
    assert out.returncode == 2, out.stdout + out.stderr
    assert "git" in (out.stdout + out.stderr).lower()


def test_zero_affected_prints_superset_caveat(tmp_path):
    # a change nothing depends on → empty stdout, but a stderr caveat so the empty
    # result is not misread as "nothing to run, safe".
    (tmp_path / "lonely.py").write_text("X = 1\n")
    out = _run(["--root", str(tmp_path), "--changed", "lonely.py"], tmp_path)
    assert out.returncode == 0, out.stdout + out.stderr
    assert out.stdout.strip() == ""
    assert "superset" in out.stderr.lower() or "full suite" in out.stderr.lower()
