"""H1-resolved (plans/260709-1514-cc-docs-standardization/DECISIONS.md): the ship
default for the 5 hooks INV-3 found live-unwired (F-1). Ship default in
harness/data/harness-hooks.yaml:
  glossary_pointer_inject=ON, glossary_capture_nudge=ON,
  decision_reconcile_nudge=ON, gemini_stop_review_gate=OFF (+ explicit
  `timeout` field for whenever it IS wired — an external-engine call at Stop
  must not hang an interactive turn on the CC spec default of 600s).

RED until harness/data/harness-hooks.yaml is caged (write_guard GUARD_LIST) —
the patch is staged at
plans/260709-1514-cc-docs-standardization/artifacts/h1-harness-hooks-patch.yaml
for main/owner to apply (a subagent cannot Edit this file directly).
"""
import sys
from pathlib import Path

import yaml

_REPO = Path(__file__).resolve().parents[2]
_SHIPPED = _REPO / "harness" / "data" / "harness-hooks.yaml"
_HOOKS_DIR = _REPO / "harness" / "hooks"

if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))
import hook_runtime  # noqa: E402


def _hooks() -> dict:
    return (yaml.safe_load(_SHIPPED.read_text(encoding="utf-8")) or {}).get("hooks") or {}


def test_glossary_pointer_inject_on():
    assert _hooks().get("glossary_pointer_inject", {}).get("enabled") is True


def test_glossary_capture_nudge_on():
    assert _hooks().get("glossary_capture_nudge", {}).get("enabled") is True


def test_decision_reconcile_nudge_on():
    assert _hooks().get("decision_reconcile_nudge", {}).get("enabled") is True


def test_gemini_stop_review_gate_off():
    assert _hooks().get("gemini_stop_review_gate", {}).get("enabled") is False


def test_gemini_stop_review_gate_has_explicit_timeout():
    # H1: a gate that ever wires to Stop must not inherit the CC spec's 600s
    # default for an external-engine call — the SSOT carries the value the
    # installer copies into the .claude/settings.json matcher entry.
    entry = _hooks().get("gemini_stop_review_gate", {})
    assert isinstance(entry.get("timeout"), (int, float))
    assert 0 < entry["timeout"] <= 60


def test_runtime_agrees_glossary_capture_enabled(monkeypatch):
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(_SHIPPED))
    hook_runtime._reset_config_cache()
    try:
        assert hook_runtime.hook_enabled("glossary_capture_nudge", "nudge") is True
    finally:
        hook_runtime._reset_config_cache()


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
