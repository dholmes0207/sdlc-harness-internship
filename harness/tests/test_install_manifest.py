"""test_install_manifest.py — build_manifest + verify_install roundtrip.

Manifest covers ONLY git-tracked files under harness/ (generated state,
RUN-LOG, untracked junk must not poison --strict) and excludes manifest.json
itself (self-hash is unstable by construction). verify --strict fails loud
NAMING each drifted file (R8 install drift).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from conftest import _git  # noqa: E402

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


@pytest.fixture()
def mini_repo(tmp_path):
    """A tiny git repo with harness/ files: 2 tracked, 1 untracked, 1 state."""
    repo = tmp_path / "repo"
    h = repo / "harness"
    (h / "hooks").mkdir(parents=True)
    (h / "state").mkdir(parents=True)
    (h / "hooks" / "a.py").write_text("print('a')\n")
    (h / "README.md").write_text("# mini\n")
    (h / "release.json").write_text('{"harness_version": "0.0.0"}\n')  # version identity
    (h / "state" / "runtime.jsonl").write_text('{"never": "hashed"}\n')  # untracked state
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "harness/hooks/a.py", "harness/README.md", "harness/release.json")
    _git(repo, "commit", "-qm", "seed")
    (h / "untracked.tmp").write_text("junk")  # exists on disk, never committed
    return repo


def _build(repo):
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "build_manifest.py"), "--root", str(repo)],
        capture_output=True, text=True)


def _verify(repo, *flags):
    return subprocess.run(
        [sys.executable, str(_SCRIPTS / "verify_install.py"),
         "--root", str(repo)] + list(flags),
        capture_output=True, text=True)


class TestBuildManifest:
    def test_only_git_tracked_files_hashed(self, mini_repo):
        proc = _build(mini_repo)
        assert proc.returncode == 0, proc.stderr
        manifest = json.loads((mini_repo / "harness" / "manifest.json").read_text())
        paths = set(manifest["files"].keys())
        assert paths == {"harness/hooks/a.py", "harness/README.md"}
        # untracked + state + manifest itself + release.json (version identity,
        # SEPARATE from the integrity map) all excluded
        assert "harness/untracked.tmp" not in paths
        assert "harness/state/runtime.jsonl" not in paths
        assert "harness/manifest.json" not in paths
        assert "harness/release.json" not in paths

    def test_hashes_are_sha256(self, mini_repo):
        _build(mini_repo)
        manifest = json.loads((mini_repo / "harness" / "manifest.json").read_text())
        for digest in manifest["files"].values():
            assert len(digest) == 64
            int(digest, 16)  # valid hex

    def test_non_ascii_tracked_filename_is_covered(self, mini_repo):
        # A tracked file with a non-ASCII name must appear in the manifest.
        # git ls-files C-quotes such names under the default core.quotepath=true
        # ("harness/caf\303\251.md"), which misses on disk and silently drops the
        # file from coverage — drift on it would then be undetectable.
        h = mini_repo / "harness"
        (h / "café.md").write_text("# accented\n", encoding="utf-8")
        _git(mini_repo, "add", "harness/café.md")
        _git(mini_repo, "commit", "-qm", "accented")
        proc = _build(mini_repo)
        assert proc.returncode == 0, proc.stderr
        manifest = json.loads(
            (mini_repo / "harness" / "manifest.json").read_text(encoding="utf-8"))
        assert "harness/café.md" in manifest["files"]

    def test_rebuild_with_no_change_is_byte_identical(self, mini_repo):
        # A manifest is a content-integrity map; rebuilding it without touching
        # any file must reproduce the exact same bytes. A wall-clock field would
        # churn the diff on every rebuild and defeat that, so none is embedded.
        _build(mini_repo)
        first = (mini_repo / "harness" / "manifest.json").read_bytes()
        _build(mini_repo)
        second = (mini_repo / "harness" / "manifest.json").read_bytes()
        assert first == second


class TestVerifyInstall:
    def test_roundtrip_passes(self, mini_repo):
        _build(mini_repo)
        proc = _verify(mini_repo, "--strict")
        assert proc.returncode == 0, proc.stderr

    def test_one_byte_drift_fails_strict_naming_the_file(self, mini_repo):
        _build(mini_repo)
        target = mini_repo / "harness" / "hooks" / "a.py"
        target.write_text(target.read_text() + "#")
        proc = _verify(mini_repo, "--strict")
        assert proc.returncode != 0
        assert "harness/hooks/a.py" in (proc.stderr + proc.stdout)

    def test_missing_file_fails_strict(self, mini_repo):
        _build(mini_repo)
        (mini_repo / "harness" / "README.md").unlink()
        proc = _verify(mini_repo, "--strict")
        assert proc.returncode != 0
        assert "harness/README.md" in (proc.stderr + proc.stdout)

    def test_non_strict_reports_but_exits_zero(self, mini_repo):
        _build(mini_repo)
        target = mini_repo / "harness" / "hooks" / "a.py"
        target.write_text("drifted")
        proc = _verify(mini_repo)
        assert proc.returncode == 0
        assert "harness/hooks/a.py" in (proc.stderr + proc.stdout)


@pytest.fixture()
def reg_repo(tmp_path):
    """A git repo whose harness/ ships an entrypoint hook, a library module, and
    a hooks-registration.yaml. The base layout is registration-clean: the
    entrypoint is wired, the library is intentionally left unwired."""
    repo = tmp_path / "repo"
    h = repo / "harness"
    (h / "hooks").mkdir(parents=True)
    (h / "install").mkdir(parents=True)
    (h / "hooks" / "foo.py").write_text(
        'def main():\n    pass\n\n\nif __name__ == "__main__":\n    main()\n')
    # library module: no __main__ → not an entrypoint, never required to be wired
    (h / "hooks" / "lib.py").write_text("VALUE = 1\n")
    (h / "install" / "hooks-registration.yaml").write_text(
        "hooks:\n"
        "  - event: Stop\n"
        "    command: python3 $HARNESS_ROOT/harness/hooks/foo.py\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo


@pytest.fixture()
def config_repo(tmp_path):
    """A git repo shipping one code file and one deployer-editable config file
    under harness/data/."""
    repo = tmp_path / "repo"
    h = repo / "harness"
    (h / "hooks").mkdir(parents=True)
    (h / "data").mkdir(parents=True)
    (h / "hooks" / "a.py").write_text("print('a')\n")
    (h / "data" / "guard-policy.yaml").write_text("mode: warn\n")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed")
    return repo


class TestLocalizedConfig:
    """Deployer-editable config under harness/data/*.yaml (and the hook
    registration) ships as a baseline in the manifest, but a deploying team is
    EXPECTED to edit it per target. verify downgrades that divergence to a
    warning so a configured deploy stays --strict-clean (gate config is
    tamper-visible via git, not integrity-locked by the manifest). Code drift
    still fails --strict."""

    def test_is_localized_predicate(self):
        sys.path.insert(0, str(_SCRIPTS))
        import verify_install
        assert verify_install.is_localized("harness/data/guard-policy.yaml")
        assert verify_install.is_localized("harness/install/hooks-registration.yaml")
        assert not verify_install.is_localized("harness/hooks/a.py")
        assert not verify_install.is_localized("harness/scripts/build_manifest.py")
        # behavior/integrity data is NOT localized: it stays manifest-locked so a
        # tampered skill-deps (the core-immutable set) / components map fails --strict.
        assert not verify_install.is_localized("harness/data/skill-deps.yaml")
        assert not verify_install.is_localized("harness/data/components.yaml")
        # a documented deployer knob stays localized
        assert verify_install.is_localized("harness/data/stage-policy.yaml")

    def test_tampered_core_data_fails_strict(self, mini_repo):
        # appending to skill-deps.yaml (a manifest-tracked behavior file) must be hard
        # drift, not a silent localized WARN.
        h = mini_repo / "harness"
        (h / "data").mkdir(parents=True, exist_ok=True)
        (h / "data" / "skill-deps.yaml").write_text("core: [plan]\n")
        _git(mini_repo, "add", "harness/data/skill-deps.yaml")
        _git(mini_repo, "commit", "-qm", "add skill-deps")
        _build(mini_repo)
        (h / "data" / "skill-deps.yaml").write_text("core: [plan]\ntampered: true\n")
        proc = _verify(mini_repo, "--strict")
        assert proc.returncode != 0, proc.stdout + proc.stderr
        assert "skill-deps.yaml" in (proc.stdout + proc.stderr)

    def test_corrupt_manifest_is_named_not_crash(self, mini_repo):
        # a truncated/corrupt manifest is reported as one named drift, never a traceback.
        _build(mini_repo)
        sys.path.insert(0, str(_SCRIPTS))
        import verify_install
        (mini_repo / "harness" / "manifest.json").write_text("{ truncated", encoding="utf-8")
        problems = verify_install.verify(mini_repo)
        assert any(rel == verify_install.MANIFEST_REL and "unreadable" in prob
                   for rel, prob in problems), problems
        proc = _verify(mini_repo)  # CLI, no --strict
        assert "Traceback" not in proc.stderr, proc.stderr

    def test_edited_config_does_not_fail_strict(self, config_repo):
        _build(config_repo)
        cfg = config_repo / "harness" / "data" / "guard-policy.yaml"
        cfg.write_text("mode: block\n")  # deployer customization
        proc = _verify(config_repo, "--strict")
        assert proc.returncode == 0, proc.stderr + proc.stdout
        # still named, but as a warning rather than hard drift
        assert "guard-policy.yaml" in (proc.stderr + proc.stdout)

    def test_edited_code_still_fails_strict(self, config_repo):
        _build(config_repo)
        code = config_repo / "harness" / "hooks" / "a.py"
        code.write_text("print('drift')\n")
        proc = _verify(config_repo, "--strict")
        assert proc.returncode != 0
        assert "harness/hooks/a.py" in (proc.stderr + proc.stdout)


class TestHookRegistration:
    """Co-presence between shipped entrypoint hooks and their registration.
    A hook that ships but is never wired fires never (silent no-op); a
    registration that names a missing file would wire a dangling command.
    Both are named per file by --strict (R8)."""

    def test_clean_layout_passes_strict(self, reg_repo):
        _build(reg_repo)
        proc = _verify(reg_repo, "--strict")
        assert proc.returncode == 0, proc.stderr

    def test_unregistered_entrypoint_fails_strict_naming_it(self, reg_repo):
        # A second entrypoint hook ships but is never wired → strict must catch it.
        bar = reg_repo / "harness" / "hooks" / "bar.py"
        bar.write_text('if __name__ == "__main__":\n    pass\n')
        _git(reg_repo, "add", "-A")
        _git(reg_repo, "commit", "-qm", "add bar")
        _build(reg_repo)  # rebuild so manifest hash drift is NOT the failure cause
        proc = _verify(reg_repo, "--strict")
        assert proc.returncode != 0
        assert "bar.py" in (proc.stderr + proc.stdout)

    def test_registration_naming_missing_hook_fails_strict(self, reg_repo):
        reg = reg_repo / "harness" / "install" / "hooks-registration.yaml"
        reg.write_text(reg.read_text() +
                       "  - event: Stop\n"
                       "    command: python3 $HARNESS_ROOT/harness/hooks/ghost.py\n")
        _git(reg_repo, "add", "-A")
        _git(reg_repo, "commit", "-qm", "dangling reg")
        _build(reg_repo)
        proc = _verify(reg_repo, "--strict")
        assert proc.returncode != 0
        assert "ghost.py" in (proc.stderr + proc.stdout)

    def test_library_module_without_main_not_required(self, reg_repo):
        _build(reg_repo)
        proc = _verify(reg_repo, "--strict")
        assert proc.returncode == 0, proc.stderr
        assert "lib.py" not in (proc.stderr + proc.stdout)
