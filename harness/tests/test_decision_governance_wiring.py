"""P6 wiring co-presence: the decision-flip governance pieces must be registered
where their consumers look — a producer wired in one file but not its twin is the
classic silent-starve. String/structure presence only (behavior is tested in the
unit suites)."""
import json
import pytest
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent.parent


def test_reconcile_nudge_registered():
    # enabled key in harness-hooks.yaml
    hooks = yaml.safe_load((_ROOT / "harness" / "data" / "harness-hooks.yaml")
                           .read_text(encoding="utf-8"))
    assert "decision_reconcile_nudge" in (hooks.get("hooks") or {})
    # migrated into the in-process dispatcher: it fires as a Stop core of hook_dispatch.py,
    # registered in hook-dispatch.yaml under the Stop group rather than its own command.
    disp = yaml.safe_load(
        (_ROOT / "harness" / "data" / "hook-dispatch.yaml").read_text(encoding="utf-8"))
    stop_mods = {c.get("module") for c in disp["groups"].get("Stop", [])}
    assert "decision_reconcile_nudge" in stop_mods


def test_remember_skill_documents_flip_flow():
    text = (_ROOT / "harness" / "plugins" / "hs" / "skills" / "remember"
            / "SKILL.md").read_text(encoding="utf-8")
    assert "--scan-flip" in text
    assert "decision_confirm" in text
    assert "AskUserQuestion" in text


@pytest.mark.dev_repo
def test_release_skill_documents_reconcile_gate():
    text = (_ROOT / "harness" / "plugins" / "hs" / "skills" / "release"
            / "SKILL.md").read_text(encoding="utf-8")
    assert "reconcile" in text.lower()


def test_config_reference_lists_governance():
    text = (_ROOT / "docs" / "harness" / "config-reference.md") \
        .read_text(encoding="utf-8")
    assert "decision-governance.yaml" in text
    assert "decision_reconcile_nudge" in text


def test_decision_reconciler_in_manifest():
    files = json.loads((_ROOT / "harness" / "manifest.json")
                       .read_text(encoding="utf-8"))["files"]
    assert "harness/plugins/hs/agents/decision-reconciler.md" in files
    # the new scripts + nudge + data file are tracked too
    for p in ("harness/scripts/decision_neighbors.py",
              "harness/scripts/decision_confirm.py",
              "harness/scripts/decision_reconcile.py",
              "harness/hooks/decision_reconcile_nudge.py",
              "harness/data/decision-governance.yaml"):
        assert p in files, p


if __name__ == "__main__":
    import sys
    sys.exit(__import__("pytest").main([__file__, "-q"]))
