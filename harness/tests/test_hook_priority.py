"""test_hook_priority.py — Phase 4: declarative hook priority ordering.

Tests that materialize_hooks stable-sorts entries by (priority, file-order)
before grouping, so safety/compliance hooks come before telemetry, telemetry
before nudge — independent of YAML line order.
"""
from pathlib import Path
import sys


_INSTALL = Path(__file__).resolve().parent.parent / "install"
sys.path.insert(0, str(_INSTALL.parent))
sys.path.insert(0, str(_INSTALL))

from install import materialize_hooks  # noqa: E402


def _cmd(name):
    return "$HARNESS_PY $HARNESS_ROOT/harness/hooks/%s.py" % name


class TestPriorityOrdering:
    def test_materialize_orders_safety_before_telemetry(self):
        """In the PreToolUse·Bash group, compliance hooks must come before telemetry."""
        reg = {
            "hooks": [
                # Declared telemetry BEFORE compliance — sort must fix this
                {"event": "PreToolUse", "matcher": "Bash",
                 "command": _cmd("mark_bash_start"), "class": "telemetry"},
                {"event": "PreToolUse", "matcher": "Bash",
                 "command": _cmd("bash_safety_guard"), "class": "compliance"},
                {"event": "PreToolUse", "matcher": "Bash",
                 "command": _cmd("secret_scan_before_ship"), "class": "compliance"},
            ]
        }
        hooks, _ = materialize_hooks(reg, py="python3")
        bash_group = hooks["PreToolUse"][0]
        cmds = [h["command"] for h in bash_group["hooks"]]

        def idx(name):
            for i, c in enumerate(cmds):
                if name in c:
                    return i
            raise AssertionError("%s not found in %s" % (name, cmds))

        assert idx("bash_safety_guard") < idx("mark_bash_start"), \
            "bash_safety_guard (compliance) must precede mark_bash_start (telemetry)"
        assert idx("secret_scan_before_ship") < idx("mark_bash_start"), \
            "secret_scan_before_ship (compliance) must precede mark_bash_start (telemetry)"

    def test_explicit_priority_overrides_class_default(self):
        """An entry with explicit priority:5 (nudge class) sorts before class-default telemetry (50)."""
        reg = {
            "hooks": [
                {"event": "Stop", "command": _cmd("telemetry_hook"), "class": "telemetry"},
                {"event": "Stop", "command": _cmd("high_prio_nudge"), "class": "nudge",
                 "priority": 5},
            ]
        }
        hooks, _ = materialize_hooks(reg, py="python3")
        stop_cmds = [h["command"] for h in hooks["Stop"][0]["hooks"]]
        nudge_idx = next(i for i, c in enumerate(stop_cmds) if "high_prio_nudge" in c)
        telem_idx = next(i for i, c in enumerate(stop_cmds) if "telemetry_hook" in c)
        assert nudge_idx < telem_idx, \
            "explicit priority 5 nudge must precede class-default telemetry (50)"

    def test_stable_sort_preserves_file_order_within_priority(self):
        """Entries with the same effective priority retain their declaration order."""
        reg = {
            "hooks": [
                {"event": "Stop", "command": _cmd("hook_a"), "class": "compliance"},
                {"event": "Stop", "command": _cmd("hook_b"), "class": "compliance"},
                {"event": "Stop", "command": _cmd("hook_c"), "class": "compliance"},
            ]
        }
        hooks, _ = materialize_hooks(reg, py="python3")
        cmds = [h["command"] for h in hooks["Stop"][0]["hooks"]]
        names = [next(n for n in ("hook_a", "hook_b", "hook_c") if n in c) for c in cmds]
        assert names == ["hook_a", "hook_b", "hook_c"], \
            "same-priority entries must keep file declaration order"

    def test_no_priority_field_still_materializes(self):
        """Registration entries without a priority field must still materialize correctly."""
        reg = {
            "hooks": [
                {"event": "SessionStart",
                 "command": _cmd("session_init")},
            ]
        }
        hooks, skipped = materialize_hooks(reg, py="python3")
        assert "SessionStart" in hooks
        assert not skipped
        cmds = [h["command"] for h in hooks["SessionStart"][0]["hooks"]]
        assert any("session_init" in c for c in cmds)
