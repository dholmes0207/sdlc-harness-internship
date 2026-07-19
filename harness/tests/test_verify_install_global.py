"""test_verify_install_global.py — verify_install under a global (bin≠project) install.

Under a global install the manifest + hook scripts + components + plugins live
BIN-side, while the wired hook-registration in the PROJECT's settings.json points
at $HARNESS_BIN_ROOT. verify resolves the integrity checks against bin_root() and
adds a settings-wiring check that the project's hook commands reference the shared
binary (not a stale project-local harness/hooks path). Self-install (no
HARNESS_BIN_ROOT) is unchanged.
"""
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "harness" / "scripts"))

import verify_install as vi  # noqa: E402


def _settings(project, command):
    d = project / ".claude"; d.mkdir(parents=True, exist_ok=True)
    (d / "settings.json").write_text(json.dumps({
        "hooks": {"PreToolUse": [{"hooks": [
            {"type": "command", "command": command}]}]}}), encoding="utf-8")


class TestSettingsWiring:
    def test_global_wiring_points_at_bin_clean(self, tmp_path):
        proj = tmp_path / "proj"
        _settings(proj, '"$HARNESS_BIN_ROOT"/harness/hooks/session_init.py')
        assert vi.settings_wiring_problems(proj) == []

    def test_global_wiring_project_local_is_flagged(self, tmp_path):
        proj = tmp_path / "proj"
        _settings(proj, '"$CLAUDE_PROJECT_DIR"/harness/hooks/session_init.py')
        probs = vi.settings_wiring_problems(proj)
        assert probs and "HARNESS_BIN_ROOT" in probs[0][1]

    def test_non_harness_command_ignored(self, tmp_path):
        proj = tmp_path / "proj"
        _settings(proj, 'grep harness/hooks/ audit.log')  # not a hook invocation
        assert vi.settings_wiring_problems(proj) == []


class TestTrackedSettings:
    """A git-tracked .claude/settings.json is a landmine: it is install-materialized
    and mode-specific, so committing it freezes stale/wrong-mode hook wiring into
    git history — a later `git checkout` restores dead per-script paths and bricks
    every hook. verify surfaces this (warn-only, mode-agnostic)."""

    def _git(self, repo, *args):
        import subprocess
        subprocess.run(["git", "-C", str(repo), *args],
                       check=True, capture_output=True, text=True)

    def _init(self, tmp_path):
        repo = tmp_path / "proj"; repo.mkdir()
        self._git(repo, "init", "-q")
        self._git(repo, "config", "user.email", "t@t")
        self._git(repo, "config", "user.name", "t")
        return repo

    def test_tracked_settings_flagged(self, tmp_path):
        repo = self._init(tmp_path)
        _settings(repo, '"$HARNESS_BIN_ROOT"/harness/hooks/session_init.py')
        self._git(repo, "add", ".claude/settings.json")
        self._git(repo, "commit", "-qm", "seed")
        probs = vi.tracked_settings_problems(repo)
        assert probs and ".claude/settings.json" in probs[0][0]
        assert "commit" in probs[0][1].lower() or "track" in probs[0][1].lower()

    def test_untracked_settings_clean(self, tmp_path):
        repo = self._init(tmp_path)
        _settings(repo, '"$HARNESS_BIN_ROOT"/harness/hooks/session_init.py')
        # present but never `git add`-ed → not tracked → no problem
        assert vi.tracked_settings_problems(repo) == []

    def test_non_git_dir_clean(self, tmp_path):
        proj = tmp_path / "plain"
        _settings(proj, '"$HARNESS_BIN_ROOT"/harness/hooks/session_init.py')
        # not a git repo at all → graceful, no crash, no problem
        assert vi.tracked_settings_problems(proj) == []


class TestRecipientSkeleton:
    def test_reports_missing_skeleton(self, tmp_path):
        # a project with no .harness/ skeleton reports an actionable problem,
        # not a crash.
        proj = tmp_path / "proj"; proj.mkdir()
        probs = vi.recipient_skeleton_problems(proj)
        assert probs and ".harness" in probs[0][0]
        assert "bootstrap" in probs[0][1].lower()

    def test_clean_after_bootstrap(self, tmp_path):
        proj = tmp_path / "proj"; proj.mkdir()
        sys.path.insert(0, str(_REPO_ROOT / "harness" / "install"))
        import bootstrap
        bootstrap.ensure_skeleton(proj / ".harness")
        assert vi.recipient_skeleton_problems(proj) == []


class TestGlobalModeMain:
    def test_global_mode_checks_bin_root(self, tmp_path, monkeypatch):
        # HARNESS_BIN_ROOT points at the real repo (a valid manifest); --root is an
        # empty project with no harness tree. verify resolves integrity against the
        # bin and reports clean.
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(_REPO_ROOT))
        proj = tmp_path / "proj"; proj.mkdir()
        _settings(proj, '"$HARNESS_BIN_ROOT"/harness/hooks/session_init.py')
        sys.path.insert(0, str(_REPO_ROOT / "harness" / "install"))
        import bootstrap
        bootstrap.ensure_skeleton(proj / ".harness")  # a clean global install is bootstrapped
        rc = vi.main(["--root", str(proj), "--strict"])
        assert rc == 0

    def test_global_mode_flags_project_local_wiring(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(_REPO_ROOT))
        proj = tmp_path / "proj"; proj.mkdir()
        _settings(proj, '"$CLAUDE_PROJECT_DIR"/harness/hooks/session_init.py')
        rc = vi.main(["--root", str(proj), "--strict"])
        assert rc == 1  # project-local wiring under a global bin is drift

    def test_self_install_mode_unchanged(self, tmp_path, monkeypatch):
        # no HARNESS_BIN_ROOT → integrity resolves against --root, as today.
        monkeypatch.delenv("HARNESS_BIN_ROOT", raising=False)
        rc = vi.main(["--root", str(_REPO_ROOT), "--strict"])
        assert rc == 0
