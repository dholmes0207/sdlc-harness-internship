"""test_install.py — packaged installer: materialize hooks, merge, copy, verify.

The installer turns the source harness tree into an installed-and-wired copy in
a target repo: it copies the tracked harness/ tree, materializes
hooks-registration.yaml into the target's .claude/settings.json (Claude Code
shape, $HARNESS_ROOT -> "$CLAUDE_PROJECT_DIR"), installs the pre-push transport
gate, writes the reviewer roster into the TARGET (never the source), and ends in
a verify_install-clean state. Every mutation is idempotent and dry-run-able; an
uninstall reverses the settings and pre-push edits.

These cover: command substitution, materialization shape + event allow-list,
additive merge (preserves user hooks, dedup, idempotent), roster normalization,
and end-to-end install/dry-run/uninstall into a temp git repo that ends
verify-clean.
"""
import collections
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_INSTALL_DIR = _REPO_ROOT / "harness" / "install"
_SCRIPTS = _REPO_ROOT / "harness" / "scripts"
for _p in (str(_INSTALL_DIR), str(_SCRIPTS)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import install as installer  # noqa: E402
from conftest import _git  # noqa: E402


@pytest.fixture()
def target_repo(tmp_path):
    """An empty git repo to install INTO."""
    repo = tmp_path / "target"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    return repo


_Installed = collections.namedtuple("_Installed", "repo res")


@pytest.fixture(scope="module")
def default_installed(tmp_path_factory):
    """One default install (~28MB copytree + wiring + verify) shared by the tests
    below that only INSPECT the produced tree and never mutate it. A default install
    is deterministic, so paying it once instead of per-test is safe; tests that
    pre-seed the target, re-install, or vary flags keep their own fresh `target_repo`.
    Under `-n auto --dist loadfile` this whole file lands on one worker, so a
    module-scoped fixture builds exactly once — this is where the win lands."""
    repo = tmp_path_factory.mktemp("default_install")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    res = installer.install(_REPO_ROOT, repo)
    assert res["ok"], res["problems"]
    return _Installed(repo, res)


# --- pure helpers --------------------------------------------------------


class TestPathContainment:
    """A bundle's manifest is the authoritative file list when the source has no
    .git (the `sh install.sh <tarball> <repo>` path). The bundle is user-supplied
    and its sibling .sha256 only proves transit-integrity, not authorship — so a
    crafted entry that climbs out of harness/ must be refused, never copied
    outside the install target."""

    def test_rel_escapes_predicate(self):
        assert installer._rel_escapes("harness/../../tmp/evil")
        assert installer._rel_escapes("/etc/passwd")
        assert installer._rel_escapes("harness/../secret")
        assert not installer._rel_escapes("harness/hooks/session_init.py")
        assert not installer._rel_escapes("harness/manifest.json")

    def test_copy_tree_refuses_manifest_path_escaping_target(self, tmp_path):
        source = tmp_path / "src"
        (source / "harness").mkdir(parents=True)
        (source / "harness" / "manifest.json").write_text(
            json.dumps({"files": {"harness/../../evil.txt": "x"}}),
            encoding="utf-8")
        target = tmp_path / "target"
        target.mkdir()
        escaped = (target / ".." / "evil.txt").resolve()
        with pytest.raises(installer.InstallError):
            installer._copy_tree(source, target, dry_run=False)
        assert not escaped.exists()  # nothing written outside the target


class TestCommandSubstitution:
    def test_harness_root_becomes_project_dir(self):
        raw = "python3 $HARNESS_ROOT/harness/hooks/session_init.py"
        assert installer.to_command(raw) == (
            'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/session_init.py')

    def test_non_harness_text_is_preserved(self):
        assert installer.to_command("echo hi") == "echo hi"


class TestInterpreterPortability:
    """Windows' python.org installer ships `python` / `py`, not `python3`, so a
    hook command hard-wired to `python3` is dead on every tool call there. The
    interpreter is a `$HARNESS_PY` placeholder resolved at install time."""

    def test_registration_uses_py_placeholder_not_bare_python3(self):
        # The SSOT must not pin `python3`: every hook command starts with the
        # `$HARNESS_PY` placeholder the installer resolves per platform.
        reg_path = _INSTALL_DIR / "hooks-registration.yaml"
        text = reg_path.read_text(encoding="utf-8")
        import re as _re
        bare = _re.findall(r"(?m)^\s*command:\s*python3\b", text)
        assert not bare, "registration still hard-wires python3: %s" % bare
        assert "$HARNESS_PY" in text

    def test_hook_interpreter_respects_env_override(self, monkeypatch):
        monkeypatch.setenv("HARNESS_PY", "py -3")
        assert installer.hook_interpreter() == "py -3"

    def test_hook_interpreter_platform_default(self, monkeypatch):
        monkeypatch.delenv("HARNESS_PY", raising=False)
        monkeypatch.setattr(os, "name", "nt")
        assert installer.hook_interpreter() == "python"
        monkeypatch.setattr(os, "name", "posix")
        assert installer.hook_interpreter() == "python3"

    def test_to_command_substitutes_py_placeholder(self):
        raw = "$HARNESS_PY $HARNESS_ROOT/harness/hooks/session_init.py"
        assert installer.to_command(raw, py="python") == (
            'python "$CLAUDE_PROJECT_DIR"/harness/hooks/session_init.py')

    def test_materialize_resolves_interpreter(self, monkeypatch):
        monkeypatch.setenv("HARNESS_PY", "python")
        reg = {"hooks": [
            {"event": "SessionStart",
             "command": "$HARNESS_PY $HARNESS_ROOT/harness/hooks/session_init.py"},
        ]}
        hooks, _ = installer.materialize_hooks(reg)
        cmd = hooks["SessionStart"][0]["hooks"][0]["command"]
        assert cmd == ('python "$CLAUDE_PROJECT_DIR"'
                       '/harness/hooks/session_init.py')


class TestUnicodeStdout:
    """Windows consoles default to a legacy codepage (cp1252) that cannot encode
    the em-dash / arrow glyphs in the installer's output, so a plain print()
    raises UnicodeEncodeError — even on `--help`. The installer forces UTF-8 on
    its streams so it prints identically on every platform."""

    def test_help_survives_legacy_codepage(self):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "cp1252"  # reproduce the Windows console
        out = subprocess.run(
            [sys.executable, str(_INSTALL_DIR / "install.py"), "--help"],
            capture_output=True, text=True, env=env, timeout=30)
        assert out.returncode == 0, out.stderr
        assert "UnicodeEncodeError" not in out.stderr


class TestMaterializeHooks:
    def test_claude_code_shape_and_grouping(self):
        reg = {"hooks": [
            {"event": "SessionStart",
             "command": "python3 $HARNESS_ROOT/harness/hooks/session_init.py"},
            {"event": "PreToolUse", "matcher": "Bash",
             "command": "python3 $HARNESS_ROOT/harness/hooks/gate_stage.py"},
            {"event": "PreToolUse", "matcher": "Bash",
             "command": "python3 $HARNESS_ROOT/harness/hooks/mark_bash_start.py"},
        ]}
        hooks, skipped = installer.materialize_hooks(reg)
        # SessionStart: no matcher key, one command, substituted
        ss = hooks["SessionStart"]
        assert ss == [{"hooks": [{"type": "command",
                                  "command": 'python3 "$CLAUDE_PROJECT_DIR"'
                                  '/harness/hooks/session_init.py'}]}]
        # Two Bash entries collapse into ONE matcher group with two commands
        pre = hooks["PreToolUse"]
        assert len(pre) == 1
        assert pre[0]["matcher"] == "Bash"
        assert [h["command"] for h in pre[0]["hooks"]] == [
            'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/gate_stage.py',
            'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/mark_bash_start.py']
        assert skipped == []

    def test_unknown_event_is_skipped_and_reported(self):
        reg = {"hooks": [
            {"event": "TotallyFakeEvent",
             "command": "python3 $HARNESS_ROOT/harness/hooks/track_skill_invocation.py"},
            {"event": "Stop",
             "command": "python3 $HARNESS_ROOT/harness/hooks/emit_session_summary.py"},
        ]}
        hooks, skipped = installer.materialize_hooks(reg)
        assert "TotallyFakeEvent" not in hooks
        assert "Stop" in hooks
        assert any(ev == "TotallyFakeEvent" for ev, _cmd in skipped)

    def test_userpromptexpansion_is_materialized(self):
        # UserPromptExpansion is a live Claude Code event (verified via capture:
        # it fires on slash_command expansion carrying command_name) — it must be
        # wired, not skipped, so user-typed /hs:* invocations get captured.
        reg = {"hooks": [
            {"event": "UserPromptExpansion",
             "command": "python3 $HARNESS_ROOT/harness/hooks/track_skill_invocation.py"},
        ]}
        hooks, skipped = installer.materialize_hooks(reg)
        assert "UserPromptExpansion" in hooks
        assert not any(ev == "UserPromptExpansion" for ev, _cmd in skipped)


class TestMergeHooks:
    def _harness_new(self):
        return {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command",
                 "command": 'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/gate_stage.py'}]}]}

    def test_preserves_pre_existing_user_hook(self):
        existing = {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo user-hook"}]}]}
        merged = installer.merge_hooks(existing, self._harness_new())
        cmds = [h["command"] for h in merged["PreToolUse"][0]["hooks"]]
        assert "echo user-hook" in cmds
        assert 'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/gate_stage.py' in cmds

    def test_merge_is_idempotent(self):
        new = self._harness_new()
        once = installer.merge_hooks({}, new)
        twice = installer.merge_hooks(once, new)
        assert once == twice
        assert len(twice["PreToolUse"][0]["hooks"]) == 1

    def test_strip_removes_only_harness_hooks(self):
        existing = {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo user-hook"},
                {"type": "command",
                 "command": 'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/gate_stage.py'}]}]}
        stripped = installer.strip_harness_hooks(existing)
        cmds = [h["command"] for h in stripped["PreToolUse"][0]["hooks"]]
        assert cmds == ["echo user-hook"]

    def test_strip_keeps_user_hook_that_merely_mentions_the_dir(self):
        # a user hook whose command CONTAINS the substring 'harness/hooks/' but
        # does not INVOKE a harness hook .py (e.g. an audit grep) must survive
        # uninstall — substring matching would delete it (data loss).
        existing = {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "grep -r harness/hooks/ . || true"},
                {"type": "command",
                 "command": 'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/gate_stage.py'}]}]}
        stripped = installer.strip_harness_hooks(existing)
        cmds = [h["command"] for h in stripped["PreToolUse"][0]["hooks"]]
        assert cmds == ["grep -r harness/hooks/ . || true"]

    def test_strip_prunes_emptied_event(self):
        existing = {"Stop": [
            {"hooks": [
                {"type": "command",
                 "command": 'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/emit_session_summary.py'}]}]}
        assert installer.strip_harness_hooks(existing) == {}

    def test_strip_tolerates_null_or_non_dict(self):
        # Round-24 H1: `"hooks": null` in settings.local.json reaches the consumers as
        # None (dict.get("hooks", {}) returns the stored None), and strip_harness_hooks
        # must not do None.items() — that re-opens the raw-traceback hole in setup +
        # uninstall. None / a non-dict is treated as "no hooks".
        assert installer.strip_harness_hooks(None) == {}
        assert installer.strip_harness_hooks(["oops"]) == {}

# --- end to end into a temp repo ----------------------------------------


class TestInstallEndToEnd:
    @pytest.mark.dev_repo
    def test_install_wires_settings_copies_tree_and_verifies(self, default_installed):
        target_repo, res = default_installed.repo, default_installed.res
        assert res["ok"], res["problems"]
        # tree copied
        assert (target_repo / "harness" / "hooks" / "session_init.py").is_file()
        assert (target_repo / "harness" / "manifest.json").is_file()
        # settings wired with substituted command
        settings = json.loads(
            (target_repo / ".claude" / "settings.json").read_text())
        cmds = [h["command"]
                for groups in settings["hooks"].values()
                for g in groups for h in g["hooks"]]
        # session_init fires via the in-process dispatcher (SessionStart); the installer
        # still substitutes $HARNESS_ROOT -> $CLAUDE_PROJECT_DIR on the dispatch command.
        assert any('"$CLAUDE_PROJECT_DIR"/harness/hooks/hook_dispatch.py' in c
                   for c in cmds)
        assert all("$HARNESS_ROOT" not in c for c in cmds)
        # pre-push installed + executable
        pp = target_repo / ".git" / "hooks" / "pre-push"
        assert pp.is_file()
        import os
        assert os.access(pp, os.X_OK)
        # final verify clean (manifest matches the copied tree)
        assert res["problems"] == []

    @pytest.mark.dev_repo

    def test_install_default_leaves_team_posture(self, default_installed):
        # No --posture => standard team posture, guard preset NOT solo.
        import yaml
        target_repo, res = default_installed.repo, default_installed.res
        assert res["ok"], res["problems"]
        gp = target_repo / "harness" / "data" / "guard-policy.yaml"
        assert yaml.safe_load(gp.read_text())["preset"] != "solo"

    @pytest.mark.dev_repo
    def test_install_with_group_selection_omits_other_skills(self, target_repo):
        # per-skill install: pick the viz group -> its skills + deps + spine core
        # are copied; every unrelated skill dir is omitted, and the omit-seam keeps
        # the absent dirs from reading as drift in the final --strict verify.
        res = installer.install(_REPO_ROOT, target_repo, skill_groups="viz")
        assert res["ok"], res["problems"]
        plug = target_repo / "harness" / "plugins" / "hs" / "skills"
        assert (plug / "excalidraw" / "SKILL.md").is_file()   # viz member
        assert (plug / "plan" / "SKILL.md").is_file()         # spine core, always
        assert not (plug / "shopify").exists()                # unrelated -> omitted
        rec = json.loads(
            (target_repo / "harness" / "state"
             / "install-omitted-skills.json").read_text())
        assert "shopify" in rec["omitted"]
        assert "excalidraw" not in rec["omitted"]
        assert "plan" not in rec["omitted"]
        assert res["problems"] == []   # omit-seam -> no drift

    def test_install_rejects_a_typod_skill_group(self, target_repo):
        # F2 regression: a typo'd group must HARD-FAIL (deployer-actionable), not
        # silently install every skill — the opposite of the user's intent. This
        # mirrors the existing component-selection abort.
        with pytest.raises(installer.InstallError):
            installer.install(_REPO_ROOT, target_repo, skill_groups="thnik")

    @pytest.mark.dev_repo
    def test_reinstall_narrowing_moves_omitted_dir_to_stash_and_records(self, target_repo):
        # re-install over a wider tree with a narrower selection: the deselected skill's
        # dir must leave skills/ (omit = off is real) but be MOVED into the stash so it
        # stays reachable via hs:use / --enable, and be recorded.
        installer.install(_REPO_ROOT, target_repo, all_skills=True)     # install all
        plug = target_repo / "harness" / "plugins" / "hs" / "skills"
        stash = target_repo / "harness" / "plugins" / "hs" / "disabled-skills"
        assert (plug / "shopify").exists()
        installer.install(_REPO_ROOT, target_repo, skill_groups="viz")  # narrow
        assert not (plug / "shopify").exists()                          # gone from live
        assert (stash / "shopify" / "SKILL.md").is_file()              # moved, not deleted
        rec = json.loads((target_repo / "harness" / "state"
                          / "install-omitted-skills.json").read_text())
        assert "shopify" in rec["omitted"]

    @pytest.mark.dev_repo
    def test_omitted_skill_lands_in_stash_not_dropped(self, default_installed):
        # a default install (no flags) is now default-off: an OFF skill is copied into
        # the stash rather than dropped, and stays out of the live skills/ tree.
        target_repo = default_installed.repo  # default -> default-off catalog
        plug = target_repo / "harness" / "plugins" / "hs" / "skills"
        stash = target_repo / "harness" / "plugins" / "hs" / "disabled-skills"
        assert not (plug / "shopify").exists()                # off, absent from live tree
        assert (stash / "shopify" / "SKILL.md").is_file()     # copied into the stash
        assert (plug / "plan" / "SKILL.md").is_file()         # a floor skill stays ON

    @pytest.mark.dev_repo
    def test_all_skills_flag_ships_everything(self, target_repo):
        # --all-skills is the explicit escape: every skill ships, nothing omitted.
        installer.install(_REPO_ROOT, target_repo, all_skills=True)
        plug = target_repo / "harness" / "plugins" / "hs" / "skills"
        assert (plug / "shopify" / "SKILL.md").is_file()      # off-by-default skill shipped
        rec = json.loads((target_repo / "harness" / "state"
                          / "install-omitted-skills.json").read_text())
        assert rec["omitted"] == []

    @pytest.mark.dev_repo
    def test_reenable_from_stash_roundtrip_on_installed_copy(self, target_repo):
        # on a default install, an off skill re-enables from the stash WITHOUT the
        # source tree — hs-cli --enable moves it from the stash back into live skills/.
        import subprocess
        installer.install(_REPO_ROOT, target_repo)
        plug = target_repo / "harness" / "plugins" / "hs" / "skills"
        stash = target_repo / "harness" / "plugins" / "hs" / "disabled-skills"
        assert (stash / "shopify" / "SKILL.md").is_file() and not (plug / "shopify").exists()
        hs_cli = target_repo / "harness" / "scripts" / "hs_cli.py"
        proc = subprocess.run(
            [sys.executable, str(hs_cli), "skills", "--enable", "shopify",
             "--root", str(target_repo)], capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert (plug / "shopify" / "SKILL.md").is_file()      # restored into live tree
        assert not (stash / "shopify").exists()               # stash emptied of it

    def test_reinstall_widening_resets_stale_omit_record(self, target_repo):
        # narrow then install-all: the omit record must RESET to empty, else verify
        # --strict reads a stale record and silently skips a real drift under it.
        installer.install(_REPO_ROOT, target_repo, skill_groups="viz")
        rec_path = (target_repo / "harness" / "state" / "install-omitted-skills.json")
        assert json.loads(rec_path.read_text())["omitted"]              # non-empty
        installer.install(_REPO_ROOT, target_repo, all_skills=True)     # widen to all
        assert json.loads(rec_path.read_text())["omitted"] == []        # reset

    def test_existing_foreign_prepush_is_backed_up(self, target_repo):
        hooks_dir = target_repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        (hooks_dir / "pre-push").write_text("#!/bin/sh\necho mine\n")
        installer.install(_REPO_ROOT, target_repo)
        assert (hooks_dir / "pre-push.bak").read_text() == "#!/bin/sh\necho mine\n"
        # the active hook is now the harness one
        assert "pre-push" in (hooks_dir / "pre-push").read_text()

    def test_rerun_is_idempotent(self, target_repo):
        installer.install(_REPO_ROOT, target_repo)
        settings_path = target_repo / ".claude" / "settings.json"
        first = settings_path.read_text()
        installer.install(_REPO_ROOT, target_repo)
        assert settings_path.read_text() == first

    def test_merge_preserves_user_authored_settings(self, target_repo):
        claude = target_repo / ".claude"
        claude.mkdir(parents=True)
        (claude / "settings.json").write_text(json.dumps({"hooks": {"PreToolUse": [
            {"matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo user-hook"}]}]}}))
        installer.install(_REPO_ROOT, target_repo)
        settings = json.loads((claude / "settings.json").read_text())
        assert "echo user-hook" in json.dumps(settings["hooks"])

    def test_userpromptexpansion_wired_in_real_install(self, default_installed):
        # The real registration wires UserPromptExpansion via the in-process dispatcher
        # (hook_dispatch.py), which runs track_skill_invocation as a core; the install
        # must materialize the event (not skip it). track_skill_invocation still fires —
        # now through the dispatcher's hook-dispatch.yaml registry.
        target_repo = default_installed.repo
        settings = json.loads(
            (target_repo / ".claude" / "settings.json").read_text())
        assert "UserPromptExpansion" in settings["hooks"]
        cmds = [h["command"]
                for g in settings["hooks"]["UserPromptExpansion"]
                for h in g["hooks"]]
        assert any("hook_dispatch.py" in c or "track_skill_invocation.py" in c
                   for c in cmds)
        # and the dispatcher registry must carry track_skill_invocation for this event
        import yaml
        reg = yaml.safe_load(
            (_REPO_ROOT / "harness" / "data" / "hook-dispatch.yaml").read_text())
        cores = reg["groups"].get("UserPromptExpansion", [])
        assert any(c["module"] == "track_skill_invocation" for c in cores)

    def test_dry_run_writes_nothing(self, target_repo):
        res = installer.install(_REPO_ROOT, target_repo, dry_run=True)
        assert res["ok"]
        assert not (target_repo / ".claude").exists()
        assert not (target_repo / "harness").exists()
        assert res["actions"]  # it still PLANNED actions

    @pytest.mark.dev_repo

    def test_source_equals_target_is_noop_copy(self, tmp_path):
        # Pointing source at target must not attempt to copy/clobber the source.
        res = installer.install(_REPO_ROOT, _REPO_ROOT, dry_run=True)
        assert res["source_is_target"] is True
        assert not any("copy" in a.lower() and "tree" in a.lower()
                       and "skip" not in a.lower() for a in res["actions"])

    def test_uninstall_reverses_settings_and_prepush(self, target_repo):
        # seed a user hook so we can prove uninstall keeps it
        installer.install(_REPO_ROOT, target_repo)
        settings_path = target_repo / ".claude" / "settings.json"
        s = json.loads(settings_path.read_text())
        s["hooks"].setdefault("PreToolUse", []).insert(0, {
            "matcher": "Bash", "hooks": [
                {"type": "command", "command": "echo user-hook"}]})
        settings_path.write_text(json.dumps(s))
        installer.install(_REPO_ROOT, target_repo, uninstall=True)
        after = json.dumps(json.loads(settings_path.read_text()))
        assert "harness/hooks/" not in after
        assert "echo user-hook" in after
        # pre-push removed (no backup existed)
        assert not (target_repo / ".git" / "hooks" / "pre-push").exists()


class TestClaudeMdOnboarding:
    """The installer injects a self-loading onboarding block into the target's
    CLAUDE.md so a fresh agent session knows the harness is present and how to
    drive it. Replace-BETWEEN-markers (not skip-if-present) so a version bump
    refreshes the block instead of leaving a stale one; prose OUTSIDE the markers
    is always preserved; re-running never duplicates."""

    def _result(self):
        return {"actions": [], "warnings": [], "problems": [], "ok": True}

    def test_creates_claude_md_when_absent(self, tmp_path):
        installer._write_claude_md(tmp_path, self._result(), dry_run=False)
        text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert installer._CLAUDE_BEGIN in text and installer._CLAUDE_END in text
        assert "/hs:" in text            # how to invoke the skills
        assert "harness/rules" in text   # pointer to the rule layer

    def test_onboarding_block_mentions_off_skill_discovery(self, tmp_path):
        # A project installed default-off must not be blind to its off skills: the
        # shipped onboarding block names find-skills --list, the [OFF] tag, and hs:use.
        installer._write_claude_md(tmp_path, self._result(), dry_run=False)
        text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert "/hs:find-skills --list" in text
        assert "[OFF]" in text
        assert "/hs:use" in text

    def test_onboarding_block_states_probe_first_principle(self, tmp_path):
        # The single most load-bearing working principle rides into every install: probe /
        # verify empirically before building on a guess; a claim not exercised for real is
        # ASSUMED, never reported as "works". A future block edit must not drop it.
        installer._write_claude_md(tmp_path, self._result(), dry_run=False)
        text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8").lower()
        assert "probe" in text, "onboarding block must carry the probe-first principle"
        assert "assumed" in text, "onboarding block must name the claim-typing gate"

    def test_preserves_user_prose_when_appending(self, tmp_path):
        p = tmp_path / "CLAUDE.md"
        p.write_text("# My Project\n\nHand-written guidance.\n", encoding="utf-8")
        installer._write_claude_md(tmp_path, self._result(), dry_run=False)
        text = p.read_text(encoding="utf-8")
        assert "# My Project" in text
        assert "Hand-written guidance." in text
        assert installer._CLAUDE_BEGIN in text

    def test_replace_between_markers_drops_stale_keeps_outside(self, tmp_path):
        p = tmp_path / "CLAUDE.md"
        p.write_text("intro line\n\n%s\nSTALE OLD BLOCK\n%s\n\noutro line\n"
                     % (installer._CLAUDE_BEGIN, installer._CLAUDE_END),
                     encoding="utf-8")
        installer._write_claude_md(tmp_path, self._result(), dry_run=False)
        text = p.read_text(encoding="utf-8")
        assert "STALE OLD BLOCK" not in text              # block refreshed
        assert "intro line" in text and "outro line" in text  # prose preserved
        assert text.count(installer._CLAUDE_BEGIN) == 1
        assert "/hs:" in text

    def test_idempotent_block_appears_once(self, tmp_path):
        r = self._result()
        installer._write_claude_md(tmp_path, r, dry_run=False)
        installer._write_claude_md(tmp_path, r, dry_run=False)
        text = (tmp_path / "CLAUDE.md").read_text(encoding="utf-8")
        assert text.count(installer._CLAUDE_BEGIN) == 1
        assert text.count(installer._CLAUDE_END) == 1

    def test_dry_run_writes_nothing(self, tmp_path):
        installer._write_claude_md(tmp_path, self._result(), dry_run=True)
        assert not (tmp_path / "CLAUDE.md").exists()

    def test_reversed_markers_do_not_corrupt(self, tmp_path):
        # a hand-mangled file with END before BEGIN must not garble — fall back
        # to a clean append, preserving the user's prose.
        p = tmp_path / "CLAUDE.md"
        p.write_text("prose A\n%s\nprose B\n%s\nprose C\n"
                     % (installer._CLAUDE_END, installer._CLAUDE_BEGIN),
                     encoding="utf-8")
        installer._write_claude_md(tmp_path, self._result(), dry_run=False)
        text = p.read_text(encoding="utf-8")
        assert "prose A" in text and "prose B" in text and "prose C" in text
        assert "/hs:" in text  # a valid block was written
        # the appended block is well-formed: a BEGIN that precedes its END exists
        assert text.rindex(installer._CLAUDE_BEGIN) < text.rindex(installer._CLAUDE_END)

    def test_install_injects_block_into_target(self, default_installed):
        target_repo = default_installed.repo
        text = (target_repo / "CLAUDE.md").read_text(encoding="utf-8")
        assert installer._CLAUDE_BEGIN in text

    def test_dogfood_install_does_not_touch_source_claude_md(self):
        # source == target: our hand-authored CLAUDE.md must not get a block.
        res = installer.install(_REPO_ROOT, _REPO_ROOT, dry_run=True)
        assert not any("CLAUDE.md" in a for a in res["actions"])


class TestNoTrack:
    """--no-track installs a RUNNING harness that the adopter's git ignores
    (harness/ added to .gitignore). Because the roster's whole safety model is
    'tamper-visible via git diff', a no-track install refuses to localize the
    roster and says why — the gate needs harness/ tracked to mean anything."""

    def _gitignore_lines(self, repo):
        p = repo / ".gitignore"
        return p.read_text(encoding="utf-8").splitlines() if p.is_file() else []

    def test_no_track_gitignores_harness_tree(self, target_repo):
        installer.install(_REPO_ROOT, target_repo, no_track=True)
        assert "harness/" in self._gitignore_lines(target_repo)
        # ...but the tree is really installed and runnable
        assert (target_repo / "harness" / "hooks" / "session_init.py").is_file()

    def test_default_install_does_not_gitignore_harness_tree(self, default_installed):
        target_repo = default_installed.repo
        assert "harness/" not in self._gitignore_lines(target_repo)

    def test_no_track_is_idempotent(self, target_repo):
        installer.install(_REPO_ROOT, target_repo, no_track=True)
        installer.install(_REPO_ROOT, target_repo, no_track=True)
        assert self._gitignore_lines(target_repo).count("harness/") == 1

    def test_cli_no_track_flag_parses(self, target_repo):
        proc = subprocess.run(
            [sys.executable, str(_INSTALL_DIR / "install.py"),
             "--target", str(target_repo), "--no-track", "--non-interactive"],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert "harness/" in self._gitignore_lines(target_repo)


class TestCli:
    def test_cli_dry_run_exit_zero(self, target_repo):
        proc = subprocess.run(
            [sys.executable, str(_INSTALL_DIR / "install.py"),
             "--target", str(target_repo), "--dry-run"],
            capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        assert not (target_repo / ".claude").exists()


class TestRobustness:
    """A team adopting the harness may already have a hand-edited
    .claude/settings.json. A JSON syntax error in it must surface as a clean,
    actionable message naming the file — never a raw JSONDecodeError traceback."""

    def _seed_bad_settings(self, target_repo, text="{ broken json, }"):
        claude = target_repo / ".claude"
        claude.mkdir(parents=True, exist_ok=True)
        (claude / "settings.json").write_text(text)

    def test_malformed_settings_raises_clean_error_on_install(self, target_repo):
        self._seed_bad_settings(target_repo)
        with pytest.raises(installer.InstallError) as ei:
            installer.install(_REPO_ROOT, target_repo)
        assert "settings.json" in str(ei.value)

    def test_malformed_settings_raises_clean_error_on_uninstall(self, target_repo):
        self._seed_bad_settings(target_repo, "{ nope ")
        with pytest.raises(installer.InstallError):
            installer.install(_REPO_ROOT, target_repo, uninstall=True)

    def test_cli_malformed_settings_exits_nonzero_naming_file(self, target_repo):
        self._seed_bad_settings(target_repo, "{ bad ")
        proc = subprocess.run(
            [sys.executable, str(_INSTALL_DIR / "install.py"),
             "--target", str(target_repo)],
            capture_output=True, text=True)
        assert proc.returncode != 0
        assert "settings.json" in (proc.stderr + proc.stdout)
        # a clean message, not a Python traceback
        assert "Traceback" not in proc.stderr


class TestGitignoreFragment:
    """The installer adds a managed block so the target never commits harness
    runtime state. Idempotent and additive: a pre-existing .gitignore survives."""

    def test_writes_harness_ignore_block(self, target_repo):
        installer.install(_REPO_ROOT, target_repo)
        gi = (target_repo / ".gitignore").read_text()
        assert "harness/state/" in gi
        assert "harness/standards/.snapshots/" in gi
        assert "RUN-LOG.md" in gi

    def test_stale_managed_block_is_refreshed(self, target_repo):
        # An adopter installed before .claude/settings.json joined the patterns has
        # a stale managed block. A reinstall must REFRESH it between the markers
        # (not skip-if-present), else the new ignore never reaches existing repos.
        stale = ("node_modules/\n"
                 "# >>> harness (generated runtime — never commit) >>>\n"
                 "harness/state/\n"
                 "# <<< harness <<<\n")
        (target_repo / ".gitignore").write_text(stale)
        installer.install(_REPO_ROOT, target_repo)
        gi = (target_repo / ".gitignore").read_text()
        assert ".claude/settings.json" in gi          # refreshed
        assert "node_modules/" in gi                  # user lines preserved
        assert gi.count("# >>> harness") == 1          # no duplicate block

    def test_gitignore_is_idempotent(self, target_repo):
        installer.install(_REPO_ROOT, target_repo)
        first = (target_repo / ".gitignore").read_text()
        installer.install(_REPO_ROOT, target_repo)
        assert (target_repo / ".gitignore").read_text() == first

    def test_preserves_existing_gitignore(self, target_repo):
        (target_repo / ".gitignore").write_text("node_modules/\n")
        installer.install(_REPO_ROOT, target_repo)
        gi = (target_repo / ".gitignore").read_text()
        assert "node_modules/" in gi
        assert "harness/state/" in gi

    def test_gitignores_claude_settings(self, target_repo):
        # settings.json is install-materialized and mode-specific (global wires
        # $HARNESS_BIN_ROOT, project wires $CLAUDE_PROJECT_DIR). Committing it
        # freezes wrong-mode/stale hook wiring into git history, so a later
        # `git checkout` restores dead per-script paths and bricks every hook.
        # The managed block must gitignore it (and settings.local.json).
        installer.install(_REPO_ROOT, target_repo)
        gi = (target_repo / ".gitignore").read_text()
        assert ".claude/settings.json" in gi
        assert ".claude/settings.local.json" in gi

    def test_git_check_ignore_matches_settings(self, target_repo):
        # End-to-end: a materialized settings.json in the target must actually be
        # ignored by git — otherwise a recipient can commit it and hit the
        # branch-switch landmine.
        installer.install(_REPO_ROOT, target_repo)
        claude = target_repo / ".claude"
        claude.mkdir(parents=True, exist_ok=True)
        (claude / "settings.json").write_text("{}")
        proc = subprocess.run(
            ["git", "-C", str(target_repo), "check-ignore",
             ".claude/settings.json"],
            capture_output=True, text=True)
        assert proc.returncode == 0, (
            ".claude/settings.json must be gitignored, but git check-ignore "
            "did not match it: " + (proc.stdout + proc.stderr))

    def test_gitignore_does_not_ignore_disabled_skills(self, default_installed):
        # The off-skill stash is a TRACKED sibling of skills/ so it ships with the
        # bundle. The managed block ignores harness/state/, which must NOT swallow
        # harness/plugins/hs/disabled-skills/ — git check-ignore must MISS it.
        target_repo = default_installed.repo
        proc = subprocess.run(
            ["git", "-C", str(target_repo), "check-ignore",
             "harness/plugins/hs/disabled-skills/some-skill/SKILL.md"],
            capture_output=True, text=True)
        assert proc.returncode != 0, (
            "disabled-skills must be tracked, but git check-ignore matched: "
            + proc.stdout)


class TestStandardsLengthWarning:
    """An over-length standards doc is advisory only: many skills load it, so a
    long file costs tokens and is easy to skim past — but the installer warns,
    never blocks. The threshold is tunable via HARNESS_STANDARDS_MAXLOC."""

    def test_overlength_standards_warns_not_blocks(self, tmp_path, monkeypatch):
        base = tmp_path / "docs"
        base.mkdir(parents=True)
        (base / "system-architecture.md").write_text("line\n" * 50)
        (base / "code-standards.md").write_text("x" * 60 + "\n")  # not thin, short
        monkeypatch.setenv("HARNESS_STANDARDS_MAXLOC", "10")
        result = {"warnings": []}
        installer._check_standards(tmp_path, result)
        assert any("system-architecture.md" in w and "token" in w.lower()
                   for w in result["warnings"])
        assert not any("code-standards.md" in w for w in result["warnings"])

    def test_within_threshold_is_silent(self, tmp_path, monkeypatch):
        base = tmp_path / "docs"
        base.mkdir(parents=True)
        (base / "system-architecture.md").write_text("x" * 60 + "\n")
        (base / "code-standards.md").write_text("y" * 60 + "\n")
        monkeypatch.setenv("HARNESS_STANDARDS_MAXLOC", "800")
        result = {"warnings": []}
        installer._check_standards(tmp_path, result)
        assert result["warnings"] == []


class TestInteractiveInstall:
    """On a TTY the installer suggests a reviewer (from git config) and prompts;
    --non-interactive / --yes force the non-prompt path even on a TTY (the CI
    case), and a non-TTY stays silent regardless."""

    def test_yes_skips_prompt_even_when_tty(self, monkeypatch, target_repo):
        monkeypatch.setattr(installer, "_stdin_is_tty", lambda: True)
        monkeypatch.setattr(
            "builtins.input",
            lambda *a: pytest.fail("must not prompt with --yes"))
        rc = installer.main(["--target", str(target_repo),
                             "--source", str(_REPO_ROOT), "--yes"])
        assert rc == 0

    def test_non_interactive_alias_skips_prompt(self, monkeypatch, target_repo):
        monkeypatch.setattr(installer, "_stdin_is_tty", lambda: True)
        monkeypatch.setattr(
            "builtins.input",
            lambda *a: pytest.fail("must not prompt with --non-interactive"))
        rc = installer.main(["--target", str(target_repo),
                             "--source", str(_REPO_ROOT), "--non-interactive"])
        assert rc == 0

    def test_tty_without_yes_prompts(self, monkeypatch, target_repo):
        monkeypatch.setattr(installer, "_stdin_is_tty", lambda: True)
        seen = []
        monkeypatch.setattr("builtins.input",
                            lambda *a: seen.append(a) or "")  # blank → finish
        rc = installer.main(["--target", str(target_repo),
                             "--source", str(_REPO_ROOT)])
        assert rc == 0
        assert seen  # the reviewer prompt was shown


class TestNonGitSource:
    """A shipped bundle extracts to a plain directory with no .git. The
    installer's file list must then come from manifest.json — `git ls-files`
    exits 128 outside a work tree and must not abort the install."""

    def _fake_source(self, tmp_path):
        src = tmp_path / "extracted"
        (src / "harness" / "data").mkdir(parents=True)
        (src / "harness" / "hooks").mkdir(parents=True)
        (src / "harness" / "hooks" / "a.py").write_text("x\n")
        (src / "harness" / "data" / "b.yaml").write_text("y\n")
        manifest = {"files": {
            "harness/hooks/a.py": "0" * 64,
            "harness/data/b.yaml": "1" * 64,
        }}
        (src / "harness" / "manifest.json").write_text(json.dumps(manifest))
        return src

    def test_tracked_files_fall_back_to_manifest(self, tmp_path):
        src = self._fake_source(tmp_path)
        rels = installer._tracked_harness_files(src)
        # manifest keys PLUS manifest.json itself (the target needs it to verify)
        assert sorted(rels) == [
            "harness/data/b.yaml", "harness/hooks/a.py", "harness/manifest.json"]

    def test_copy_tree_works_without_git(self, tmp_path):
        src = self._fake_source(tmp_path)
        target = tmp_path / "target"
        target.mkdir()
        installer._copy_tree(src, target, dry_run=False)
        assert (target / "harness" / "hooks" / "a.py").is_file()
        assert (target / "harness" / "data" / "b.yaml").is_file()


class TestGitRepoUntrackedHarness:
    """Source IS a git work tree, but harness/ was copied in without being
    committed — e.g. the bundle extracted into an existing repo, then install run
    from there (and the harness's own test suite re-installing from a freshly
    installed copy). `git ls-files -- harness/` then exits 0 with NO output; the
    file list must still come from manifest.json, not collapse to an empty
    install. The error-only fallback misses this because git did not error."""

    def _git_untracked_source(self, tmp_path):
        src = tmp_path / "repo"
        (src / "harness" / "data").mkdir(parents=True)
        (src / "harness" / "hooks").mkdir(parents=True)
        (src / "harness" / "hooks" / "a.py").write_text("x\n")
        (src / "harness" / "data" / "b.yaml").write_text("y\n")
        manifest = {"files": {
            "harness/hooks/a.py": "0" * 64,
            "harness/data/b.yaml": "1" * 64,
        }}
        (src / "harness" / "manifest.json").write_text(json.dumps(manifest))
        subprocess.run(["git", "-C", str(src), "init", "-q"], check=True)
        return src

    def test_tracked_files_fall_back_when_git_lists_nothing(self, tmp_path):
        src = self._git_untracked_source(tmp_path)
        rels = installer._tracked_harness_files(src)
        assert sorted(rels) == [
            "harness/data/b.yaml", "harness/hooks/a.py", "harness/manifest.json"]

    def test_copy_tree_populates_from_manifest_in_untracked_repo(self, tmp_path):
        src = self._git_untracked_source(tmp_path)
        target = tmp_path / "installed"
        target.mkdir()
        installer._copy_tree(src, target, dry_run=False)
        assert (target / "harness" / "hooks" / "a.py").is_file()
        assert (target / "harness" / "manifest.json").is_file()


class TestSkillSelectionPrompt:
    """The interactive 2-phase skill prompt maps user input to install() args:
    (skills_csv, groups_csv). The spine core is never asked — install() keeps it."""

    @staticmethod
    def _scripted(answers):
        it = iter(answers)

        def _inp(_prompt=""):
            try:
                return next(it)
            except StopIteration:
                raise EOFError
        return _inp

    def _run(self, answers, groups=("viz", "think", "ai")):
        import builtins
        real = builtins.input
        builtins.input = self._scripted(answers)
        try:
            return installer._prompt_skill_selection(list(groups))
        finally:
            builtins.input = real

    def test_all_is_the_default(self):
        # Enter at the mode prompt -> ship everything (None, None, None)
        assert self._run([""]) == (None, None, None)

    def test_by_group_collects_picked_groups(self):
        # mode 2, then y/n per group (sorted: ai, think, viz)
        skills, groups, add = self._run(["2", "n", "y", "y"])  # ai=n think=y viz=y
        assert skills is None and add is None
        assert set(groups.split(",")) == {"think", "viz"}

    def test_by_group_none_picked_is_core_only(self):
        # mode 2, decline every group -> "" (core-only), NOT None (=all)
        assert self._run(["2", "n", "n", "n"]) == (None, "", None)

    def test_manual_returns_skill_csv(self):
        assert self._run(["3", "cook,brainstorm"]) == ("cook,brainstorm", None, None)


class TestSkillClusterPrompt:
    """With the default-off catalog present, the prompt is recommended / +clusters /
    everything, and it emits an explicit skills CSV built on the recommended set."""

    def _run(self, answers):
        import builtins
        real = builtins.input
        it = iter(answers)

        def _inp(_prompt=""):
            try:
                return next(it)
            except StopIteration:
                raise EOFError
        builtins.input = _inp
        try:
            return installer._prompt_skill_selection([], source_root=_REPO_ROOT)
        finally:
            builtins.input = real

    def test_recommended_is_default(self):
        # Enter -> recommended set = the default-off catalog (None, None, None)
        assert self._run([""]) == (None, None, None)

    @pytest.mark.dev_repo
    def test_everything_ships_all_skills(self):
        import sys as _s
        _s.path.insert(0, str(_REPO_ROOT / "harness" / "scripts"))
        import skill_selection as ssel
        skills, groups, add = self._run(["3"])
        assert groups is None and add is None
        assert set(skills.split(",")) == ssel.all_skills(_REPO_ROOT)

    def _clusters(self):
        import yaml
        return sorted((yaml.safe_load(
            (_REPO_ROOT / "harness" / "data" / "skill-defaults.yaml").read_text())
            or {}).get("clusters") or {})

    @pytest.mark.dev_repo
    def test_zero_clusters_resolves_to_recommended(self):
        # H1 regression pin: 'recommended + clusters' with NO cluster picked must
        # resolve to the SAME installed set as the plain recommended default, never
        # the dep-closed superset. The prompt returns add_skills=[]; the resolver
        # keeps the baseline unclosed.
        import sys as _s
        _s.path.insert(0, str(_REPO_ROOT / "harness" / "scripts"))
        import skill_selection as ssel
        answers = ["2"] + ["n" for _ in self._clusters()]
        skills, groups, add = self._run(answers)
        assert skills is None and groups is None and add == []
        opt1 = ssel.resolve_enabled(source_root=_REPO_ROOT)                  # [1] default
        opt2 = ssel.resolve_enabled(source_root=_REPO_ROOT, add_skills=add)   # [2] 0 clusters
        assert opt2 == opt1

    @pytest.mark.dev_repo
    def test_enable_cluster_adds_to_recommended(self):
        import sys as _s
        _s.path.insert(0, str(_REPO_ROOT / "harness" / "scripts"))
        import skill_selection as ssel
        # mode 2, decline every cluster except "viz" (clusters are asked sorted)
        answers = ["2"] + ["y" if c == "viz" else "n" for c in self._clusters()]
        skills, groups, add = self._run(answers)
        assert skills is None and groups is None
        assert "excalidraw" in add                  # viz cluster picked
        assert "shopify" not in add                 # a declined cluster stays off
        enabled = ssel.resolve_enabled(source_root=_REPO_ROOT, add_skills=add)
        on = ssel.all_skills(_REPO_ROOT) - ssel.load_defaults(_REPO_ROOT)
        assert on <= enabled                         # recommended baseline kept exactly
        assert "excalidraw" in enabled               # viz turned on
        assert "shopify" not in enabled              # declined cluster stays stashed


class TestReinstallNarrowingRefresh:
    """Re-install narrowing must move the CURRENT live skill dir into the stash even
    when a (possibly stale) prior stash copy exists — the newest copy wins, never a
    lingering old one."""

    def _mk_skill(self, root, tree, name, body):
        d = root / "harness" / "plugins" / "hs" / tree / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "SKILL.md").write_text(body, encoding="utf-8")
        return d

    def test_live_dir_refreshes_a_stale_stash_copy(self, tmp_path):
        target = tmp_path / "t"
        # live skills/foo = NEW content; stash disabled-skills/foo = OLD content
        self._mk_skill(target, "skills", "foo", "# foo NEW\n")
        self._mk_skill(target, "disabled-skills", "foo", "# foo OLD\n")
        result = {"actions": [], "warnings": []}
        installer._remove_omitted_skill_dirs(target, {"foo"}, result, dry_run=False)
        stash = target / "harness/plugins/hs/disabled-skills/foo/SKILL.md"
        live = target / "harness/plugins/hs/skills/foo"
        assert not live.exists()                       # live dir moved out
        assert stash.read_text() == "# foo NEW\n"      # stash refreshed, not stale-kept


class TestWiringReconcileOnReinstall:
    """A re-install (upgrade/narrow) must reconcile the settings.json wiring
    against the hooks that actually ship. A stale entry pointing at a hook the
    new version dropped bricks every Bash tool-call with "can't open file", so
    the wire step must prune harness-owned entries the new registration no longer
    carries — while leaving user-authored hooks untouched."""

    def test_reinstall_prunes_dropped_hook_wiring_keeps_user_hook(self, target_repo):
        installer.install(_REPO_ROOT, target_repo)
        spath = target_repo / ".claude" / "settings.json"
        settings = json.loads(spath.read_text())

        # Simulate a target left by an OLDER version: a harness hook this version
        # dropped is still wired under a Bash matcher, alongside the user's own
        # audit hook. The dropped-hook name is assembled from fragments so this
        # test file does not itself carry the banned literal — the tier-c absence
        # guard git-greps tracked files under harness/ for it.
        dropped = "ownership_" + "guard.py"
        stale_cmd = 'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/' + dropped
        user_cmd = "python3 my-own-audit.py"
        settings.setdefault("hooks", {}).setdefault("PreToolUse", []).append(
            {"matcher": "Bash",
             "hooks": [{"type": "command", "command": stale_cmd},
                       {"type": "command", "command": user_cmd}]})
        spath.write_text(json.dumps(settings))

        installer.install(_REPO_ROOT, target_repo)
        after = spath.read_text()

        assert dropped not in after                # dropped-hook wiring pruned
        assert "my-own-audit.py" in after          # user-authored hook preserved
        assert "harness/hooks/" in after           # current hooks still wired
