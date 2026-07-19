"""test_hook_dispatch_registry.py — the dispatch registry parser + resolution.

The registry (hook-dispatch.yaml) is the single source of which cores a group runs,
in what order, under what class. These tests pin its parse (group key -> (event,
matcher), core-spec shape, argv-variant same-module-twice), the module-HOOK_CLASS
override of the registry class (config cannot reclassify a hook), and the
parsed-empty-vs-unparseable distinction (a no-op group continues; a broken registry
fails closed).
"""
import sys
import textwrap
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hook_dispatch  # noqa: E402


def _write(tmp_path, text):
    p = tmp_path / "reg.yaml"
    p.write_text(textwrap.dedent(text), encoding="utf-8")
    return p


class TestParse:
    def test_group_key_splits_event_matcher(self, tmp_path):
        p = _write(tmp_path, """
            groups:
              "PreToolUse:Bash":
                - {name: g, module: gate_stage, entry: core, class: compliance}
        """)
        reg = hook_dispatch.load_registry(p)
        assert ("PreToolUse", "Bash") in reg
        spec = reg[("PreToolUse", "Bash")][0]
        assert spec == {"name": "g", "module": "gate_stage", "entry": "core",
                        "class": "compliance", "kind": None, "fail_open": False,
                        "timeout": None}

    def test_matcherless_key_defaults_star(self, tmp_path):
        p = _write(tmp_path, """
            groups:
              "Stop":
                - {module: memory_gap_hook, class: telemetry}
        """)
        reg = hook_dispatch.load_registry(p)
        assert ("Stop", "*") in reg
        # entry defaults to core, name defaults to module
        s = reg[("Stop", "*")][0]
        assert s["entry"] == "core" and s["name"] == "memory_gap_hook"

    def test_argv_variant_same_module_twice(self, tmp_path):
        # the same module wired under two entries/events -> two distinct specs
        p = _write(tmp_path, """
            groups:
              "Stop":
                - {name: memgap_stop, module: memory_gap_hook, entry: core, class: telemetry}
              "PostToolUse:Write":
                - {name: memgap_post, module: memory_gap_hook, entry: post_tool_use, class: telemetry}
        """)
        reg = hook_dispatch.load_registry(p)
        assert reg[("Stop", "*")][0]["entry"] == "core"
        assert reg[("PostToolUse", "Write")][0]["entry"] == "post_tool_use"
        assert reg[("Stop", "*")][0]["name"] != reg[("PostToolUse", "Write")][0]["name"]

    def test_bad_core_rows_skipped(self, tmp_path):
        p = _write(tmp_path, """
            groups:
              "PreToolUse:Bash":
                - "not a dict"
                - {no_module: true}
                - {module: gate_stage, class: compliance}
        """)
        reg = hook_dispatch.load_registry(p)
        specs = reg[("PreToolUse", "Bash")]
        assert len(specs) == 1 and specs[0]["module"] == "gate_stage"

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(Exception):
            hook_dispatch.load_registry(tmp_path / "absent.yaml")


class TestClassOverride:
    def test_module_hook_class_wins_over_registry(self, tmp_path, monkeypatch):
        # a fixture module WITH a HOOK_CLASS constant; the registry lies and says
        # telemetry, but _resolve_core must report the module's constant.
        fixdir = tmp_path / "fix"
        fixdir.mkdir()
        (fixdir / "clsmod.py").write_text(
            "HOOK_CLASS = 'compliance'\ndef core(data): return None\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(fixdir))
        fn, cls = hook_dispatch._resolve_core(
            {"module": "clsmod", "entry": "core", "class": "telemetry", "name": "x"})
        assert cls == "compliance"  # module constant wins, not the registry class
        assert callable(fn)

    def test_missing_entry_returns_none(self, tmp_path, monkeypatch):
        fixdir = tmp_path / "fix2"
        fixdir.mkdir()
        (fixdir / "noentry.py").write_text("def other(d): return None\n", encoding="utf-8")
        monkeypatch.syspath_prepend(str(fixdir))
        fn, cls = hook_dispatch._resolve_core(
            {"module": "noentry", "entry": "core", "class": "telemetry", "name": "x"})
        assert fn is None


class TestShippedRegistry:
    def test_all_entries_resolve_to_callables(self):
        # the real hook-dispatch.yaml must resolve every entry to a callable, and
        # the module's HOOK_CLASS constant must match the registry class (a drift
        # here means a hook was reclassified or an entry renamed).
        shipped = Path(hook_dispatch.__file__).resolve().parent.parent / "data" / "hook-dispatch.yaml"
        reg = hook_dispatch.load_registry(shipped)
        assert reg, "the shipped registry must not be empty"
        for (event, matcher), specs in reg.items():
            for spec in specs:
                fn, cls = hook_dispatch._resolve_core(spec)
                assert callable(fn), "%s.%s does not resolve" % (spec["module"], spec["entry"])
                assert cls == spec["class"], \
                    "%s: module HOOK_CLASS %r != registry class %r" % (spec["name"], cls, spec["class"])


class TestEmptyVsBroken:
    def test_empty_group_continues(self, tmp_path, monkeypatch):
        # a parsed-but-empty registry -> no-op continue (exit 0), NOT a block
        p = _write(tmp_path, "groups: {}\n")
        monkeypatch.setenv("HARNESS_HOOK_DISPATCH_CONFIG", str(p))
        code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
        assert code == 0

    def test_unparseable_registry_failclosed(self, tmp_path, monkeypatch):
        p = tmp_path / "reg.yaml"
        p.write_text("{ this: is: not valid: yaml ::::\n", encoding="utf-8")
        monkeypatch.setenv("HARNESS_HOOK_DISPATCH_CONFIG", str(p))
        code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
        assert code == 2  # a broken registry standing in for real gates fails closed
