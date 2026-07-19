#!/usr/bin/env python3
"""hook_dispatch.py — ONE command per (event, matcher) group.

Claude Code spawns one process per registered hook per tool call. A busy
PreToolUse:Bash fires 8 separate Python interpreters; across all events that is
~61k spawns a day. This dispatcher replaces the N per-group commands with ONE:
it reads stdin once, loads the group's registry, and runs each hook's `core(data)`
IN-PROCESS under its correct HOOK_CLASS posture — no re-spawn, no stdin re-read.

Posture is preserved exactly (the whole point — a mixed fail-open/fail-closed loop
is the F3 hole). The contract (plan Validation Log VL-1, hardened through VL-4/VL-5):

  * Run order: telemetry + nudge cores FIRST (isolated, fail-open, timed) so their
    JSONL/trace side-effects are never dropped by a later block; compliance cores
    AFTER, in registry order.
  * Timeout: each core runs in a daemon worker thread observed with join(timeout)
    (hook_runtime.run_core_isolated — portable, no SIGALRM). telemetry/nudge timeout
    → skip (fail-open); compliance timeout → exit 2 (fail-closed). The main thread
    never injects into the worker, so a gate's own `except Exception` cannot swallow
    the timeout (C1).
  * Short-circuit: the first compliance core that returns a reason AND is in
    `blocking` mode → stderr `[name] BLOCKED: reason` + exit 2, remaining cores skip.
    A reason in `advisory` mode (e.g. simplify_gate shipped-ON) → `[advisory]` stderr
    + CONTINUE, no early stop (OVERTURN-1).
  * stdin: a genuine READ failure with any compliance core in the group → exit 2
    (fail-closed); an empty/unparseable payload → {} → continue (the anti-DoS
    fail-open contract, C4).
  * stdout merge on continue: ONE JSON — {continue:true} plus a joined systemMessage
    (drained per-core) plus a joined additionalContext, the latter ONLY for
    UserPromptSubmit / SessionStart (CC honors the field only there; Stop uses
    decision:block+reason). Queue drained per-core so a crashing core never leaks its
    partial systemMessage into a later core's blob (C8).
  * enabled:false in-loop → skip; a disabled compliance core still records a
    skip-trace once per session (inherits Phase 3).

Fail-open on the dispatcher's own telemetry side, fail-closed on any error while a
compliance core is still pending.
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import hook_runtime  # noqa: E402

HOOK_CLASS = "compliance"  # the dispatcher inherits the strictest posture in its group
NAME = "hook_dispatch"

_REGISTRY_NAME = "hook-dispatch.yaml"
_ADDITIONAL_CONTEXT_EVENTS = ("UserPromptSubmit", "SessionStart")


def _core_timeout_s() -> float:
    """Per-core timeout budget (seconds). Env-overridable so tests can shrink it;
    a bad value degrades to the 5s default."""
    try:
        return float(os.environ.get("HARNESS_DISPATCH_TIMEOUT", "5.0"))
    except (TypeError, ValueError):
        return 5.0


# --- registry -----------------------------------------------------------------

def _registry_path() -> Path:
    raw = os.environ.get("HARNESS_HOOK_DISPATCH_CONFIG")
    return Path(raw) if raw else Path(__file__).resolve().parent.parent / "data" / _REGISTRY_NAME


def load_registry(path=None) -> dict:
    """Parse the dispatch registry into {(event, matcher): [core-spec, ...]}.

    A core-spec is {name, module, entry, class[, kind]}. Raises on a missing or
    unparseable file — the caller decides whether that is fail-closed (a wired group
    with a broken registry) vs a benign empty group. Group keys are 'event:matcher'."""
    p = Path(path) if path is not None else _registry_path()
    import yaml  # lazy — a compliance-bearing dispatcher needs it, and blocks if absent
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    out = {}
    for key, cores in (raw.get("groups") or {}).items():
        event, _, matcher = str(key).partition(":")
        specs = []
        for c in (cores or []):
            if not isinstance(c, dict) or not c.get("module"):
                continue
            specs.append({
                "name": str(c.get("name") or c["module"]),
                "module": str(c["module"]),
                "entry": str(c.get("entry") or "core"),
                "class": str(c.get("class") or "telemetry"),
                "kind": c.get("kind"),  # "additionalContext" | None
                # fail_open: a compliance hook whose crash/timeout is fail-open BY DESIGN
                # (e.g. simplify_gate — a heuristic advisory whose own main() never blocks
                # on an internal error). Only the crash/timeout path is affected; a
                # returned BLOCK reason still respects hook_mode. Default False = a crash
                # fails closed, the safe default for a real gate.
                "fail_open": bool(c.get("fail_open", False)),
                # per-hook timeout override (seconds): a core making a slow external call
                # (e.g. a partner-model spawn) needs longer than the default budget; None
                # → the group default. The hook's inherent latency is not the dispatcher's
                # to cap below what the standalone would wait.
                "timeout": c.get("timeout"),
            })
        out[(event, matcher or "*")] = specs
    return out


def _resolve_core(spec):
    """Import the spec's module and return (callable, hook_class). The module's own
    HOOK_CLASS constant wins over the registry's `class` (config cannot reclassify a
    hook — mirrors hook_enabled). Returns (None, class) if the entry is missing."""
    mod = __import__(spec["module"])
    fn = getattr(mod, spec["entry"], None)
    cls = getattr(mod, "HOOK_CLASS", None) or spec["class"]
    return fn, cls


# --- per-core execution -------------------------------------------------------

class _Block(Exception):
    """A compliance verdict/timeout/crash that must stop the group with exit 2."""
    def __init__(self, reason):
        super().__init__(reason)
        self.reason = reason


def _run_group(event, matcher, data, specs):
    """Run every core in `specs` per the contract. Returns a continue-blob dict, or
    raises _Block(reason) for a fail-closed stop. `data` is the parsed stdin payload."""
    session = data.get("session_id") if isinstance(data, dict) else None
    # telemetry + nudge FIRST (in registry order), compliance AFTER (registry order).
    ordered = ([s for s in specs if s["class"] != "compliance"] +
               [s for s in specs if s["class"] == "compliance"])

    sys_msgs = []
    addl_ctx = []
    for spec in ordered:
        name = spec["name"]
        reg_cls = spec["class"]
        # Cheap enabled-check BEFORE importing the module — a disabled hook must not
        # pay the per-dispatch import tax, and a disabled compliance hook with a
        # broken import must not fail the group closed. Uses the registry class
        # (import-free); a disabled hook does not run regardless of its true
        # HOOK_CLASS, so deciding to SKIP off the registry class cannot change a gate
        # outcome (the module HOOK_CLASS still governs every RUNNING core below).
        if not hook_runtime.hook_enabled(name, reg_cls):
            if reg_cls == "compliance":
                _skip_trace_once(name, session)
            continue

        # Enabled -> import + resolve. HOOK_CLASS constant is authoritative; fall back
        # to the registry class if the module cannot be imported cleanly (compliance =
        # safest assumption).
        try:
            core, cls = _resolve_core(spec)
        except Exception as e:  # noqa: BLE001
            if reg_cls == "compliance":
                hook_runtime.log_hook_error(name, e)
                raise _Block("dispatch: cannot load %s (%s)" % (name, e))
            hook_runtime.log_hook_error(name, e)
            continue
        if core is None:
            if cls == "compliance":
                raise _Block("dispatch: %s.%s missing" % (spec["module"], spec["entry"]))
            continue

        hook_runtime._reset_pending_system_messages()  # per-core clean slate (C8)
        _t0 = time.monotonic()
        _timeout = spec.get("timeout")
        _timeout = float(_timeout) if _timeout else _core_timeout_s()
        res = hook_runtime.run_core_isolated(core, data, timeout=_timeout)
        _emit_timing(event, matcher, name, cls, (time.monotonic() - _t0) * 1000.0,
                     res.get("status"))
        status = res.get("status")

        if status == "timeout":
            hook_runtime._reset_pending_system_messages()  # discard partial queue
            if cls == "compliance" and not spec.get("fail_open"):
                raise _Block("%s timed out (>%.1fs) — fail-closed" % (name, _core_timeout_s()))
            # fail-open-by-design compliance (e.g. simplify_gate) OR telemetry/nudge
            hook_runtime.log_hook_error(name, TimeoutError("%s core timeout" % name))
            continue

        if status == "error":
            hook_runtime._reset_pending_system_messages()  # discard partial queue
            err = res.get("error")
            hook_runtime.log_hook_error(name, err)
            if cls == "compliance" and not spec.get("fail_open"):
                raise _Block("%s crashed (%s) — fail-closed" % (name, type(err).__name__))
            continue

        value = res.get("value")
        if cls == "compliance":
            if value:  # a block reason
                if hook_runtime.hook_mode(name, "compliance") == "blocking":
                    hook_runtime._reset_pending_system_messages()
                    raise _Block("%s: %s" % (name, value))
                # advisory reason: warn, do NOT stop (OVERTURN-1)
                sys.stderr.write("[advisory] %s: %s\n" % (name, value))
                hook_runtime._reset_pending_system_messages()
                continue
            # no reason — a soft compliance core may have queued a systemMessage
            q = hook_runtime._drain_system_messages()
            if q:
                sys_msgs.append(q)
            continue

        # telemetry / nudge core
        # kind wins over class: a hook may be nudge/telemetry-class for its FAILURE
        # posture yet produce a model-channel additionalContext (e.g. a Stop reinject or
        # a post-compaction resurface) rather than a routed nudge advisory.
        if spec.get("kind") == "additionalContext" and value:
            addl_ctx.append(value if isinstance(value, str) else str(value))
        elif cls == "nudge" and value:
            hook_runtime.emit_nudge(name, value, session=session)
        q = hook_runtime._drain_system_messages()
        if q:
            sys_msgs.append(q)

    return _assemble_blob(event, addl_ctx, sys_msgs)


# CC event name -> context_surface_config SSOT key (the injector chokepoint events).
# For these, the model channel + the OPTIONAL human systemMessage mirror (and Stop's
# decision:block/reason model channel) are owned by context_surface_config so every
# injector — and now the dispatcher — mirrors identically. Emitting additionalContext
# raw here would DROP the human mirror that the standalone hooks carry.
_CHOKEPOINT_EVENT_KEY = {
    "UserPromptSubmit": "user_prompt_submit",
    "SessionStart": "session_start",
    "SubagentStart": "subagent_start",
    "Stop": "stop",
}


def _assemble_blob(event, addl_ctx, sys_msgs):
    """Build the ONE terminal continue-blob. additionalContext for a chokepoint event
    routes through context_surface_config.build_payload so the human systemMessage
    mirror + Stop decision:block model channel match the standalone injectors exactly;
    other events emit additionalContext raw (only UPS/SessionStart honor the field)."""
    joined_ctx = "\n".join(addl_ctx) if addl_ctx else ""
    blob = None
    if joined_ctx and event in _CHOKEPOINT_EVENT_KEY:
        try:
            sys.path.append(str(Path(__file__).resolve().parent.parent / "scripts"))
            import context_surface_config as _cs
            blob = _cs.build_payload(_CHOKEPOINT_EVENT_KEY[event], joined_ctx)
        except Exception as e:  # noqa: BLE001 — fall back to a raw additionalContext emit
            hook_runtime.log_hook_error(NAME, e)
            blob = None
    if blob is None:
        blob = {"continue": True}
        if joined_ctx and event in _ADDITIONAL_CONTEXT_EVENTS:
            blob["hookSpecificOutput"] = {"hookEventName": event,
                                          "additionalContext": joined_ctx}
    # fold the dispatcher's own queued systemMessages under any mirror build_payload set
    if sys_msgs:
        existing = blob.get("systemMessage")
        blob["systemMessage"] = ((existing + "\n") if existing else "") + "\n".join(sys_msgs)
    # continue:true is the non-blocking marker for every shape EXCEPT Stop's
    # decision:block (which re-invokes and must not also say continue).
    if "decision" not in blob:
        blob.setdefault("continue", True)
    return blob


def _emit_timing(event, matcher, hook, cls, elapsed_ms, status) -> None:
    """Record a per-core elapsed_ms to the diag stream (always-on INFO — every machine
    gets its own hook-cost profile, the perf dashboard's input). Fail-open: a diag
    hiccup never affects the dispatch. HARNESS_DEBUG adds the verbose per-core line."""
    try:
        import hlog
        # NB: the diag record's own key is `event` (="core_timing"); the CC hook event
        # is carried as `hook_event` to avoid colliding with hlog.info's `event` param.
        hlog.info("core_timing", **{
            "hook_event": event, "matcher": matcher, "hook": hook, "class": cls,
            "elapsed_ms": round(elapsed_ms, 3), "status": status})
        if os.environ.get("HARNESS_DEBUG"):
            hlog.debug("core_detail", hook=hook, hook_event=event, matcher=matcher,
                       elapsed_ms=round(elapsed_ms, 3), status=status)
    except Exception:  # noqa: BLE001 — self-timing is telemetry, never blocks
        pass


def _skip_trace_once(name, session):
    """Record a disabled-compliance skip once per (session, hook) — inherits the
    Phase 3 marker so a wide always-off gate does not spam the trace. Fail-open."""
    try:
        if not hook_runtime._skip_already_traced(session, name):
            import trace_log
            trace_log.append_event(hook=name, event="%s_skip" % name, session=session,
                                   note="disabled (dispatch)")
            hook_runtime._mark_skip_traced(session, name)
    except Exception as e:  # noqa: BLE001 — the skip trace is telemetry, never blocks
        hook_runtime.log_hook_error(name, e)


# --- entry --------------------------------------------------------------------

def _group_has_compliance(specs) -> bool:
    """True if the group carries a compliance gate. Defense-in-depth (M1): trusts the
    registry `class`, BUT if a spec is labelled non-compliance it best-effort resolves
    the module's own HOOK_CLASS — a mislabelled gate must not hide behind a wrong
    registry class and fail open. An unresolvable module is assumed compliance (safe)."""
    for s in specs:
        if s["class"] == "compliance":
            return True
        try:
            _, cls = _resolve_core(s)
            if cls == "compliance":
                return True
        except Exception:  # noqa: BLE001 — cannot tell → assume the gate side
            return True
    return False


def run(argv=None, stdin_text=None) -> int:
    """Testable entry. Resolves (event, matcher) from argv, reads stdin, runs the
    group, writes the terminal JSON to stdout (or a block reason to stderr). Returns
    the process exit code (0 continue / 2 block). Never raises."""
    argv = list(sys.argv[1:] if argv is None else argv)
    event = argv[0] if len(argv) >= 1 else ""
    matcher = argv[1] if len(argv) >= 2 else "*"

    # Load the registry. A wired group whose registry is broken must fail closed IF
    # it could carry a compliance gate; but we cannot know the class before parsing,
    # so an unparseable/missing registry → fail-closed exit 2 (the safe default for a
    # command that stands in for real gates).
    try:
        registry = load_registry()
    except Exception as e:  # noqa: BLE001
        hook_runtime.log_hook_error(NAME, e)
        sys.stderr.write("[%s] BLOCKED: registry unreadable (%s). Fail-closed.\n" % (NAME, e))
        return 2

    specs = registry.get((event, matcher))
    if specs is None:
        specs = registry.get((event, "*"), [])
    # A parsed-but-empty group is a legitimate no-op (e.g. a migrated-away group with
    # an empty registry) — continue, do NOT block (C5: distinct from unparseable). But
    # a dispatcher wired for a real event whose (event, matcher) key resolves to NOTHING
    # is the matcher-drift smell (M2): if that event should have carried gates they were
    # just skipped. Warn loudly to the diag stream + stderr so the drift is visible
    # rather than a silent gate bypass.
    if not specs:
        if event:
            try:
                import hlog
                hlog.warn("dispatch_empty_group", hook_event=event, matcher=matcher,
                          note="no registry group — matcher drift skips any gates for this key")
            except Exception:  # noqa: BLE001
                pass
            sys.stderr.write("[%s] no dispatch group for %s:%s — if this event has gates, "
                             "check hook-dispatch.yaml vs the wired matcher.\n"
                             % (NAME, event, matcher))
        _write_continue({"continue": True})
        return 0

    has_compliance = _group_has_compliance(specs)

    # Read stdin ONCE. A genuine read FAILURE with a compliance core pending is
    # fail-closed; an empty/unparseable payload degrades to {} → continue (C4).
    if stdin_text is None:
        try:
            stdin_text = sys.stdin.read()
        except Exception as e:  # noqa: BLE001
            hook_runtime.log_hook_error(NAME, e)
            if has_compliance:
                sys.stderr.write("[%s] BLOCKED: stdin read failed (%s). Fail-closed.\n" % (NAME, e))
                return 2
            _write_continue({"continue": True})
            return 0
    data = hook_runtime._parse(stdin_text)

    try:
        blob = _run_group(event, matcher, data, specs)
    except _Block as b:
        sys.stderr.write("[%s] BLOCKED: %s\n" % (NAME, b.reason))
        return 2
    except Exception as e:  # noqa: BLE001 — any unexpected error with compliance pending is fail-closed
        hook_runtime.log_hook_error(NAME, e)
        if has_compliance:
            sys.stderr.write("[%s] BLOCKED: dispatch crashed (%s). Fail-closed.\n" % (NAME, e))
            return 2
        _write_continue({"continue": True})
        return 0

    _write_continue(blob)
    return 0


def _write_continue(blob) -> None:
    try:
        sys.stdout.write(json.dumps(blob, ensure_ascii=False))
        sys.stdout.flush()
    except Exception:  # noqa: BLE001 — fail-open on the write itself
        pass


def main(argv=None) -> None:
    sys.exit(run(argv=argv))


if __name__ == "__main__":
    main()
