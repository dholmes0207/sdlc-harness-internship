#!/usr/bin/env python3
"""dev_override_wiring_nudge — warn when a `.harness-dev/<name>.yaml` override is
present but wired to no `HARNESS_*` env, so it is silently ignored (the loader
falls back to the shipped default).

A thin, deterministic wrapper around the `dev_override_wiring` detector: it owns
NO detection logic. On turn-end it asks "is there a dev override that no env
points to?"; if so it surfaces a ONE-line advisory (which file, how to wire it)
and records one observation, then ALWAYS allows. Never blocks, never writes.

Dev-only by construction: the detector returns None when `.harness-dev/` is
absent, so a shipped install never trips it. Class is `nudge` (default OFF):
asleep until enabled in harness-hooks.yaml — in this dev repo it is enabled via
the `.harness-dev/harness-hooks.yaml` override.

Channel intent (nudge-channels.yaml): DEV surfaces to BOTH the human and the
model (a dev wants to see + act on the gap); a shipped install would reach only
the model (model-only) — though in ship the detector never fires anyway.

Visible-degradation: if the detector cannot be imported, emits a
`dev_override_wiring_degraded` audit event first, then allows.
"""
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import hook_runtime  # noqa: E402
import trace_log     # noqa: E402

HOOK_CLASS = "nudge"
NAME = "dev_override_wiring_nudge"
ALLOW_EXIT = 0


def _import_detector():
    sd = str(Path(__file__).resolve().parent.parent / "scripts")
    if sd not in sys.path:
        sys.path.insert(0, sd)
    import dev_override_wiring  # noqa: E402 — resolved after the path insert
    return dev_override_wiring


def _project_dir(stdin_cwd: Optional[str] = None) -> Optional[str]:
    return hook_runtime.project_dir(stdin_cwd)


def _enabled() -> bool:
    try:
        return hook_runtime.hook_enabled(NAME, HOOK_CLASS)
    except Exception:  # noqa: BLE001
        return False


def _advisory_text(signal: Dict[str, Any]) -> str:
    unwired = signal.get("unwired") or []
    files = ", ".join(unwired)
    return ("dev-override: %d file(s) in .harness-dev/ are wired to no HARNESS_* "
            "env, so they are SILENTLY IGNORED (the loader uses the shipped "
            "default): %s. Point a HARNESS_<NAME> at each in "
            ".claude/settings.local.json and restart. Advisory only."
            % (len(unwired), files))


def _observation_note(signal: Dict[str, Any]) -> str:
    unwired = signal.get("unwired") or []
    return "dev_override_unwired×%d — %s" % (len(unwired), ", ".join(unwired))


def _trace_degraded(actor: str, session_id: str, exc: Exception) -> None:
    try:
        trace_log.append_event(hook=NAME, event="dev_override_wiring_degraded",
                               actor=actor, session=session_id,
                               status="degraded", note=str(exc)[:200])
    except Exception:  # noqa: BLE001
        pass


def _record_observation(actor: str, session_id: str, signal: Dict[str, Any]) -> None:
    try:
        trace_log.append_event(hook=NAME, event="dev_override_wiring_observation",
                               actor=actor, session=session_id,
                               status="observed", note=_observation_note(signal))
    except Exception:  # noqa: BLE001
        pass


def handle_stop(payload: Dict[str, Any], project_dir: Optional[str] = None) -> int:
    if not _enabled():
        return ALLOW_EXIT
    project_dir = project_dir or _project_dir(payload.get("cwd"))
    if not project_dir:
        return ALLOW_EXIT

    session_id = hook_runtime.safe_session_id(payload.get("session_id"))
    actor = hook_runtime.resolve_actor(session_id)

    try:
        detector = _import_detector()
    except ImportError as exc:
        _trace_degraded(actor, session_id, exc)
        return ALLOW_EXIT

    try:
        signal = detector.collect(project_dir)
    except Exception as e:  # noqa: BLE001 — advisory: never break turn-end
        hook_runtime.log_hook_error(NAME, e)
        return ALLOW_EXIT

    if signal:
        hook_runtime.route_relay_nudge(
            NAME, _advisory_text(signal),
            lambda: _record_observation(actor, session_id, signal))
    return ALLOW_EXIT


def main(argv: Optional[List[str]] = None) -> int:
    payload = hook_runtime.read_stdin_json()
    project_dir = _project_dir(payload.get("cwd"))
    try:
        rc = handle_stop(payload, project_dir)
    except Exception as e:  # noqa: BLE001
        try:
            hook_runtime.log_hook_error(NAME, e)
        except Exception:
            pass
        rc = ALLOW_EXIT
    hook_runtime.drain_or_continue()
    return rc


if __name__ == "__main__":
    sys.exit(main())
