"""test_state_dir_global.py — per-project state under a global install.

Under a global install (HARNESS_BIN_ROOT set, bin != project) the runtime state
writers (trace, telemetry, the session actor cache, mutation-guard snapshots) must
land in the PROJECT's private .harness/state, NOT the shared binary's harness/state.
They all route through hook_runtime._state_dir(), so that one resolver is the
chokepoint: with HARNESS_STATE_DIR unset it must delegate to the project data home
under a global layout, while self-host (HARNESS_BIN_ROOT unset) keeps the legacy
harness/state next to the module (dogfood + the whole suite depend on that).

_state_dir() reads os.environ at call time, so monkeypatch alone drives it — the
tests intentionally do NOT importlib.reload the module (a mid-suite reload poisons
the shared hook_runtime object other test modules hold).
"""
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO_ROOT / "harness" / "hooks"))
sys.path.insert(0, str(_REPO_ROOT / "harness" / "scripts"))

import hook_runtime  # noqa: E402


class TestStateDirGlobal:
    def test_global_state_lands_in_project(self, tmp_path, monkeypatch):
        proj = tmp_path / "proj"
        (proj / ".harness").mkdir(parents=True)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(_REPO_ROOT))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        monkeypatch.delenv("HARNESS_STATE_DIR", raising=False)
        monkeypatch.delenv("HARNESS_DATA_ROOT", raising=False)
        sd = hook_runtime._state_dir().resolve()
        assert sd == (proj / ".harness" / "state").resolve(), sd
        # it must NOT be the shared binary's state dir
        assert (_REPO_ROOT / "harness" / "state").resolve() not in [sd, *sd.parents]

    def test_explicit_state_dir_still_wins(self, tmp_path, monkeypatch):
        explicit = tmp_path / "custom-state"
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(_REPO_ROOT))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
        monkeypatch.setenv("HARNESS_STATE_DIR", str(explicit))
        assert hook_runtime._state_dir() == explicit

    def test_self_host_unchanged(self, tmp_path, monkeypatch):
        # HARNESS_BIN_ROOT unset → legacy state next to the module (harness/state),
        # NOT the .harness/ data home. Dogfood + the whole suite rely on this.
        monkeypatch.delenv("HARNESS_BIN_ROOT", raising=False)
        monkeypatch.delenv("HARNESS_ROOT", raising=False)
        monkeypatch.delenv("HARNESS_STATE_DIR", raising=False)
        monkeypatch.delenv("HARNESS_DATA_ROOT", raising=False)
        sd = hook_runtime._state_dir()
        assert sd.name == "state" and sd.parent.name == "harness", sd

    def test_global_unresolved_project_does_not_crash(self, monkeypatch):
        # global bin invoked with no resolvable project (no CLAUDE_PROJECT_DIR) —
        # the resolver must not raise; it falls back rather than crashing the hook.
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(_REPO_ROOT))
        monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
        monkeypatch.delenv("HARNESS_STATE_DIR", raising=False)
        monkeypatch.delenv("HARNESS_DATA_ROOT", raising=False)
        sd = hook_runtime._state_dir()  # must return SOME path, no exception
        assert isinstance(sd, Path)
