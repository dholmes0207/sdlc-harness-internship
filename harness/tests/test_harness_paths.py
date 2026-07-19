"""test_harness_paths.py — ROOT + state-dir resolution seams.

root() is a back-compat alias for bin_root(). bin_root() order: HARNESS_BIN_ROOT
> HARNESS_ROOT (legacy alias) > __file__-relative (the binary is always where
harness_paths.py lives) > upward marker walk from CWD (harness/manifest.json
post-install, or harness/hooks/ pre-manifest bootstrap) > CWD. state_dir():
HARNESS_STATE_DIR env > data_root()/state (the per-project .harness/state under a
global install; the dogfood self-host collapses it under the repo). Pure
resolution — no mkdir side effects; writers own their mkdir.

The __file__-relative tier fires FIRST among the non-env tiers, so tests that
exercise the CWD walk-up fallback point harness_paths.__file__ at a marker-less
location to force fall-through.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import harness_paths  # noqa: E402


def _no_root_env(monkeypatch):
    for e in ("HARNESS_BIN_ROOT", "HARNESS_ROOT", "HARNESS_DATA_ROOT",
              "CLAUDE_PROJECT_DIR", "HARNESS_STATE_DIR"):
        monkeypatch.delenv(e, raising=False)


def _blind_file(monkeypatch, tmp_path):
    """Point harness_paths.__file__ at a marker-less location so bin_root()'s
    __file__-relative tier misses and the CWD walk-up fallback is exercised."""
    blind = tmp_path / "_blind" / "scripts" / "harness_paths.py"
    blind.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(harness_paths, "__file__", str(blind))


class TestRoot:
    def test_env_override_wins(self, monkeypatch, tmp_path):
        _no_root_env(monkeypatch)
        monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
        assert harness_paths.root() == tmp_path.resolve()

    def test_root_is_bin_root_alias(self, monkeypatch, tmp_path):
        # root() must stay a pure alias for bin_root() so the shipped-binary
        # readers are untouched by the split.
        _no_root_env(monkeypatch)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path))
        assert harness_paths.root() == harness_paths.bin_root()

    def test_file_relative_resolves_bin_root(self, monkeypatch, tmp_path):
        # With no env, the __file__-relative tier finds the bin: harness_paths.py
        # lives at <bin>/harness/scripts/harness_paths.py.
        _no_root_env(monkeypatch)
        binr = tmp_path / "bin"
        (binr / "harness" / "hooks").mkdir(parents=True)
        f = binr / "harness" / "scripts" / "harness_paths.py"
        f.parent.mkdir(parents=True)
        monkeypatch.setattr(harness_paths, "__file__", str(f))
        assert harness_paths.bin_root() == binr.resolve()

    def test_marker_manifest_found_from_nested_cwd(self, monkeypatch, tmp_path):
        repo = tmp_path / "repo"
        (repo / "harness").mkdir(parents=True)
        (repo / "harness" / "manifest.json").write_text("{}", encoding="utf-8")
        deep = repo / "a" / "b"
        deep.mkdir(parents=True)
        _no_root_env(monkeypatch)
        _blind_file(monkeypatch, tmp_path)
        monkeypatch.chdir(deep)
        assert harness_paths.root() == repo.resolve()

    def test_marker_hooks_dir_serves_pre_manifest_bootstrap(self, monkeypatch, tmp_path):
        # Before the first build_manifest run there is no manifest.json yet;
        # the committed harness/hooks/ dir is the bootstrap marker.
        repo = tmp_path / "repo"
        (repo / "harness" / "hooks").mkdir(parents=True)
        deep = repo / "sub"
        deep.mkdir()
        _no_root_env(monkeypatch)
        _blind_file(monkeypatch, tmp_path)
        monkeypatch.chdir(deep)
        assert harness_paths.root() == repo.resolve()

    def test_no_marker_falls_back_to_cwd(self, monkeypatch, tmp_path):
        _no_root_env(monkeypatch)
        _blind_file(monkeypatch, tmp_path)
        monkeypatch.chdir(tmp_path)
        assert harness_paths.root() == tmp_path.resolve()


class TestStateDirs:
    def test_state_env_override_wins(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "st"))
        assert harness_paths.state_dir() == tmp_path / "st"

    def test_state_defaults_under_data_root_selfhost(self, monkeypatch, tmp_path):
        # No HARNESS_STATE_DIR + self-host (HARNESS_BIN_ROOT unset, HARNESS_ROOT
        # aliases the bin, no project env) → state_dir under the per-project
        # data home .harness/state, collapsed under the bin for dogfood.
        _no_root_env(monkeypatch)
        monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
        assert harness_paths.state_dir() == tmp_path.resolve() / ".harness" / "state"

    def test_trace_and_telemetry_nest_under_state(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "st"))
        assert harness_paths.trace_dir() == tmp_path / "st" / "trace"
        assert harness_paths.telemetry_dir() == tmp_path / "st" / "telemetry"

    def test_resolution_is_pure_no_mkdir(self, monkeypatch, tmp_path):
        # Calling the resolvers must not create directories — writers mkdir.
        _no_root_env(monkeypatch)
        monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
        harness_paths.state_dir()
        harness_paths.trace_dir()
        harness_paths.telemetry_dir()
        assert not (tmp_path / "harness").exists()
        assert not (tmp_path / ".harness").exists()


class TestProjectRoot:
    def test_claude_project_dir_wins(self, monkeypatch, tmp_path):
        proj = tmp_path / "proj"
        proj.mkdir()
        _no_root_env(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        assert harness_paths.project_root() == proj.resolve()

    def test_global_resolves_project_from_data_root(self, monkeypatch, tmp_path):
        # global install: bin lives elsewhere, the project is derived from the
        # data home. project_root() must point at the PROJECT, not the bin.
        proj = tmp_path / "proj"
        (proj / ".harness").mkdir(parents=True)
        _no_root_env(monkeypatch)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path / "bin"))
        monkeypatch.setenv("HARNESS_DATA_ROOT", str(proj / ".harness"))
        assert harness_paths.project_root() == (proj / ".harness").resolve().parent

    def test_selfhost_coincides_with_bin_root(self, monkeypatch, tmp_path):
        # self-host (no bin/project env): project_root() == bin root, so a
        # caller switched from root() to project_root() is a no-op here.
        _no_root_env(monkeypatch)
        monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
        assert harness_paths.project_root() == harness_paths.root()

    def test_filesystem_root_project_dir_is_rejected_f7(self, monkeypatch, tmp_path):
        # F7 fail-closed: CLAUDE_PROJECT_DIR="/" must NOT make project_root() the
        # filesystem root — a project-scoped WRITE there (inject stamps
        # "/plans/reports/...") is catastrophic. data_root() already fail-closes on
        # this; project_root() (always concrete) must fall THROUGH to the next tier
        # (here the bin root) rather than hand back "/".
        _no_root_env(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/")
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path / "bin"))
        pr = harness_paths.project_root()
        assert pr != Path("/"), "project_root() handed back the filesystem root"
        assert pr == (tmp_path / "bin").resolve()
