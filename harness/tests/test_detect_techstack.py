"""test_detect_techstack.py — read-only tech-stack detector for a target repo.

detect_techstack scans a repo root for build/dependency markers and returns a
profile (languages, the test command, the package manager, CI) so harness skills
(hs:setup / hs:test / hs:insights) can adapt to a NON-Python target instead of
assuming pytest. It is the read-only Shape-A self-learning surface: it observes a
repo, it never mutates one.

Contract under test:
  - presence-based + fail-soft: an empty or missing root yields an empty profile,
    never raises (a detector that crashes the host is worse than one that finds
    nothing).
  - per-stack inference: each detected stack carries its own test_cmd + pkg_mgr;
    a polyglot repo lists all of them; `primary` is the first by fixed precedence.
  - pkg-manager is read from the lockfile/marker, not guessed.
  - CLI emits the profile as JSON on stdout, exit 0.
"""
import json
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import detect_techstack as dt  # noqa: E402

HOOK_PATH = _SCRIPTS / "detect_techstack.py"


def _langs(profile):
    return [s["language"] for s in profile["stacks"]]


def _stack(profile, language):
    return next(s for s in profile["stacks"] if s["language"] == language)


def test_python_pytest_pip(tmp_path):
    (tmp_path / "requirements.txt").write_text("pytest\n", encoding="utf-8")
    p = dt.detect(tmp_path)
    assert "python" in _langs(p)
    s = _stack(p, "python")
    assert s["test_cmd"] == "pytest"
    assert s["pkg_mgr"] == "pip"
    assert p["primary"] == "python"


def test_python_poetry(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname = \"x\"\n", encoding="utf-8")
    s = _stack(dt.detect(tmp_path), "python")
    assert s["pkg_mgr"] == "poetry"


def test_python_pipfile_only(tmp_path):
    # a pipenv project (only Pipfile, no requirements/pyproject) must still be
    # detected as Python — the pkg_mgr branch already reads Pipfile, so omitting
    # it from the marker list was an internal inconsistency.
    (tmp_path / "Pipfile").write_text("[packages]\n", encoding="utf-8")
    s = _stack(dt.detect(tmp_path), "python")
    assert s["pkg_mgr"] == "pipenv"
    assert s["test_cmd"] == "pytest"


def test_python_setup_cfg_only(tmp_path):
    # setuptools projects can carry only setup.cfg (no setup.py) — still Python.
    (tmp_path / "setup.cfg").write_text("[metadata]\nname = x\n", encoding="utf-8")
    assert "python" in _langs(dt.detect(tmp_path))


def test_node_pnpm_reads_test_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8")
    (tmp_path / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n", encoding="utf-8")
    s = _stack(dt.detect(tmp_path), "javascript")
    assert s["pkg_mgr"] == "pnpm"
    assert s["test_cmd"] == "pnpm test"  # there IS a test script → runner invocation


def test_node_no_test_script(tmp_path):
    (tmp_path / "package.json").write_text(
        json.dumps({"name": "x"}), encoding="utf-8")
    s = _stack(dt.detect(tmp_path), "javascript")
    assert s["pkg_mgr"] == "npm"          # no lockfile → npm default
    assert s["test_cmd"] is None          # honest: no test script declared


def test_typescript_when_tsconfig(tmp_path):
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "tsconfig.json").write_text("{}", encoding="utf-8")
    assert "typescript" in _langs(dt.detect(tmp_path))


def test_go(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    s = _stack(dt.detect(tmp_path), "go")
    assert s["test_cmd"] == "go test ./..."
    assert s["pkg_mgr"] == "go"


def test_rust(tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname=\"x\"\n", encoding="utf-8")
    s = _stack(dt.detect(tmp_path), "rust")
    assert s["test_cmd"] == "cargo test"


def test_java_maven(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>\n", encoding="utf-8")
    s = _stack(dt.detect(tmp_path), "java")
    assert s["test_cmd"] == "mvn test"
    assert s["pkg_mgr"] == "maven"


def test_polyglot_lists_all(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool.x]\n", encoding="utf-8")
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    langs = _langs(dt.detect(tmp_path))
    assert "python" in langs and "go" in langs


def test_ci_github_actions(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push\n", encoding="utf-8")
    assert "github-actions" in dt.detect(tmp_path)["ci"]


def test_ci_gitlab(tmp_path):
    (tmp_path / ".gitlab-ci.yml").write_text("stages: [test]\n", encoding="utf-8")
    assert "gitlab-ci" in dt.detect(tmp_path)["ci"]


def test_empty_repo_failsoft(tmp_path):
    p = dt.detect(tmp_path)
    assert p["stacks"] == [] and p["ci"] == [] and p["primary"] is None


def test_missing_root_failsoft(tmp_path):
    p = dt.detect(tmp_path / "does-not-exist")
    assert p["stacks"] == [] and p["primary"] is None  # never raises


def test_malformed_package_json_failsoft(tmp_path):
    # a broken package.json must still detect the JS stack, just without a test_cmd
    (tmp_path / "package.json").write_text("{ not json", encoding="utf-8")
    s = _stack(dt.detect(tmp_path), "javascript")
    assert s["test_cmd"] is None


def test_cli_emits_json(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n", encoding="utf-8")
    r = subprocess.run([sys.executable, str(HOOK_PATH), "--root", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0
    out = json.loads(r.stdout)
    assert out["primary"] == "go"


def test_poetry_not_detected_from_comment(tmp_path):
    # pkg-mgr is read from a real [tool.poetry] TABLE, not a commented/prose mention
    # (docstring: "read from the lockfile/marker, not guessed"). A migration-note
    # comment must not override a real uv.lock, nor the pip default.
    (tmp_path / "pyproject.toml").write_text(
        "# Migrated away from [tool.poetry] to uv\n[project]\nname = \"x\"\n", encoding="utf-8")
    (tmp_path / "uv.lock").write_text("", encoding="utf-8")
    assert _stack(dt.detect(tmp_path), "python")["pkg_mgr"] == "uv"

    only = tmp_path / "only"
    only.mkdir()
    (only / "pyproject.toml").write_text(
        "# uses [tool.poetry] historically\n[project]\nname = \"x\"\n", encoding="utf-8")
    assert _stack(dt.detect(only), "python")["pkg_mgr"] == "pip"
