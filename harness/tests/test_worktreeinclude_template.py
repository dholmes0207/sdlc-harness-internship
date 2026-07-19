"""test_worktreeinclude_template.py — the .worktreeinclude enumeration is
complete and end-user-safe (F1 close).

Claude Code copies files matching `.worktreeinclude` (gitignore syntax) into
every worktree it creates. The template POSITIVELY enumerates the harness runtime
subtree + exactly `.claude/settings.json` — a probe proved negation `!dir/**`
does NOT exclude, so a forgotten subdir/file means a PARTIAL carry = the F1
hard-block. The forgotten-entry guard turns red on a new top-level dir OR file
(R5); a WorktreeCreate hook in registration would silently disable the native
copy, so a tripwire fails if one appears.
"""
import re
from pathlib import Path

_HARNESS = Path(__file__).resolve().parent.parent
_TEMPLATE = _HARNESS / "install" / "worktreeinclude.template"
_EXCLUDE = {"state", "tests", "e2e"}  # runtime state (host); suite runs at host


def _template_lines():
    return [l.strip() for l in _TEMPLATE.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


class TestForgottenEntry:
    def test_every_top_level_dir_and_file_enumerated(self):
        # R5: enumerate the REAL harness/ top level — dirs AND files — minus the
        # exclusion set, and require a matching template line for each.
        lines = set(_template_lines())
        missing = []
        for entry in sorted(_HARNESS.iterdir()):
            if entry.name in _EXCLUDE or entry.name.startswith("."):
                continue
            if entry.is_dir():
                want = "harness/%s/**" % entry.name
            else:
                want = "harness/%s" % entry.name
            if want not in lines:
                missing.append(want)
        assert not missing, "template missing (add or justify in _EXCLUDE): %r" % missing

    def test_excluded_dirs_not_enumerated(self):
        lines = _template_lines()
        for name in _EXCLUDE:
            assert not any(l.startswith("harness/%s/" % name) or l == "harness/%s/**" % name
                           for l in lines), "%s must NOT be carried (R6/state)" % name


class TestEndUserSafe:
    def test_no_harness_dev(self):
        assert all(".harness-dev" not in l for l in _template_lines()), \
            "shipped template must not carry the dev-only .harness-dev"

    def test_only_settings_json_not_whole_claude(self):
        lines = _template_lines()
        assert ".claude/settings.json" in lines
        assert ".claude/**" not in lines and ".claude/" not in lines, \
            "whole .claude/ is a recursion risk — carry only settings.json"

    def test_no_tests_or_e2e(self):
        # R6: the ~19M suite is not needed to RUN the harness in a worktree.
        lines = _template_lines()
        assert not any("harness/tests" in l or "harness/e2e" in l for l in lines)


class TestWorktreeCreateTripwire:
    def test_no_worktreecreate_hook_in_registration(self):
        # A WorktreeCreate hook would take over worktree creation and silently
        # bypass the native .worktreeinclude copy.
        reg = _HARNESS / "data" / "hooks-registration.yaml"
        if reg.is_file():
            assert "WorktreeCreate" not in reg.read_text(encoding="utf-8"), \
                "a WorktreeCreate hook disables .worktreeinclude — tripwire"
        settings = _HARNESS.parent / ".claude" / "settings.json"
        if settings.is_file():
            assert "WorktreeCreate" not in settings.read_text(encoding="utf-8"), \
                "a WorktreeCreate hook in shipped settings disables .worktreeinclude"


class TestDogfoodRootFile:
    def test_dogfood_superset_plus_harness_dev(self):
        # D4: the committed dogfood .worktreeinclude ⊇ template + .harness-dev/**.
        root_file = _HARNESS.parent / ".worktreeinclude"
        if not root_file.is_file():
            import pytest
            pytest.skip("no dogfood root .worktreeinclude")
        root = set(l.strip() for l in root_file.read_text(encoding="utf-8").splitlines()
                   if l.strip() and not l.strip().startswith("#"))
        for line in _template_lines():
            assert line in root, "dogfood file missing template line: %s" % line
        assert ".harness-dev/**" in root
