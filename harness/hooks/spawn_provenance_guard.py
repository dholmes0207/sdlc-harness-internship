#!/usr/bin/env python3
"""spawn_provenance_guard.py — PreToolUse(Agent|Task) gate: enforce the
resolved Layer-1b spawn-provenance budget (Ask-3 BLOCK form).

Ships to the SAME `PreToolUse:Agent|Task` group as `spawn_provenance_nudge`
(the Layer-1a advisory) and reads the SAME `spawn_provenance.py` counter, but
this is the BLOCK form: `HOOK_CLASS = "compliance"`. Default OFF
(`spawn_provenance.block_enabled()` — a new exit-2 surface with false-block
risk, opt-in like `secret_scan_before_ship`); when on, it BLOCKS (exit 2) a
spawn that would exceed the session's resolved budget instead of merely
nudging.

Token lifecycle (the resolved decision — see
plans/reports/spawn-provenance-token-lifecycle-sequential-thinking-260716.md):
BLOCK iff `prior >= B`, where `prior = count_in_window(session,
since_ts=window_start(session))` and `B = budget(session)`:

  - an active (session-matched, unexpired) orchestrate token -> `T_epoch =
    token.ts`, `B = min(token.sub_count, sub_count_cap())`
  - an expired token on record for the session -> `T_epoch =
    token.expires_at` (only post-expiry spawns count), `B = threshold()`
  - no token ever for the session -> `T_epoch = now -
    unticketed_window_seconds()` (a sliding wall-clock window: only spawns in
    the last W seconds count, so a legitimate sequential flow never
    accumulates a session-lifetime count), `B = threshold()`

`record_spawn` runs AFTER the decision (M6 — the in-flight spawn is never
counted against itself) and carries `tool_use_id` so this guard dedups
against the Layer-1a nudge's own record of the SAME spawn.

Honest ceiling (unchanged even when blocking): this checks provenance +
count/budget + shape ONLY — never strategy quality (PreToolUse is
semantics-blind to what the spawned agent will actually do).

HOOK_CLASS = compliance: mirrors `model_guard`'s F4 skeleton
VERBATIM — an INTERNAL ERROR fails OPEN (exit 0) so a bug in this gate never
wedges a spawn; only a genuine block decision exits 2. The block path sits
OUTSIDE the fail-open try and the `except` re-raises SystemExit, so a broad
except can never swallow the exit-2 and turn the gate dark. The dispatch row
additionally carries `fail_open: true` as defense in depth (hook-dispatch.yaml)
— the in-process dispatcher calls `core()` directly, bypassing this file's
own `main()` gating, so that registry flag is what actually protects
production; `main()`'s own try exists for the rarer direct-spawn path.

write_token is minted ONLY at the workflow-orchestrate APPROVAL step (never
agent-callable at will) — closes the forgery/self-auth hole (memory
anchor-cli-self-auth-hole). This gate is a reader; it never mints.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hook_runtime  # noqa: E402
import spawn_provenance  # noqa: E402

HOOK_CLASS = "compliance"
_HOOK = "spawn_provenance_guard"
_SPAWN_TOOLS = {"Agent", "Task"}


def _block_reason(prior: int, budget: int, session: str) -> str:
    has_token = spawn_provenance.has_orchestrate_token(session)
    source = ("an active orchestrate token" if has_token
              else "no recorded orchestrate strategy")
    reason = (
        "spawn_provenance: this session has %d tracked subagent spawns against "
        "a budget of %d (%s). This checks spawn PROVENANCE, COUNT, and SHAPE "
        "only — never strategy quality. Size and group the fan-out through "
        "hs:workflow-orchestrate and get the strategy approved (that mints a "
        "wider, disk-backed budget) before spawning more"
        % (prior, budget, source)
    )
    # The age-out advice is honest ONLY on the TRUE no-token lane, whose
    # window slides by wall-clock time (now - unticketed_window_seconds), so
    # older tracked spawns really do fall out of the count over W seconds. An
    # EXPIRED token also reports has_token=False, but its window
    # [expires_at, now] does NOT slide — promising an age-out there would be a
    # lie (F1). Gate on _most_recent_token(session) is None (no token ever).
    # "older tracked spawns", not "the oldest": a blocked spawn is still
    # recorded (the Layer-1a nudge records unconditionally), so we do not
    # over-claim exactly which record ages out first (F2).
    if spawn_provenance._most_recent_token(session) is None:
        window = spawn_provenance.unticketed_window_seconds()
        reason += (", or wait ~%d seconds for older tracked spawns to age out "
                   "of the window" % window)
    reason += (
        ". If hs:workflow-orchestrate is off/not installed, run it via "
        "/hs:use workflow-orchestrate (hs:use is always available)."
    )
    return reason


def core(data: dict):
    """Return a BLOCK reason string when this spawn would exceed the
    session's resolved budget, else None. Stdout-free (dispatcher-callable).

    Every reader this calls (`window_start`/`budget`/`count_in_window`/
    `record_spawn`) is fail-open by its own contract (spawn_provenance
    module docstring), so `core()` carries no try/except of its own — an
    unexpected crash anywhere in this call chain surfaces to the caller
    (main()'s fail-open try, or the dispatcher's `fail_open: true` row)
    rather than being silently absorbed here (F4: the crash-handling seam
    lives ONE layer up, never inside the gate that might itself be buggy)."""
    if not spawn_provenance.block_enabled():
        return None  # the Layer-1b flip switch — opt-in, default off
    if not isinstance(data, dict) or data.get("tool_name") not in _SPAWN_TOOLS:
        return None  # not an Agent/Task spawn — free
    session = data.get("session_id") or ""
    tool_use_id = data.get("tool_use_id")
    epoch = spawn_provenance.window_start(session)
    b = spawn_provenance.budget(session)
    # exclude_tool_use_id: the dispatcher runs the Layer-1a nudge (nudge
    # class) BEFORE this guard (compliance class) — by this read the nudge
    # may have already recorded the in-flight spawn under this SAME
    # tool_use_id. Without excluding it here that record counts as "prior"
    # and turns budget N into N-1 (F1); this restores M6 regardless of
    # intra-dispatch ordering, and is a no-op when the nudge is disabled or
    # tool_use_id is absent (nothing was recorded under it yet).
    prior = spawn_provenance.count_in_window(session, since_ts=epoch, exclude_tool_use_id=tool_use_id)
    blocked = prior >= b
    reason = _block_reason(prior, b, session) if blocked else None
    # Record AFTER the decision (M6) — the spawn under evaluation is never
    # counted against itself, and this dedups with the Layer-1a nudge's own
    # record of the same spawn via the shared tool_use_id.
    spawn_provenance.record_spawn(session, tool_use_id)
    return reason


def main() -> None:
    # 1. Registration toggle is the ONLY switch. A broken enabled read fails OPEN.
    try:
        if not hook_runtime.hook_enabled(_HOOK, HOOK_CLASS):
            hook_runtime.emit_continue()
            sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — a broken enabled read must not wedge a spawn
        hook_runtime.log_hook_error(_HOOK, e)
        hook_runtime.emit_continue()
        sys.exit(0)

    # Every internal error from here fails OPEN (documented deviation, F4 — mirrors
    # model_guard).
    try:
        reason = core(hook_runtime.read_stdin_json())  # {} on empty/malformed
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — FAIL-OPEN: a gate crash never wedges a spawn
        hook_runtime.log_hook_error(_HOOK, e)
        reason = None
    if reason:
        # The only exit-2 path — deliberate block, kept outside the fail-open try (F4).
        sys.stderr.write("[%s] BLOCKED: %s\n" % (_HOOK, reason))
        sys.exit(2)
    hook_runtime.emit_continue()
    sys.exit(0)


if __name__ == "__main__":
    main()
