#!/usr/bin/env python3
"""spawn_provenance_nudge.py — PreToolUse(Agent|Task) advisory: wide
un-planned subagent fan-out (Layer-1a, `nudge` class, fail-open SILENT).

Counts subagent spawns per `session_id` (`spawn_provenance.count_in_window` —
windowed, truncated, scan-derived) and, once a session's PRIOR spawn count
reaches the configured threshold (default 5, `spawn_provenance.threshold()`)
with no orchestrate strategy token on record
(`spawn_provenance.has_orchestrate_token` — always False today, no producer
exists yet), emits a one-line advisory recommending
`hs:workflow-orchestrate`. Layer-1b (the BLOCK form) is NOT built here — see
`spawn_provenance.py`'s module docstring for the seam it leaves.

Order matters (M6): `core()` reads the PRIOR count and decides FIRST, THEN
records the current spawn — the spawn in flight is never counted against
itself. With the default threshold=5: prior=4 (this is the 5th spawn) stays
silent; prior=5 (the 6th spawn — total >5) fires.

Nudge posture: default OFF (config-gated), advisory, fail-open — writes a
reminder to its configured sink and ALWAYS continues (never exit 2). The
binding HOOK_CLASS lives here in code, never in config.
"""
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
for _p in (_HERE, os.path.join(os.path.dirname(_HERE), "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import hook_runtime  # noqa: E402
import spawn_provenance  # noqa: E402

HOOK_CLASS = "nudge"
_NAME = Path(__file__).stem
_SPAWN_TOOLS = {"Agent", "Task"}


def core(data: dict):
    """Return the advisory string when this spawn just crossed the threshold
    with no orchestrate token on record, else None. ALWAYS records the
    current spawn (best-effort) AFTER the decision — never before (M6).
    Carries `tool_use_id` into `record_spawn` so this nudge and the Layer-1b
    `spawn_provenance_guard` (same `PreToolUse:Agent|Task` group, same
    spawn) dedup onto ONE counted event instead of two."""
    if not isinstance(data, dict) or data.get("tool_name") not in _SPAWN_TOOLS:
        return None
    session = data.get("session_id") or ""
    tool_use_id = data.get("tool_use_id")
    prior = spawn_provenance.count_in_window(session)
    limit = spawn_provenance.threshold()
    fires = prior >= limit and not spawn_provenance.has_orchestrate_token(session)
    msg = None
    if fires:
        msg = (
            "[nudge] spawn_provenance: this session has spawned %d+ subagents "
            "with no recorded orchestrate strategy — consider "
            "hs:workflow-orchestrate to size and group the fan-out before "
            "spawning more. Advisory, non-blocking.\n" % (prior + 1)
        )
    spawn_provenance.record_spawn(session, tool_use_id)
    return msg


def main() -> int:
    # Nudge structure mirrors cook_isolation_nudge: honor the config gate,
    # run the detector fail-open, route via emit_nudge_and_continue, ALWAYS
    # continue. A disabled hook is fully inert.
    if not hook_runtime.hook_enabled(_NAME, HOOK_CLASS):
        hook_runtime.emit_continue()
        return 0
    data = hook_runtime.read_stdin_json()
    d = data if isinstance(data, dict) else {}
    try:
        msg = core(d)
        if msg:
            hook_runtime.emit_nudge_and_continue(_NAME, msg, d)
            return 0
    except Exception as e:  # noqa: BLE001 — fail-open: a nudge never blocks the tool
        hook_runtime.log_hook_error(_NAME, e)
    hook_runtime.emit_continue()
    return 0


if __name__ == "__main__":
    sys.exit(main())
