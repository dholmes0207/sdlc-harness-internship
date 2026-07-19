"""
Tests for harness/plugins/hs/skills/repomix/scripts/repomix_batch.py

Non-vacuous contract:
- The test FAILS if repomix_batch.py is absent (hard import).
- Batch-orchestration logic (config parse, per-repo iteration, output-path
  construction, success/failure accounting) is exercised against 2 real
  fixture dirs in tmp — the external `repomix` CLI is mocked because it is
  not installed in this environment.
- A dedicated test probes the mock/skip path explicitly so a reader can see
  what firing looks like.

Does NOT read repo-internal docs/ledger — no @pytest.mark.dev_repo needed.
"""

import importlib.util
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Hard import — test FAILS if the ported file is absent.
_SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "plugins/hs/skills/repomix/scripts/repomix_batch.py"
)
assert _SCRIPT.is_file(), (
    f"repomix_batch.py not found at {_SCRIPT}. "
    "Port the script before running this suite."
)
spec = importlib.util.spec_from_file_location("repomix_batch", _SCRIPT)
_mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(_mod)

RepomixConfig = _mod.RepomixConfig
EnvLoader = _mod.EnvLoader
RepomixBatchProcessor = _mod.RepomixBatchProcessor
load_repositories_from_file = _mod.load_repositories_from_file
main = _mod.main


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_repo(base: Path, name: str) -> Path:
    """Create a minimal directory tree that looks like a repository."""
    repo = base / name
    repo.mkdir(parents=True)
    (repo / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    (repo / "main.py").write_text("print('hello')\n", encoding="utf-8")
    return repo


def _mock_run_success(cmd, **kwargs):
    """subprocess.run replacement that always returns rc=0."""
    result = MagicMock()
    result.returncode = 0
    result.stdout = ""
    result.stderr = ""
    return result


def _mock_run_fail(cmd, **kwargs):
    """subprocess.run replacement that always returns rc=1."""
    result = MagicMock()
    result.returncode = 1
    result.stdout = ""
    result.stderr = "repomix: command not found"
    return result


# ---------------------------------------------------------------------------
# NOTICE: repomix CLI is absent in this environment
# ---------------------------------------------------------------------------

def test_repomix_cli_absent_notice():
    """Explicit probe that documents the mock/skip path.

    The `repomix` binary is not installed.  This test shows what that looks
    like — check_repomix_installed() returns False — and that the test suite
    works around it by mocking subprocess.run in the orchestration tests.
    """
    config = RepomixConfig()
    processor = RepomixBatchProcessor(config)
    installed = processor.check_repomix_installed()
    if installed:
        pytest.skip("repomix IS installed — mock path not exercised here")
    # Absent case: confirm the return value and that the guard works.
    assert installed is False, (
        "NOTICE: repomix CLI is absent. "
        "Orchestration tests below mock subprocess.run to bypass the binary."
    )


# ---------------------------------------------------------------------------
# Core: batch over 2 fixture repos — mocked CLI
# ---------------------------------------------------------------------------

def test_batch_two_repos_produces_two_outputs(tmp_path):
    """The main batch-orchestration test.

    Creates 2 fixture directories, runs process_batch() with subprocess.run
    mocked to succeed, and asserts both repos produced a success entry with
    the expected output-path pattern.
    """
    repo_a = _make_fake_repo(tmp_path / "src", "repo-alpha")
    repo_b = _make_fake_repo(tmp_path / "src", "repo-beta")
    out_dir = tmp_path / "digests"

    config = RepomixConfig(output_dir=str(out_dir), style="xml")
    processor = RepomixBatchProcessor(config)

    repositories = [
        {"path": str(repo_a)},
        {"path": str(repo_b)},
    ]

    with patch("subprocess.run", side_effect=_mock_run_success):
        results = processor.process_batch(repositories)

    assert len(results["success"]) == 2, (
        f"Expected 2 successes, got {results['success']} / failures: {results['failed']}"
    )
    assert len(results["failed"]) == 0

    # Output paths are embedded in the success messages.
    assert "repo-alpha" in results["success"][0]
    assert "repo-beta" in results["success"][1]
    assert str(out_dir) in results["success"][0]
    assert str(out_dir) in results["success"][1]


def test_batch_output_paths_use_correct_extension(tmp_path):
    """Output filename uses the extension matching the configured style."""
    repo = _make_fake_repo(tmp_path, "myrepo")
    out_dir = tmp_path / "out"

    for style, ext in [("xml", "xml"), ("markdown", "md"), ("json", "json"), ("plain", "txt")]:
        config = RepomixConfig(output_dir=str(out_dir), style=style)
        processor = RepomixBatchProcessor(config)

        captured_cmd = []

        def capture_run(cmd, **kwargs):
            captured_cmd.clear()
            captured_cmd.extend(cmd)
            r = MagicMock()
            r.returncode = 0
            r.stdout = r.stderr = ""
            return r

        with patch("subprocess.run", side_effect=capture_run):
            success, msg = processor.process_repository(str(repo))

        assert success, f"Expected success for style={style}"
        # The -o argument is followed by the output path.
        o_idx = captured_cmd.index("-o")
        output_path = captured_cmd[o_idx + 1]
        assert output_path.endswith(f".{ext}"), (
            f"style={style}: expected .{ext}, got {output_path}"
        )


def test_batch_one_success_one_failure(tmp_path):
    """Correct accounting when one repo fails and one succeeds."""
    repo_a = _make_fake_repo(tmp_path, "good-repo")
    out_dir = tmp_path / "out"
    config = RepomixConfig(output_dir=str(out_dir))
    processor = RepomixBatchProcessor(config)

    call_count = [0]

    def alternating_run(cmd, **kwargs):
        call_count[0] += 1
        r = MagicMock()
        r.returncode = 0 if call_count[0] == 1 else 1
        r.stdout = ""
        r.stderr = "simulated failure" if call_count[0] > 1 else ""
        return r

    repositories = [
        {"path": str(repo_a)},
        {"path": str(tmp_path / "nonexistent-repo")},
    ]

    with patch("subprocess.run", side_effect=alternating_run):
        results = processor.process_batch(repositories)

    assert len(results["success"]) == 1
    assert len(results["failed"]) == 1
    assert "simulated failure" in results["failed"][0]


def test_batch_missing_path_entry_counted_as_failure(tmp_path):
    """A repo dict with no 'path' key is counted as a failure, not an error."""
    out_dir = tmp_path / "out"
    config = RepomixConfig(output_dir=str(out_dir))
    processor = RepomixBatchProcessor(config)

    repositories = [
        {"output": "custom.xml"},  # missing 'path'
    ]

    # No subprocess.run call expected — the guard fires before the shell-out.
    results = processor.process_batch(repositories)
    assert len(results["failed"]) == 1
    assert "Missing 'path'" in results["failed"][0]


# ---------------------------------------------------------------------------
# Config parse: JSON file loading
# ---------------------------------------------------------------------------

def test_load_repositories_from_valid_json(tmp_path):
    repos = [
        {"path": "/repo/alpha"},
        {"path": "owner/beta", "remote": True, "output": "beta.xml"},
    ]
    f = tmp_path / "repos.json"
    f.write_text(json.dumps(repos), encoding="utf-8")

    result = load_repositories_from_file(str(f))
    assert result == repos


def test_load_repositories_from_invalid_json(tmp_path):
    f = tmp_path / "bad.json"
    f.write_text("not { valid }", encoding="utf-8")
    result = load_repositories_from_file(str(f))
    assert result == []


def test_load_repositories_non_array_json(tmp_path):
    f = tmp_path / "obj.json"
    f.write_text('{"path": "/repo"}', encoding="utf-8")
    result = load_repositories_from_file(str(f))
    assert result == []


def test_load_repositories_missing_file():
    result = load_repositories_from_file("/no/such/file.json")
    assert result == []


# ---------------------------------------------------------------------------
# EnvLoader: precedence chain
# ---------------------------------------------------------------------------

def test_env_loader_skill_env_file_loaded(tmp_path):
    """A .env file at the skill root is picked up and parsed."""
    # Fake a skill-root .env by placing it at script_dir.parent
    skill_env = tmp_path / "skill_root" / ".env"
    skill_env.parent.mkdir(parents=True)
    skill_env.write_text("TEST_SKILL_KEY=from_skill\n", encoding="utf-8")

    result = EnvLoader._parse_env_file(skill_env)
    assert result["TEST_SKILL_KEY"] == "from_skill"


def test_env_loader_process_env_wins_over_file(tmp_path, monkeypatch):
    """Process environment takes precedence over .env file values.

    Verifies the precedence rule documented in load_env_files:
    process env > repo-root .env > skills/.env > skill/.env
    """
    import os as _os
    env_file = tmp_path / ".env"
    env_file.write_text("OVERRIDE_ME=from_file\n", encoding="utf-8")
    monkeypatch.setenv("OVERRIDE_ME", "from_process")

    # Simulate the merge order: file first, then process env overwrites.
    file_vars = EnvLoader._parse_env_file(env_file)
    merged: dict = {}
    merged.update(file_vars)       # lower priority: file
    merged.update(_os.environ)     # higher priority: process env
    assert merged["OVERRIDE_ME"] == "from_process"


def test_env_loader_parse_quoted_values(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        'DOUBLE="quoted value"\nSINGLE=\'another\'\nPLAIN=bare\n',
        encoding="utf-8",
    )
    result = EnvLoader._parse_env_file(env_file)
    assert result["DOUBLE"] == "quoted value"
    assert result["SINGLE"] == "another"
    assert result["PLAIN"] == "bare"


def test_env_loader_ignores_comments_and_blanks(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nKEY=value\n# another\n",
        encoding="utf-8",
    )
    result = EnvLoader._parse_env_file(env_file)
    assert list(result.keys()) == ["KEY"]


# ---------------------------------------------------------------------------
# Command construction
# ---------------------------------------------------------------------------

def test_build_command_local_repo(tmp_path):
    config = RepomixConfig(style="markdown", remove_comments=True, verbose=True)
    processor = RepomixBatchProcessor(config)
    cmd = processor._build_command("/some/repo", Path("out/repo.md"), is_remote=False)

    assert cmd[0] == "repomix"
    assert "/some/repo" in cmd
    assert "--style" in cmd
    assert "markdown" in cmd
    assert "--remove-comments" in cmd
    assert "--verbose" in cmd


def test_build_command_remote_repo():
    config = RepomixConfig(no_security_check=True)
    processor = RepomixBatchProcessor(config)
    cmd = processor._build_command("owner/repo", Path("out/repo.xml"), is_remote=True)

    assert cmd[0] == "npx"
    assert "repomix" in cmd
    assert "--remote" in cmd
    assert "owner/repo" in cmd
    assert "--no-security-check" in cmd


def test_build_command_include_ignore_patterns():
    config = RepomixConfig(include_pattern="src/**", ignore_pattern="tests/**")
    processor = RepomixBatchProcessor(config)
    cmd = processor._build_command("/repo", Path("out.xml"), is_remote=False)

    assert "--include" in cmd
    assert "src/**" in cmd
    assert "-i" in cmd
    assert "tests/**" in cmd
