#!/usr/bin/env python3
"""Default-off contract for the 3 low-value nudges (cook_isolation, discover_isolation,
rule_nudge). Locks the shipped + dev config so a future re-enable is a visible test break,
and proves the toggle is honored through the real runtime path (not just YAML text)."""

import subprocess
import pytest
import sys
from pathlib import Path

import yaml as _yaml

_REPO = Path(__file__).resolve().parents[2]
_SHIPPED = _REPO / "harness" / "data" / "harness-hooks.yaml"
_DEV = _REPO / ".harness-dev" / "harness-hooks.yaml"
_HOOKS_DIR = _REPO / "harness" / "hooks"

if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
import hook_runtime  # noqa: E402

_OFF_KEYS = ("cook_isolation_nudge", "discover_isolation_nudge", "rule_nudge_hook",
             "scout_heavy_dir_nudge")  # scout_heavy_dir: stderr-only channel reaches no
             # reader — turned off (kept WIRED, not removed) so it stops paying a hook
             # spawn on every Read/Grep/Glob for an advisory nobody sees.
# Nudges that must stay ON after the diet — regression guard against over-reach.
_KEEP_ON = (
    "memory_gap_hook", "backlog_capture_nudge", "backlog_hygiene_nudge",
    "goal_cycle_nudge",
)


def _hooks(path: Path) -> dict:
    return (_yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("hooks") or {}


# T1 — shipped: the 3 target nudges are explicitly default-off.
def test_shipped_three_nudges_off():
    hooks = _hooks(_SHIPPED)
    for k in _OFF_KEYS:
        assert hooks.get(k, {}).get("enabled") is False, k


# T2 — shipped: the value-carrying nudges are untouched (no collateral).
def test_shipped_keep_on_unchanged():
    hooks = _hooks(_SHIPPED)
    for k in _KEEP_ON:
        assert hooks.get(k, {}).get("enabled") is True, k


# T3 — mutation_guard (safety-adjacent) is not swept off with the friction nudges.
def test_mutation_guard_stays_on():
    assert _hooks(_SHIPPED).get("mutation_guard", {}).get("enabled") is True


# T4 — the toggle is real: resolved through hook_runtime against the shipped file.
def test_runtime_reports_disabled(monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(_SHIPPED))
    monkeypatch.delenv("HARNESS_RULE_NUDGE", raising=False)
    hook_runtime._reset_config_cache()
    try:
        for k in _OFF_KEYS:
            assert hook_runtime.hook_enabled(k, "nudge") is False, k
    finally:
        hook_runtime._reset_config_cache()


# T5 — the dev override mirrors the shipped default so this session goes quiet now.
@pytest.mark.dev_repo
def test_dev_override_three_nudges_off():
    if not _DEV.exists():
        # .harness-dev/ is this dev's gitignored personal config — absent from a bare
        # git checkout / CI runner. The override only exists to mirror the shipped
        # default, so with no override there is nothing to assert here.
        pytest.skip(".harness-dev/harness-hooks.yaml not present in this checkout")
    hooks = _hooks(_DEV)
    for k in _OFF_KEYS:
        assert hooks.get(k, {}).get("enabled") is False, k


# T6 — a disabled nudge is inert end-to-end: given a cook trigger it emits no
# advisory (/clear) on stderr and still continues (exit 0). The hook always emits
# the {"continue": true} contract on stdout by design — "not firing" is the
# absence of the advisory, not empty stdout.
def test_disabled_nudge_does_not_fire(tmp_path):
    env = {
        "HARNESS_HOOK_CONFIG": str(_SHIPPED),
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(_HOOKS_DIR),
    }
    payload = '{"tool_name":"Skill","tool_input":{"skill":"hs:cook"},"session_id":"S1"}'
    r = subprocess.run(
        [sys.executable, str(_HOOKS_DIR / "cook_isolation_nudge.py")],
        input=payload, capture_output=True, text=True, env=env, timeout=20,
    )
    assert r.returncode == 0
    assert "/clear" not in r.stderr
