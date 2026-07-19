"""Regression guard: the hooks-config cache must not leak a scratch config across tests.

hook_runtime memoizes the parsed hooks config in a module-level global. A test that points
HARNESS_HOOK_CONFIG at a scratch yaml and reads a hook's state poisons that cache; monkeypatch
restores the env but NOT the cache, so a later test asserting a SHIPPED hook default reads the
stale scratch and reds — order-dependent under xdist loadfile packing. The autouse
`_reset_hook_runtime_caches` fixture (conftest) clears the cache around every test.

These two tests encode the invariant in definition order (pytest preserves it within a file,
and loadfile keeps a file on one worker): the first poisons the cache, the second asserts a
shipped default still reads true — which only holds if the fixture cleared the cache between
them.
"""
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hook_runtime as hr  # noqa: E402


def test_1_poison_config_cache_with_scratch(monkeypatch, tmp_path):
    cfg = tmp_path / "scratch-hooks.yaml"
    cfg.write_text("hooks:\n  scratch_only_nudge: {enabled: true}\n", encoding="utf-8")
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    hr._reset_config_cache()
    # reads the scratch config, populating the module cache with it
    assert hr.hook_enabled("scratch_only_nudge", "nudge") is True
    # deliberately NO teardown reset here — the autouse fixture must clean up.


def test_2_shipped_default_not_poisoned():
    # goal_cycle_nudge ships enabled:true; if test_1's scratch cache leaked, this reads False.
    assert hr.hook_enabled("goal_cycle_nudge", "nudge") is True
