#!/usr/bin/env python3
"""dev_override_wiring — detector for a `.harness-dev/<name>.yaml` override that
NO `HARNESS_*` env points to, so the loader silently falls back to the shipped
default and the override does nothing.

This is the "I made a dev override but forgot to wire its env" failure mode:
most localized configs resolve `explicit --config > $HARNESS_XXX > shipped`, so
the dev file is inert until an explicit `HARNESS_XXX` env in
`.claude/settings.local.json` points at it (a restart then applies it). Auto-
discovery is deliberately NOT done for the caged/security-relevant seams (an
agent could widen its own cage via a writeable-zone file), so the wiring is
manual — and therefore forgettable. This detector catches the gap; it owns no
side effects and never writes anything.

Dev-only by construction: it returns None (silent) when `.harness-dev/` is
absent, so a shipped install never trips it.
"""
import json
import os
from pathlib import Path
from typing import Optional

# Dev override files consumed WITHOUT a HARNESS_* env — absence of an env is
# correct for these, not a wiring bug:
#   dev-off-skills.yaml — read directly by dev_skill_farm.py (the farm off-list)
#   terminal-voice.yaml — voice_prefs.py AUTO-DISCOVERS it at the repo-root path
#                         (cosmetic config, safe to auto-load; the caged seams
#                         are the ones that must stay env-explicit)
_NO_ENV_NEEDED = frozenset({"dev-off-skills.yaml", "terminal-voice.yaml"})


def _env_sources(project: Path):
    """The env maps to scan for HARNESS_* wiring, most-authoritative first:
    settings.local.json (the persistent per-project surface — reflects a wiring
    added but not yet restarted), then the live process env (a wiring exported
    by any other mechanism)."""
    sources = []
    sl = project / ".claude" / "settings.local.json"
    try:
        data = json.loads(sl.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("env"), dict):
            sources.append(data["env"])
    except (OSError, ValueError):
        pass
    sources.append(dict(os.environ))
    return sources


def _wired_names(project: Path, dev: Path):
    """Basenames of dev-override files that some HARNESS_* env value resolves
    to (i.e. are actually wired)."""
    wired = set()
    for env in _env_sources(project):
        for key, val in env.items():
            if not (isinstance(key, str) and key.startswith("HARNESS_")
                    and isinstance(val, str) and val):
                continue
            try:
                p = Path(val)
                p = p.resolve() if p.is_absolute() else (project / p).resolve()
            except (OSError, RuntimeError, ValueError):
                continue
            if p.parent == dev:
                wired.add(p.name)
    return wired


def collect(project_dir: Optional[str] = None) -> Optional[dict]:
    """Return `{"unwired": [<name>.yaml, ...]}` for dev overrides present but
    wired to no HARNESS_* env, else None. Fail-soft: any error or a non-dev
    tree returns None (the nudge stays silent) rather than raising."""
    try:
        project = Path(project_dir).resolve() if project_dir else Path.cwd()
        dev = project / ".harness-dev"
        if not dev.is_dir():
            return None
        present = {f.name for f in dev.glob("*.yaml")}
        unwired = sorted(present - _wired_names(project, dev) - _NO_ENV_NEEDED)
        return {"unwired": unwired} if unwired else None
    except (OSError, RuntimeError):
        return None
