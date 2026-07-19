"""test_bin_data_root_split.py — the bin/data root split contract.

A global install serves ONE shared binary (bin_root) to many projects, each with
its own private data home (data_root = <project>/.harness). This suite pins the
resolution precedence, the self-hosted collapse (bin==project), the two-project
state isolation, and the fail-closed marker that a broken global layout must NOT
decay past (never silent CWD).

Seams are env vars only (no monkeypatch of module internals) — the resolvers read
HARNESS_BIN_ROOT / HARNESS_ROOT / HARNESS_DATA_ROOT / CLAUDE_PROJECT_DIR /
HARNESS_STATE_DIR. Every test scrubs the ambient env first so the dogfood
session's own values never leak in.
"""
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import harness_paths  # noqa: E402

_ROOT_ENVS = ("HARNESS_BIN_ROOT", "HARNESS_ROOT", "HARNESS_DATA_ROOT",
              "CLAUDE_PROJECT_DIR", "HARNESS_STATE_DIR")


def _scrub(monkeypatch):
    for e in _ROOT_ENVS:
        monkeypatch.delenv(e, raising=False)


class TestBinRoot:
    def test_bin_root_from_env(self, monkeypatch, tmp_path):
        _scrub(monkeypatch)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path))
        assert harness_paths.bin_root() == tmp_path.resolve()

    def test_bin_root_legacy_harness_root_alias(self, monkeypatch, tmp_path):
        # HARNESS_ROOT is kept as a bin_root() alias for the old test/e2e seams.
        _scrub(monkeypatch)
        monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
        assert harness_paths.bin_root() == tmp_path.resolve()

    def test_bin_root_env_beats_legacy(self, monkeypatch, tmp_path):
        _scrub(monkeypatch)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path / "bin"))
        monkeypatch.setenv("HARNESS_ROOT", str(tmp_path / "legacy"))
        assert harness_paths.bin_root() == (tmp_path / "bin").resolve()


class TestDataRoot:
    def test_data_root_from_claude_project_dir(self, monkeypatch, tmp_path):
        _scrub(monkeypatch)
        proj = tmp_path / "proj"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        assert harness_paths.data_root() == proj.resolve() / ".harness"

    def test_data_root_env_wins(self, monkeypatch, tmp_path):
        _scrub(monkeypatch)
        d = tmp_path / "d"  # nested — a real parent, not the fs root (see F7 test)
        monkeypatch.setenv("HARNESS_DATA_ROOT", str(d))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
        assert harness_paths.data_root() == d.resolve()

    def test_state_dir_under_data_root(self, monkeypatch, tmp_path):
        _scrub(monkeypatch)
        proj = tmp_path / "proj"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        assert harness_paths.state_dir() == proj.resolve() / ".harness" / "state"

    def test_two_projects_distinct_state(self, monkeypatch, tmp_path):
        # The race the brief names: two projects must never share a state dir.
        _scrub(monkeypatch)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "A"))
        a = harness_paths.state_dir()
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "B"))
        b = harness_paths.state_dir()
        assert a != b
        assert a == (tmp_path / "A").resolve() / ".harness" / "state"
        assert b == (tmp_path / "B").resolve() / ".harness" / "state"


class TestSelfHostAndFailClosed:
    def test_self_hosted_collapse(self, monkeypatch, tmp_path):
        # Self-host is detected by HARNESS_BIN_ROOT UNSET (NOT by a .git walk-up).
        # With no project env, data_root collapses under the bin (dogfood).
        _scrub(monkeypatch)
        monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))  # bin alias, BIN_ROOT unset
        assert harness_paths.data_root() == tmp_path.resolve() / ".harness"
        assert not harness_paths.data_root_unresolved(harness_paths.data_root())

    def test_data_root_failclosed_global_bin_git_present(self, monkeypatch, tmp_path):
        # F2 / the C4-defeats-C5 trap: a global bin is itself a git checkout.
        # HARNESS_BIN_ROOT SET + no project env → fail-closed marker, NEVER a
        # self-host collapse that would unlock the shared bin.
        _scrub(monkeypatch)
        binr = tmp_path / "bin"
        (binr / ".git").mkdir(parents=True)
        (binr / "harness" / "hooks").mkdir(parents=True)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(binr))
        assert harness_paths.data_root_unresolved(harness_paths.data_root())

    def test_data_root_failclosed_when_unresolved(self, monkeypatch, tmp_path):
        # Global layout, no project env, no marker → fail-closed marker, NOT CWD.
        _scrub(monkeypatch)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path / "bin"))
        monkeypatch.chdir(tmp_path)
        dr = harness_paths.data_root()
        assert harness_paths.data_root_unresolved(dr)
        assert dr != tmp_path.resolve() / ".harness"  # never decayed to CWD

    def test_data_root_parent_root_rejected(self, monkeypatch, tmp_path):
        # F7: a data root whose parent is "/" (e.g. HARNESS_DATA_ROOT=/data) is
        # not a real project — treat as unresolved.
        _scrub(monkeypatch)
        monkeypatch.setenv("HARNESS_DATA_ROOT", "/data")
        assert harness_paths.data_root_unresolved(harness_paths.data_root())


class TestToolContextCwdWalk:
    """The tool-context resolver (`tool_data_root()`/`tool_state_dir()`): a lane
    companion runs via the Bash tool, whose env carries NO CLAUDE_PROJECT_DIR.
    Under a global layout it must resolve its OWN project from CWD (walk up to
    the nearest `.harness/`) instead of crashing on the fail-closed marker —
    WITHOUT unlocking the guard path, which stays fail-closed on the bare
    `data_root()`/`state_dir()` calls.
    """

    def _global(self, monkeypatch, tmp_path):
        # global layout: BIN_ROOT set, no project env (mirrors a Bash-tool env).
        _scrub(monkeypatch)
        binr = tmp_path / "bin"
        (binr / "harness" / "hooks").mkdir(parents=True)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(binr))
        return binr

    def test_tool_walk_resolves_enclosing_project(self, monkeypatch, tmp_path):
        self._global(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        (proj / ".harness").mkdir(parents=True)
        monkeypatch.chdir(proj)
        assert harness_paths.tool_data_root() == (proj / ".harness").resolve()

    def test_tool_walk_from_deep_subdir(self, monkeypatch, tmp_path):
        # a companion may run from a nested cwd; walk-up still finds the project.
        self._global(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        (proj / ".harness").mkdir(parents=True)
        deep = proj / "a" / "b" / "c"
        deep.mkdir(parents=True)
        monkeypatch.chdir(deep)
        assert harness_paths.tool_data_root() == (proj / ".harness").resolve()
        assert harness_paths.tool_state_dir() == (proj / ".harness").resolve() / "state"

    def test_guard_default_stays_failclosed_same_cwd(self, monkeypatch, tmp_path):
        # THE security invariant: the SAME cwd that a tool resolves, a guard
        # (default, no opt-in) must still see as unresolved — walk-up never leaks
        # into the fail-closed guard path.
        self._global(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        (proj / ".harness").mkdir(parents=True)
        monkeypatch.chdir(proj)
        assert harness_paths.data_root_unresolved(harness_paths.data_root())
        assert harness_paths.data_root_unresolved(harness_paths.state_dir().parent)

    def test_tool_walk_bare_cwd_stays_failclosed(self, monkeypatch, tmp_path):
        # no `.harness/` encloses cwd → even the opt-in tool fails closed rather
        # than fabricating a project (never decays to CWD).
        self._global(monkeypatch, tmp_path)
        bare = tmp_path / "bare"
        bare.mkdir()
        monkeypatch.chdir(bare)
        assert harness_paths.data_root_unresolved(harness_paths.tool_data_root())

    def test_tool_walk_never_keys_off_git(self, monkeypatch, tmp_path):
        # F2: a `.git` dir (a bare checkout, e.g. the global bin) is NOT a project
        # marker — only an existing `.harness/` is. A git checkout without
        # `.harness/` stays fail-closed for the tool too.
        self._global(monkeypatch, tmp_path)
        checkout = tmp_path / "checkout"
        (checkout / ".git").mkdir(parents=True)
        monkeypatch.chdir(checkout)
        assert harness_paths.data_root_unresolved(harness_paths.tool_data_root())

    def test_job_registry_lives_under_global_from_cwd(self, monkeypatch, tmp_path):
        # end-to-end: the crash the bug reproduced (JobRegistry mkdir under the
        # fail-closed marker) is gone — it now lands under the cwd project.
        self._global(monkeypatch, tmp_path)
        proj = tmp_path / "proj"
        (proj / ".harness").mkdir(parents=True)
        monkeypatch.chdir(proj)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent
                               / "plugins" / "hs" / "scripts"))
        import partner_core
        reg = partner_core.JobRegistry(subdir="gemini")
        assert reg._dir == (proj / ".harness").resolve() / "state" / "gemini"
        reg.append({"job_id": "t1", "status": "ok"})
        assert [r["job_id"] for r in reg.read_all()] == ["t1"]


class TestStrictParamRemoved:
    """`allow_cwd_walk` is gone from the strict resolvers — a bool on a trust
    boundary is a trap (unlintable, nothing stops a future caller widening a
    guard lane by passing True). The opt-in now lives only in the named
    tool_data_root()/tool_state_dir() wrappers."""

    def test_data_root_has_no_walk_param(self, monkeypatch, tmp_path):
        _scrub(monkeypatch)
        proj = tmp_path / "proj"
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
        # no-param calls still resolve strict correctly.
        assert harness_paths.data_root() == proj.resolve() / ".harness"
        assert harness_paths.state_dir() == proj.resolve() / ".harness" / "state"
        # the param itself is gone — passing it is a TypeError, not a silent no-op.
        with pytest.raises(TypeError):
            harness_paths.data_root(allow_cwd_walk=True)
        with pytest.raises(TypeError):
            harness_paths.state_dir(allow_cwd_walk=True)


class TestGuardContextAbsenceGuard:
    """The trust boundary is now grep-enforceable: no file a guard/hook can
    load may reference the tool-context resolvers. Scans the FILESYSTEM (not
    `git grep`, which only sees tracked files and would miss an uncommitted
    hook) over harness/hooks/**/*.py PLUS harness/scripts/fs_guard.py (a
    guard-class helper living outside hooks/ that also calls data_root())."""

    def _guard_context_files(self):
        repo_root = Path(__file__).resolve().parent.parent.parent
        hooks_dir = repo_root / "harness" / "hooks"
        files = []
        for dirpath, _dirnames, filenames in os.walk(hooks_dir):
            for name in filenames:
                if name.endswith(".py"):
                    files.append(Path(dirpath) / name)
        files.append(repo_root / "harness" / "scripts" / "fs_guard.py")
        return files

    def test_guard_context_never_imports_tool_resolver(self):
        # the functions must exist (this is the RED half before implement —
        # a hasattr check that fails vacuously-green grep from masking absence).
        assert hasattr(harness_paths, "tool_" + "data_root")
        assert hasattr(harness_paths, "tool_" + "state_dir")

        # forbidden tokens built from fragments so this test file itself never
        # contains the literal token (defensive against a broader future scan).
        forbidden = ("tool_" + "data_root", "tool_" + "state_dir")
        offenders = []
        for path in self._guard_context_files():
            if not path.is_file():
                continue
            text = path.read_text(encoding="utf-8", errors="replace")
            for token in forbidden:
                if token in text:
                    offenders.append(f"{path}: {token}")
        assert offenders == []


class TestInstallMetadata:
    def test_install_metadata_stays_bin_side(self, monkeypatch, tmp_path):
        # install-omitted-skills.json describes the BINARY, not a project — under
        # a global layout it resolves under bin_state_dir(), never per-project
        # .harness/state.
        _scrub(monkeypatch)
        binr = tmp_path / "bin"
        (binr / "harness" / "hooks").mkdir(parents=True)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(binr))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path / "proj"))
        bsd = harness_paths.bin_state_dir()
        assert bsd == binr.resolve() / "harness" / "state"

        import omit_record
        rec = omit_record.record_path(harness_paths.bin_root())
        assert bsd in rec.parents
        # the project data root exists and is distinct — metadata never lands there
        assert harness_paths.data_root() == (tmp_path / "proj").resolve() / ".harness"
        assert harness_paths.data_root() not in rec.parents
