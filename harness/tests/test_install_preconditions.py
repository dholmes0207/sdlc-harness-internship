"""test_install_preconditions.py — two install-time guards that turn a silent
wrong-result into a loud, actionable stop.

1. source == target with an UNTRACKED harness/ tree. Unpacking a release bundle
   INSIDE the repo and running `install.py --target .` makes source == target,
   which the installer treats as the in-repo dev no-op and skips the file copy —
   so a real install copies nothing and "succeeds" with zero files. The dev
   dogfood case (harness/ git-tracked) is legitimate and still skips quietly; the
   untracked case is the bundle-inside-repo mistake and must refuse on a real run
   (and warn on a dry run) instead of installing nothing.

2. Missing Python deps on the direct path. The one-shot install.sh runs
   preflight_deps first, but `install.py` run directly skipped it — a missing
   pyyaml/defusedxml then surfaced as an opaque hook ImportError much later.
   main() now checks deps up front and aborts with the exact pip command.
"""
import shutil
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
for _p in (str(_REPO_ROOT / "harness" / "install"),
           str(_REPO_ROOT / "harness" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import install as installer  # noqa: E402
import preflight_deps  # noqa: E402
import _settings  # noqa: E402
from _errors import InstallError  # noqa: E402
from conftest import _git  # noqa: E402


class TestSettingsReadJson:
    """`harness setup` reads the target's .claude/settings.local.json before merging
    the wiring. A hand-edited file that is non-UTF-8 or valid-JSON-but-not-a-mapping
    must become an actionable InstallError, not a raw UnicodeDecodeError/AttributeError
    traceback out of setup."""

    def test_non_utf8_settings_raises_install_error(self, tmp_path):
        p = tmp_path / "settings.local.json"
        p.write_bytes(b'{"env": "\xff\xfe bad bytes"}')
        with pytest.raises(InstallError) as ei:
            _settings._read_json(p)
        assert "re-run" in str(ei.value).lower()

    def test_wrong_shape_settings_raises_install_error(self, tmp_path):
        p = tmp_path / "settings.local.json"
        p.write_text('["not", "a", "mapping"]', encoding="utf-8")
        with pytest.raises(InstallError) as ei:
            _settings._read_json(p)
        assert "mapping" in str(ei.value).lower()

    def test_invalid_json_still_raises_install_error(self, tmp_path):
        # the pre-existing contract must still hold
        p = tmp_path / "settings.local.json"
        p.write_text('{"env": ', encoding="utf-8")
        with pytest.raises(InstallError):
            _settings._read_json(p)

    def test_valid_mapping_returns_dict(self, tmp_path):
        p = tmp_path / "settings.local.json"
        p.write_text('{"env": {"X": "1"}}', encoding="utf-8")
        assert _settings._read_json(p) == {"env": {"X": "1"}}

    def test_nested_wrong_shape_raises_install_error(self, tmp_path):
        # Round-23: a top-level-object settings whose `env`/`hooks` sub-value is the
        # wrong shape (a hand-edit slip) must become an actionable InstallError, not a
        # raw AttributeError/ValueError out of setup OR uninstall (both read via here).
        p = tmp_path / "settings.local.json"
        for bad in ('{"env": [1, 2, 3]}',
                    '{"hooks": ["x"]}',
                    '{"hooks": {"PreToolUse": "oops"}}'):
            p.write_text(bad, encoding="utf-8")
            with pytest.raises(InstallError):
                _settings._read_json(p)

    def test_null_env_and_hooks_are_tolerated(self, tmp_path):
        # Round-24 L1: `null` must be treated as "missing", NOT rejected — every
        # consumer reads these with the `or {}` idiom (dict(env or {}), hooks or {}),
        # so a `"env": null` / `"hooks": null` hand-edit ("disable all") is valid and
        # must not raise; the validator and consumers agree in both directions.
        p = tmp_path / "settings.local.json"
        p.write_text('{"env": null, "hooks": null}', encoding="utf-8")
        out = _settings._read_json(p)  # must NOT raise
        assert out == {"env": None, "hooks": None}

    def test_valid_hooks_and_env_shapes_pass(self, tmp_path):
        p = tmp_path / "settings.local.json"
        p.write_text('{"env": {"X": "1"}, "hooks": {"PreToolUse": '
                     '[{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]}}',
                     encoding="utf-8")
        out = _settings._read_json(p)
        assert out["env"] == {"X": "1"} and "PreToolUse" in out["hooks"]


def _repo_with_untracked_harness(tmp_path):
    """A git repo with the harness tree present but gitignored/untracked — exactly
    what extracting a bundle inside the target repo leaves behind."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    shutil.copytree(_REPO_ROOT / "harness", repo / "harness")
    (repo / ".gitignore").write_text("harness/\n", encoding="utf-8")
    _git(repo, "add", ".gitignore")
    _git(repo, "commit", "-m", "init")
    return repo


class TestHarnessTrackedDetection:
    def test_tracked_harness_is_true(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        _git(repo, "init")
        _git(repo, "config", "user.email", "t@t")
        _git(repo, "config", "user.name", "t")
        (repo / "harness").mkdir()
        (repo / "harness" / "x.txt").write_text("hi", encoding="utf-8")
        _git(repo, "add", "harness/x.txt")
        _git(repo, "commit", "-m", "track")
        assert installer._harness_is_tracked(repo) is True

    def test_untracked_harness_is_false(self, tmp_path):
        repo = _repo_with_untracked_harness(tmp_path)
        assert installer._harness_is_tracked(repo) is False

    def test_non_git_dir_is_false(self, tmp_path):
        (tmp_path / "harness").mkdir()
        assert installer._harness_is_tracked(tmp_path) is False


class TestSourceEqualsTargetUntracked:
    def test_real_install_refuses(self, tmp_path):
        repo = _repo_with_untracked_harness(tmp_path)
        with pytest.raises(installer.InstallError) as ei:
            installer.install(repo, repo)
        msg = str(ei.value)
        assert "source" in msg.lower() and "target" in msg.lower()
        # the message must point at the real fix (extract outside / pass --source)
        assert "--source" in msg or "outside" in msg.lower()

    def test_dry_run_warns_but_does_not_raise(self, tmp_path):
        repo = _repo_with_untracked_harness(tmp_path)
        res = installer.install(repo, repo, dry_run=True)
        assert res["ok"]
        assert any("install" in w.lower() and "nothing" in w.lower()
                   for w in res["warnings"])


class TestDepsGuard:
    def test_main_aborts_when_deps_missing(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(preflight_deps, "missing_deps",
                            lambda: ["pyyaml", "defusedxml"])
        rc = installer.main(["--target", str(tmp_path), "--non-interactive"])
        assert rc != 0
        err = capsys.readouterr().err
        assert "pip install" in err
        assert "defusedxml" in err

    def test_dry_run_is_exempt_from_deps_guard(self, tmp_path, monkeypatch):
        # a dry-run only plans; it must not be blocked by the deps gate.
        monkeypatch.setattr(preflight_deps, "missing_deps",
                            lambda: ["pyyaml"])
        rc = installer.main(["--target", str(tmp_path), "--non-interactive",
                             "--dry-run"])
        assert rc == 0
