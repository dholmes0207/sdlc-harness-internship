#!/usr/bin/env python3
"""_wire_env.py — emit the global-install root env into settings.local.json.

A global install resolves its shared binary through $HARNESS_BIN_ROOT and its
per-project data home through $HARNESS_DATA_ROOT. The installer now passes an
explicit data_root for a global install: a hook subprocess gets CLAUDE_PROJECT_DIR
from Claude Code and could derive `.harness` from it, but a Bash-tool subprocess
does not carry CLAUDE_PROJECT_DIR at all — so the explicit env write is the only
tier that subprocess can read. `data_root` stays an optional parameter (the
`else` branch below still applies) for a caller that passes None explicitly —
today that is the direct `wire_env()` tests and per-project installs, which never
call this function. Those values are MACHINE-SPECIFIC absolute paths, so they
land in settings.local.json (never the committed settings.json — a committed bin
path breaks on the next machine). The portable hook wiring keeps its
$HARNESS_BIN_ROOT PLACEHOLDER in settings.json; only the resolved value lives
here.

Idempotent (mirrors merge_hooks): re-running overwrites the two keys and leaves
every other env entry untouched. Env is bound at session start, so a fresh wire
needs a session RESTART to take effect — the caller surfaces that note.
"""
from pathlib import Path

from _settings import _load_settings, _settings_path, _write_settings

RESTART_NOTE = (
    "HARNESS_BIN_ROOT/HARNESS_DATA_ROOT are env-bound — restart the Claude Code "
    "session for the global layout to take effect."
)


def wire_env(target_root, *, bin_root: str, data_root=None, dry_run: bool = False) -> dict:
    """Write HARNESS_BIN_ROOT (+ optional HARNESS_DATA_ROOT) into the target's
    settings.local.json `env` block. Returns the env block that was written
    (for dry-run preview). Preserves any existing env keys."""
    path = _settings_path(Path(target_root), local=True)
    settings = _load_settings(path)
    env = dict(settings.get("env") or {})
    env["HARNESS_BIN_ROOT"] = str(bin_root)
    if data_root:
        env["HARNESS_DATA_ROOT"] = str(data_root)
    else:
        # caller passed None explicitly (a direct wire_env() test, or a
        # per-project caller — per-project never calls this function today):
        # drop any stale key rather than leave a prior run's value behind.
        env.pop("HARNESS_DATA_ROOT", None)
    settings["env"] = env
    _write_settings(path, settings, dry_run)
    return env
