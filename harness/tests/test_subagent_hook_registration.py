"""test_subagent_hook_registration.py — the SubagentStart hook must live under
the `hooks:` section so the installer actually wires it.

`materialize_hooks` reads only `registration['hooks']`. If the SubagentStart
entry sits under `git_hooks:` it parses fine but is never materialized into the
target's settings.json, so subagent_init.py never fires at spawn. These guard
that the entry sits in the section the installer reads and carries the fields it
needs to wire (event + command).
"""
from pathlib import Path

import yaml

_REPO_ROOT = Path(__file__).resolve().parents[2]
_REGISTRATION = (_REPO_ROOT / "harness" / "install"
                 / "hooks-registration.yaml")


def _load():
    return yaml.safe_load(_REGISTRATION.read_text(encoding="utf-8")) or {}


def _subagent_start_entries(section):
    return [e for e in (section or [])
            if isinstance(e, dict) and e.get("event") == "SubagentStart"]


def test_subagent_start_entry_lives_under_hooks():
    reg = _load()
    assert _subagent_start_entries(reg.get("hooks")), (
        "SubagentStart entry must be under hooks: so materialize_hooks wires it")


def test_subagent_start_entry_not_under_git_hooks():
    reg = _load()
    assert not _subagent_start_entries(reg.get("git_hooks")), (
        "SubagentStart belongs to the materialized hooks, not git_hooks")


def test_subagent_start_entry_carries_wiring_fields():
    reg = _load()
    entries = _subagent_start_entries(reg.get("hooks"))
    assert len(entries) >= 1, "at least one SubagentStart entry required"
    commands = [e.get("command", "") for e in entries]
    assert all("$HARNESS_ROOT" in cmd for cmd in commands), (
        "all SubagentStart commands must use $HARNESS_ROOT")
    # SubagentStart is now wired via the in-process dispatcher, which runs subagent_init
    # as a core (registered in hook-dispatch.yaml) — so subagent_init still fires at spawn.
    assert any("hook_dispatch.py" in cmd or "subagent_init.py" in cmd for cmd in commands)
    disp = yaml.safe_load(
        (_REPO_ROOT / "harness" / "data" / "hook-dispatch.yaml").read_text(encoding="utf-8"))
    ss_mods = {c.get("module") for c in disp["groups"].get("SubagentStart", [])}
    assert "subagent_init" in ss_mods, "subagent_init must be a SubagentStart dispatch core"
