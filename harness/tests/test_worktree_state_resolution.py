"""test_worktree_state_resolution.py — linked-worktree state redirects to HOST.

Closes F2: a git worktree must NOT open its own state store. Both flavours of
state resolution learn to detect a linked worktree (the `.git` FILE + `commondir`
layout) and point state back at the host repo:

  - script flavour  harness_paths.state_dir()  -> host/.harness/state
  - hook   flavour  hook_runtime._state_dir()  -> host/harness/state

The two flavours key off DIFFERENT anchors (R4): the script flavour off
CLAUDE_PROJECT_DIR (else its self-host bin_root()), the hook flavour off
_hooks_dir() and NEVER CLAUDE_PROJECT_DIR — so the redirect must hold even when
CLAUDE_PROJECT_DIR is unset (T8). Precedence is unchanged: HARNESS_STATE_DIR env
still wins over the redirect (T3). Detection is fail-open (T5) and reads a FILE,
never spawns git, and probes the filesystem once per start dir (T6).

Behaviour asserted, never mere import/existence (R9).
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import harness_paths  # noqa: E402
import hook_runtime  # noqa: E402


def _scrub_env(monkeypatch):
    """Strip every state-affecting env so the dev overlay (memory
    dev-env-leaks-into-gate-e2e-subprocess) cannot skew resolution."""
    for e in ("HARNESS_BIN_ROOT", "HARNESS_ROOT", "HARNESS_DATA_ROOT",
              "HARNESS_STATE_DIR", "CLAUDE_PROJECT_DIR"):
        monkeypatch.delenv(e, raising=False)
    harness_paths._reset_worktree_cache()


def _make_worktree(tmp_path, *, gitdir_abs=True):
    """Build a fake host repo + a linked worktree.

    host/.git/                       (dir — the real repo)
        worktrees/wt1/commondir      -> "../.."  (resolves to host/.git)
    wt/.git                          (FILE: "gitdir: <host>/.git/worktrees/wt1")
    wt/harness/{hooks,scripts}/      (so anchors resolve inside the worktree)

    Returns (host_root, worktree_root).
    """
    host = tmp_path / "host"
    wt = tmp_path / "wt"
    gitdir = host / ".git" / "worktrees" / "wt1"
    gitdir.mkdir(parents=True)
    (gitdir / "commondir").write_text("../..\n", encoding="utf-8")
    (host / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (wt / "harness" / "hooks").mkdir(parents=True)
    (wt / "harness" / "scripts").mkdir(parents=True)
    if gitdir_abs:
        target = str(gitdir)
    else:
        # relative to the worktree dir (unusual — real git writes absolute)
        target = str(Path("..") / "host" / ".git" / "worktrees" / "wt1")
    (wt / ".git").write_text("gitdir: %s\n" % target, encoding="utf-8")
    return host.resolve(), wt


def _point_hooks_dir(monkeypatch, wt):
    monkeypatch.setattr(hook_runtime, "_hooks_dir",
                        lambda: (wt / "harness" / "hooks").resolve())


# --------------------------------------------------------------------------- #
# worktree_host_root helper                                                    #
# --------------------------------------------------------------------------- #

class TestHelper:
    def test_worktree_resolves_host(self, monkeypatch, tmp_path):
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        got = harness_paths.worktree_host_root(wt / "harness" / "hooks")
        assert got == host

    def test_main_tree_returns_none(self, monkeypatch, tmp_path):
        # T2: `.git` is a DIR (main worktree) -> no redirect.
        _scrub_env(monkeypatch)
        main = tmp_path / "repo"
        (main / ".git").mkdir(parents=True)
        (main / "harness").mkdir()
        assert harness_paths.worktree_host_root(main / "harness") is None

    def test_garbage_git_file_fail_open(self, monkeypatch, tmp_path):
        # T5: junk `.git` FILE -> None, never raises.
        _scrub_env(monkeypatch)
        wt = tmp_path / "wt"
        (wt / "harness").mkdir(parents=True)
        (wt / ".git").write_text("not a gitdir line\n", encoding="utf-8")
        assert harness_paths.worktree_host_root(wt / "harness") is None

    def test_missing_commondir_fail_open(self, monkeypatch, tmp_path):
        # T5: `.git` points at a gitdir with no commondir -> None.
        _scrub_env(monkeypatch)
        host = tmp_path / "host"
        wt = tmp_path / "wt"
        gitdir = host / ".git" / "worktrees" / "wt1"
        gitdir.mkdir(parents=True)  # no commondir written
        (wt / "harness").mkdir(parents=True)
        (wt / ".git").write_text("gitdir: %s\n" % gitdir, encoding="utf-8")
        assert harness_paths.worktree_host_root(wt / "harness") is None

    def test_relative_gitdir_resolves_to_worktree_not_cwd(self, monkeypatch, tmp_path):
        # T7: a RELATIVE gitdir resolves relative to the worktree, or fail-open
        # None — never a CWD-derived wrong host.
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path, gitdir_abs=False)
        got = harness_paths.worktree_host_root(wt / "harness" / "hooks")
        assert got in (host, None)
        assert got != Path.cwd()

    def test_probes_filesystem_once(self, monkeypatch, tmp_path):
        # T6: two calls with the same start dir -> one probe (memoized).
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        calls = {"n": 0}
        orig = harness_paths._probe_worktree_host_root

        def counting(start):
            calls["n"] += 1
            return orig(start)

        monkeypatch.setattr(harness_paths, "_probe_worktree_host_root", counting)
        a = harness_paths.worktree_host_root(wt / "harness" / "hooks")
        b = harness_paths.worktree_host_root(wt / "harness" / "hooks")
        assert a == b == host
        assert calls["n"] == 1

    def test_no_subprocess_on_success(self, monkeypatch, tmp_path):
        # Success path reads a file — never spawns git.
        import subprocess
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)

        def boom(*a, **k):
            raise AssertionError("worktree_host_root must not spawn a subprocess")

        monkeypatch.setattr(subprocess, "run", boom)
        monkeypatch.setattr(subprocess, "Popen", boom)
        assert harness_paths.worktree_host_root(wt / "harness" / "hooks") == host


# --------------------------------------------------------------------------- #
# script flavour: harness_paths.state_dir()                                    #
# --------------------------------------------------------------------------- #

class TestScriptFlavour:
    def test_t1_redirect_via_claude_project_dir(self, monkeypatch, tmp_path):
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
        assert harness_paths.state_dir() == host / ".harness" / "state"

    def test_t2_main_tree_no_redirect(self, monkeypatch, tmp_path):
        _scrub_env(monkeypatch)
        main = tmp_path / "repo"
        (main / ".git").mkdir(parents=True)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(main))
        assert harness_paths.state_dir() == main.resolve() / ".harness" / "state"

    def test_t3_state_env_wins(self, monkeypatch, tmp_path):
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
        monkeypatch.setenv("HARNESS_STATE_DIR", "/tmp/x")
        assert harness_paths.state_dir() == Path("/tmp/x")

    def test_t8_redirect_without_claude_project_dir(self, monkeypatch, tmp_path):
        # R4: CLAUDE_PROJECT_DIR UNSET, self-host (HARNESS_BIN_ROOT unset) — the
        # script flavour anchors off bin_root(); redirect must STILL fire.
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        monkeypatch.setattr(harness_paths, "bin_root",
                            lambda: (wt).resolve())
        assert harness_paths.state_dir() == host / ".harness" / "state"


# --------------------------------------------------------------------------- #
# hook flavour: hook_runtime._state_dir()                                      #
# --------------------------------------------------------------------------- #

class TestHookFlavour:
    def test_t1_redirect_self_host(self, monkeypatch, tmp_path):
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        _point_hooks_dir(monkeypatch, wt)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
        assert hook_runtime._state_dir() == host / "harness" / "state"

    def test_t3_state_env_wins(self, monkeypatch, tmp_path):
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        _point_hooks_dir(monkeypatch, wt)
        monkeypatch.setenv("HARNESS_STATE_DIR", "/tmp/x")
        assert hook_runtime._state_dir() == Path("/tmp/x")

    def test_t4_bin_root_set_delegates_and_redirects(self, monkeypatch, tmp_path):
        # HARNESS_BIN_ROOT set -> _state_dir delegates to harness_paths.state_dir()
        # which redirects via data_root() (CLAUDE_PROJECT_DIR=worktree).
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        _point_hooks_dir(monkeypatch, wt)
        monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path / "somebin"))
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
        assert hook_runtime._state_dir() == host / ".harness" / "state"

    def test_t5_fail_open_to_legacy(self, monkeypatch, tmp_path):
        # Junk `.git` -> helper None -> legacy self-host dir, no crash.
        _scrub_env(monkeypatch)
        wt = tmp_path / "wt"
        (wt / "harness" / "hooks").mkdir(parents=True)
        (wt / ".git").write_text("garbage\n", encoding="utf-8")
        _point_hooks_dir(monkeypatch, wt)
        assert hook_runtime._state_dir() == (wt / "harness" / "state").resolve()

    def test_t8_redirect_without_claude_project_dir(self, monkeypatch, tmp_path):
        # R4: hook flavour anchors off _hooks_dir(), NOT CLAUDE_PROJECT_DIR — the
        # redirect must hold with CLAUDE_PROJECT_DIR unset.
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        _point_hooks_dir(monkeypatch, wt)
        assert hook_runtime._state_dir() == host / "harness" / "state"


class TestFlavoursDoNotCrossWire:
    def test_two_flavours_map_to_own_suffix(self, monkeypatch, tmp_path):
        # hook flavour -> host/harness/state ; script flavour -> host/.harness/state.
        _scrub_env(monkeypatch)
        host, wt = _make_worktree(tmp_path)
        _point_hooks_dir(monkeypatch, wt)
        monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(wt))
        hook_sd = hook_runtime._state_dir()
        script_sd = harness_paths.state_dir()
        assert hook_sd == host / "harness" / "state"
        assert script_sd == host / ".harness" / "state"
        assert hook_sd != script_sd
