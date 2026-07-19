"""test_hook_dispatch_core.py — the (event, matcher) dispatcher's posture contract.

The dispatcher runs many hooks' core(data) in ONE process. The load-bearing property
is that it preserves each core's HOOK_CLASS posture EXACTLY — a mixed fail-open /
fail-closed loop is the F3 gate-bypass hole. These tests pin the VL-1 contract:
short-circuit on a blocking compliance verdict, fail-open on telemetry crash/timeout,
fail-CLOSED on compliance crash/timeout (even when the core swallows its own
exception — C1), stdin read-fail vs empty payload, the stdout merge, run order, the
disabled-skip trace, hot-reload, and per-core queue isolation.

Cores are class-driven fixtures (no HOOK_CLASS constant) so one module can host cores
of every class; the registry's `class:` decides.
"""
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import hook_runtime  # noqa: E402
import hook_dispatch  # noqa: E402

_FIXTURE = textwrap.dedent('''
    import os, sys, time

    def _hr():
        # resolve hook_runtime from sys.modules at CALL time: a neighbour test may
        # importlib.reload() it, so a module-level binding could orphan against the
        # instance hook_dispatch drains. sys.modules is the single current object.
        return sys.modules.get("hook_runtime")

    def _record(tag):
        p = os.environ.get("DISPATCH_ORDER_FILE")
        if p:
            with open(p, "a", encoding="utf-8") as fh:
                fh.write(tag + "\\n")

    def ok(data): _record("ok"); return None
    def crash(data): raise RuntimeError("telemetry boom")
    def block(data): return "blocked-reason-XYZ"
    def passes(data): return None
    def slow(data): time.sleep(5); return "late"
    def slow_swallow(data):
        try:
            time.sleep(5); return "BLOCK"
        except Exception:
            return None
    def addlctx_a(data): return "CTX-A"
    def addlctx_b(data): return "CTX-B"
    def sysmsg(data):
        hr = _hr()
        if hr: hr.queue_system_message("SYS-HELLO")
        return None
    def leaky(data):
        # queues then raises -> its partial systemMessage must NOT leak into a later blob
        hr = _hr()
        if hr: hr.queue_system_message("LEAK-SHOULD-VANISH")
        raise RuntimeError("leaky boom")
    def order_tel(data): _record("telemetry"); return None
    def order_comp(data): _record("compliance"); return None
''')


@pytest.fixture
def disp(tmp_path, monkeypatch):
    # A neighbour test (test_hook_runtime) importlib.reload()s hook_runtime, which
    # orphans the module singleton other modules bound at import. Re-pin the canonical
    # instance and rebind hook_dispatch to it so the queue the fixture writes is the
    # queue the dispatcher drains (production never reloads — this is test hygiene).
    import importlib
    sys.modules["hook_runtime"] = hook_runtime
    importlib.reload(hook_dispatch)

    fixdir = tmp_path / "fix"
    fixdir.mkdir()
    (fixdir / "dispatch_fix.py").write_text(_FIXTURE, encoding="utf-8")
    monkeypatch.syspath_prepend(str(fixdir))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_DISPATCH_TIMEOUT", "0.4")
    # hermetic: a leaked kill-switch from a neighbour test would skip telemetry cores
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    hook_runtime._reset_config_cache()
    hook_runtime._reset_nudge_channels_cache()
    hook_runtime._reset_pending_system_messages()

    def reg(groups, hooks_cfg=None):
        p = tmp_path / "reg.yaml"
        import yaml
        p.write_text(yaml.safe_dump({"groups": groups}), encoding="utf-8")
        monkeypatch.setenv("HARNESS_HOOK_DISPATCH_CONFIG", str(p))
        if hooks_cfg is not None:
            hc = tmp_path / "hooks.yaml"
            hc.write_text(yaml.safe_dump({"hooks": hooks_cfg}), encoding="utf-8")
            monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(hc))
            hook_runtime._reset_config_cache()
        return p

    return type("D", (), {"reg": staticmethod(reg), "fixdir": fixdir,
                          "tmp": tmp_path, "fixdir_str": str(fixdir)})


def _core(module="dispatch_fix", entry="ok", cls="telemetry", name=None, kind=None):
    d = {"module": module, "entry": entry, "class": cls, "name": name or entry}
    if kind:
        d["kind"] = kind
    return d


class TestPostureShortCircuit:
    def test_compliance_block_short_circuits(self, disp):
        of = disp.tmp / "order.txt"
        os.environ["DISPATCH_ORDER_FILE"] = str(of)
        try:
            disp.reg({"PreToolUse:Bash": [
                _core(entry="ok", cls="telemetry"),
                _core(entry="block", cls="compliance", name="c_block"),
                _core(entry="order_comp", cls="compliance", name="c_after"),
            ]})
            code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
            assert code == 2
            # the compliance core AFTER the block must not have run
            ran = of.read_text().splitlines() if of.exists() else []
            assert "compliance" not in ran
        finally:
            os.environ.pop("DISPATCH_ORDER_FILE", None)

    def test_advisory_compliance_not_blocked(self, disp, capsys):
        of = disp.tmp / "order.txt"
        os.environ["DISPATCH_ORDER_FILE"] = str(of)
        try:
            disp.reg({"PreToolUse:Bash": [
                _core(entry="block", cls="compliance", name="c_adv"),
                _core(entry="order_comp", cls="compliance", name="c_after"),
            ]}, hooks_cfg={"c_adv": {"mode": "advisory"}})
            code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
            assert code == 0  # advisory reason does not block
            err = capsys.readouterr().err
            assert "[advisory]" in err and "c_adv" in err
            # the core AFTER the advisory still ran (no early stop)
            assert "compliance" in of.read_text().splitlines()
        finally:
            os.environ.pop("DISPATCH_ORDER_FILE", None)


class TestFailOpenVsClosed:
    def test_telemetry_core_crash_failopen(self, disp, capsys):
        of = disp.tmp / "order.txt"
        os.environ["DISPATCH_ORDER_FILE"] = str(of)
        try:
            disp.reg({"PreToolUse:Bash": [
                _core(entry="crash", cls="telemetry", name="t_crash"),
                _core(entry="ok", cls="telemetry", name="t_after"),
            ]})
            code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
            assert code == 0  # a telemetry crash never blocks
            assert "ok" in of.read_text().splitlines()  # the next core still ran
        finally:
            os.environ.pop("DISPATCH_ORDER_FILE", None)

    def test_compliance_timeout_failclosed(self, disp):
        disp.reg({"PreToolUse:Bash": [_core(entry="slow", cls="compliance", name="c_slow")]})
        code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
        assert code == 2

    def test_timeout_survives_core_except(self, disp):
        # C1: a compliance core with its OWN `except Exception: return None` still
        # fails closed on timeout — the main thread observes the worker, it does not
        # inject an exception the core could swallow.
        disp.reg({"PreToolUse:Bash": [
            _core(entry="slow_swallow", cls="compliance", name="c_swallow")]})
        code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
        assert code == 2

    def test_fail_open_compliance_crash_continues(self, disp):
        # H1: a compliance core marked fail_open (e.g. simplify_gate) that CRASHES must
        # continue (exit 0), preserving its by-design fail-open-on-error posture, while
        # a normal compliance crash still fails closed.
        of = disp.tmp / "order.txt"
        os.environ["DISPATCH_ORDER_FILE"] = str(of)
        try:
            disp.reg({"PreToolUse:Bash": [
                {"module": "dispatch_fix", "entry": "crash", "class": "compliance",
                 "name": "soft_gate", "fail_open": True},
                _core(entry="order_comp", cls="compliance", name="hard_after"),
            ]})
            code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
            assert code == 0  # fail-open compliance crash does NOT block
            assert "compliance" in of.read_text().splitlines()  # later gate still ran
        finally:
            os.environ.pop("DISPATCH_ORDER_FILE", None)

    def test_fail_open_compliance_timeout_continues(self, disp):
        disp.reg({"PreToolUse:Bash": [
            {"module": "dispatch_fix", "entry": "slow", "class": "compliance",
             "name": "soft_slow", "fail_open": True}]})
        code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
        assert code == 0  # fail-open compliance timeout does NOT block

    def test_group_has_compliance_uses_module_hook_class(self, disp, tmp_path, monkeypatch):
        # M1: a module whose OWN HOOK_CLASS is compliance, mislabelled telemetry in the
        # registry, must still count as a gate (so stdin-read-failure fails closed).
        (disp.fixdir / "realgate.py").write_text(
            "HOOK_CLASS='compliance'\ndef core(data): return None\n", encoding="utf-8")
        specs = [{"module": "realgate", "entry": "core", "class": "telemetry",
                  "name": "realgate", "kind": None, "fail_open": False}]
        assert hook_dispatch._group_has_compliance(specs) is True

    def test_telemetry_timeout_failopen(self, disp):
        disp.reg({"PreToolUse:Bash": [
            _core(entry="slow", cls="telemetry", name="t_slow"),
            _core(entry="ok", cls="telemetry", name="t_after")]})
        of = disp.tmp / "order.txt"
        os.environ["DISPATCH_ORDER_FILE"] = str(of)
        try:
            code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
            assert code == 0  # telemetry timeout is skipped, not fatal
            assert "ok" in of.read_text().splitlines()
        finally:
            os.environ.pop("DISPATCH_ORDER_FILE", None)

    def test_timeout_portable_no_signal(self):
        # the timeout mechanism must be thread-based (portable/Windows), never SIGALRM.
        # scan the CALL forms so the docstring's prose mention of setitimer is not a
        # false positive: the code uses threading.Thread and never invokes signal.*.
        import inspect
        iso = inspect.getsource(hook_runtime.run_core_isolated)
        body = iso.replace(hook_runtime.run_core_isolated.__doc__ or "", "")  # drop docstring prose
        assert "threading.Thread" in body and ".join(" in body
        assert "setitimer" not in body and "SIGALRM" not in body and "signal" not in body


class TestStdin:
    def test_stdin_read_fail_failclosed(self, disp, monkeypatch):
        disp.reg({"PreToolUse:Bash": [_core(entry="passes", cls="compliance", name="c_pass")]})

        def boom():
            raise IOError("stdin exploded")
        monkeypatch.setattr(sys.stdin, "read", boom)
        code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text=None)
        assert code == 2  # read failure with a compliance core pending = fail-closed

    def test_empty_payload_failopen(self, disp):
        disp.reg({"PreToolUse:Bash": [_core(entry="passes", cls="compliance", name="c_pass")]})
        code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text="")
        assert code == 0  # empty payload -> {} -> continue (anti-DoS)

    def test_read_fail_no_compliance_failopen(self, disp, monkeypatch):
        disp.reg({"Stop:*": [_core(entry="ok", cls="telemetry", name="t")]})
        monkeypatch.setattr(sys.stdin, "read",
                            lambda: (_ for _ in ()).throw(IOError("x")))
        code = hook_dispatch.run(["Stop"], stdin_text=None)
        assert code == 0  # no compliance core -> a read failure is fail-open


class TestStdoutMerge:
    def test_merge_continue_addlctx_and_sysmsg(self, disp, capsys):
        disp.reg({"UserPromptSubmit:*": [
            _core(entry="addlctx_a", cls="telemetry", name="a", kind="additionalContext"),
            _core(entry="addlctx_b", cls="telemetry", name="b", kind="additionalContext"),
            _core(entry="sysmsg", cls="telemetry", name="s"),
        ]})
        code = hook_dispatch.run(["UserPromptSubmit"], stdin_text='{"session_id":"S"}')
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["continue"] is True
        assert out["hookSpecificOutput"]["additionalContext"] == "CTX-A\nCTX-B"
        assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
        assert "SYS-HELLO" in out["systemMessage"]

    def test_chokepoint_event_carries_human_mirror(self, disp, capsys, monkeypatch):
        # UserPromptSubmit additionalContext must route through context_surface_config
        # so the human systemMessage MIRROR is preserved (the standalone injectors add
        # it; the dispatcher must not drop it). Force system_message on for the event.
        import yaml
        cs = disp.tmp / "cs.yaml"
        cs.write_text(yaml.safe_dump({"user_prompt_submit": {"system_message": True,
                                                             "verbosity": "full"}}),
                      encoding="utf-8")
        monkeypatch.setenv("HARNESS_CONTEXT_SURFACE", str(cs))
        import context_surface_config as _cs
        _cs._reset()  # a neighbour test may have memoized a different config
        disp.reg({"UserPromptSubmit": [
            _core(entry="addlctx_a", cls="telemetry", name="a", kind="additionalContext")]})
        code = hook_dispatch.run(["UserPromptSubmit"], stdin_text='{"session_id":"S"}')
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert out["hookSpecificOutput"]["additionalContext"] == "CTX-A"  # model channel
        assert "CTX-A" in out.get("systemMessage", "")  # human mirror NOT dropped

    def test_additionalcontext_gated_by_event(self, disp, capsys):
        # the SAME additionalContext core on PreToolUse must NOT emit the field
        disp.reg({"PreToolUse:Bash": [
            _core(entry="addlctx_a", cls="telemetry", name="a", kind="additionalContext")]})
        code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert "hookSpecificOutput" not in out

    def test_queue_isolated_per_core(self, disp, capsys):
        # a core that queues then crashes must not leak its message into a later blob
        disp.reg({"UserPromptSubmit:*": [
            _core(entry="leaky", cls="telemetry", name="leaky"),
            _core(entry="sysmsg", cls="telemetry", name="s"),
        ]})
        code = hook_dispatch.run(["UserPromptSubmit"], stdin_text='{"session_id":"S"}')
        assert code == 0
        out = json.loads(capsys.readouterr().out)
        assert "LEAK-SHOULD-VANISH" not in out.get("systemMessage", "")
        assert "SYS-HELLO" in out.get("systemMessage", "")


class TestRunOrder:
    def test_telemetry_before_compliance(self, disp):
        of = disp.tmp / "order.txt"
        os.environ["DISPATCH_ORDER_FILE"] = str(of)
        try:
            # registry lists compliance FIRST, but the loop must run telemetry first
            disp.reg({"PreToolUse:Bash": [
                _core(entry="order_comp", cls="compliance", name="c"),
                _core(entry="order_tel", cls="telemetry", name="t"),
            ]})
            hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
            assert of.read_text().splitlines() == ["telemetry", "compliance"]
        finally:
            os.environ.pop("DISPATCH_ORDER_FILE", None)


class TestDisabledAndHotReload:
    def test_disabled_compliance_skip_trace_once(self, disp):
        disp.reg({"PreToolUse:Bash": [
            _core(entry="block", cls="compliance", name="c_off")]},
            hooks_cfg={"c_off": {"enabled": False}})
        for _ in range(5):
            code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
            assert code == 0  # disabled gate does not block
        trace = disp.tmp / "state" / "trace"
        n = 0
        for f in trace.glob("trace-*.jsonl"):
            for line in f.read_text().splitlines():
                if '"c_off_skip"' in line:
                    n += 1
        assert n == 1  # skip recorded once per session, not per call

    def test_disabled_compliance_broken_import_does_not_block(self, disp):
        # A DISABLED compliance hook whose module cannot be imported must be skipped
        # BEFORE the import (the enabled-check now precedes _resolve_core). Otherwise
        # the broken import fails the whole group closed (exit 2) though the hook is
        # off — and every dispatch pays the import tax for a disabled hook.
        disp.reg({"PreToolUse:Bash": [
            _core(module="dispatch_absent_xyz", entry="core",
                  cls="compliance", name="c_broken")]},
            hooks_cfg={"c_broken": {"enabled": False}})
        code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
        assert code == 0, "disabled compliance hook with a broken import must not block"

    def test_enabled_toggle_hotreload(self, disp):
        # enabled -> blocks; flip to disabled -> continues (config re-read per spawn)
        disp.reg({"PreToolUse:Bash": [_core(entry="block", cls="compliance", name="c_t")]},
                 hooks_cfg={"c_t": {"enabled": True}})
        assert hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}') == 2
        # rewrite config to disable, reset cache (mirrors a fresh spawn)
        import yaml
        (disp.tmp / "hooks.yaml").write_text(
            yaml.safe_dump({"hooks": {"c_t": {"enabled": False}}}), encoding="utf-8")
        hook_runtime._reset_config_cache()
        assert hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}') == 0


class TestSubprocessExitCodes:
    """The 3 core behaviors as REAL process exit codes (the acceptance criterion)."""

    def _run(self, disp, event, matcher):
        env = dict(os.environ)
        env["PYTHONPATH"] = disp.fixdir_str + os.pathsep + str(_HOOKS) + os.pathsep + env.get("PYTHONPATH", "")
        env["HARNESS_HOOK_DISPATCH_CONFIG"] = str(disp.tmp / "reg.yaml")
        env["HARNESS_STATE_DIR"] = str(disp.tmp / "state")
        env["HARNESS_DISPATCH_TIMEOUT"] = "0.4"
        args = [sys.executable, str(_HOOKS / "hook_dispatch.py"), event]
        if matcher:
            args.append(matcher)
        return subprocess.run(args, input='{"session_id":"S"}',
                              capture_output=True, text=True, env=env)

    def test_block_exit2(self, disp):
        disp.reg({"PreToolUse:Bash": [_core(entry="block", cls="compliance", name="c")]})
        r = self._run(disp, "PreToolUse", "Bash")
        assert r.returncode == 2 and "BLOCKED" in r.stderr

    def test_telemetry_crash_exit0(self, disp):
        disp.reg({"PreToolUse:Bash": [_core(entry="crash", cls="telemetry", name="t")]})
        r = self._run(disp, "PreToolUse", "Bash")
        assert r.returncode == 0
        assert json.loads(r.stdout)["continue"] is True

    def test_compliance_timeout_exit2(self, disp):
        disp.reg({"PreToolUse:Bash": [_core(entry="slow", cls="compliance", name="c")]})
        r = self._run(disp, "PreToolUse", "Bash")
        assert r.returncode == 2
