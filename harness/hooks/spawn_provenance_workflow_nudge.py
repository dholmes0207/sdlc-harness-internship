#!/usr/bin/env python3
"""spawn_provenance_workflow_nudge.py — PreToolUse(Workflow) advisory: a
wide un-planned Workflow fan-out (Ask-3 Layer-2 advisory twin, `nudge`
class, fail-open SILENT).

Parity with `spawn_provenance_nudge.py` (the Agent|Task Layer-1a advisory),
but reads the Workflow tool's OWN declared width instead of a cumulative
session count: `spawn_provenance.workflow_width(tool_input.script)` — a
STATIC PARSE of the script text, never an execution (WP1-verified,
plans/260715-0021-subagent-spawn-guards-and-reinject/probes/
WP1-workflow-pretooluse.md). Fires when that width exceeds
`spawn_provenance.threshold()` with no orchestrate strategy token on
record for the session (`spawn_provenance.has_orchestrate_token`),
recommending `hs:workflow-orchestrate`.

No counter to update: a Workflow call is self-contained per invocation, so
unlike the Agent|Task nudge this never calls `record_spawn`.

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


def core(data: dict):
    """Return the advisory string when this Workflow's declared width
    exceeds the threshold with no orchestrate token on record, else None.
    Never records anything (no cumulative counter for this lane)."""
    if not isinstance(data, dict) or data.get("tool_name") != "Workflow":
        return None
    session = data.get("session_id") or ""
    tool_input = data.get("tool_input")
    script = tool_input.get("script") if isinstance(tool_input, dict) else None
    width = spawn_provenance.workflow_width(script)
    limit = spawn_provenance.threshold()
    fires = width > limit and not spawn_provenance.has_orchestrate_token(session)
    if not fires:
        return None
    return (
        "[nudge] spawn_provenance: this Workflow call declares a fan-out "
        "width of ~%d (static script-parse estimate) with no recorded "
        "orchestrate strategy — consider hs:workflow-orchestrate to size "
        "and group the fan-out before running it. Advisory, non-blocking.\n"
        % width
    )


def main() -> int:
    # Nudge structure mirrors spawn_provenance_nudge: honor the config gate,
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
