"""ssot_live_hook_drift_problems must catch a flipped harness-hooks.yaml toggle
that never got wired into .claude/settings.json (H1, INV-3 F-1) — the exact gap
where glossary_pointer_inject / decision_reconcile_nudge shipped `enabled: true`
in the SSOT while missing from the live hooks tree, so the config claimed ON but
was actually dead. Advisory/fail-soft: no settings.json (a pre-install checkout)
or a missing/malformed SSOT must never crash verify_install or report drift.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import verify_install  # noqa: E402


def _write_ssot(root: Path, hooks: dict) -> None:
    lines = ["hooks:"]
    for name, enabled in hooks.items():
        lines.append("  %s: {enabled: %s}" % (name, "true" if enabled else "false"))
    (root / "harness" / "data").mkdir(parents=True, exist_ok=True)
    (root / "harness" / "data" / "harness-hooks.yaml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def _write_settings(root: Path, wired_names) -> None:
    hooks_tree = {
        "Stop": [{
            "matcher": None,
            "hooks": [
                {"type": "command",
                 "command": 'python3 "$CLAUDE_PROJECT_DIR"/harness/hooks/%s.py' % n}
                for n in wired_names
            ],
        }],
    }
    (root / ".claude").mkdir(parents=True, exist_ok=True)
    (root / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": hooks_tree}), encoding="utf-8")


def test_enabled_in_ssot_but_unwired_is_flagged(tmp_path):
    _write_ssot(tmp_path, {"decision_reconcile_nudge": True})
    _write_settings(tmp_path, [])  # not wired at all
    problems = verify_install.ssot_live_hook_drift_problems(tmp_path)
    assert any("decision_reconcile_nudge" in p and "config claims ON" in p
               for _, p in problems), problems


def test_disabled_in_ssot_but_wired_is_flagged(tmp_path):
    _write_ssot(tmp_path, {"gemini_stop_review_gate": False})
    _write_settings(tmp_path, ["gemini_stop_review_gate"])
    problems = verify_install.ssot_live_hook_drift_problems(tmp_path)
    assert any("gemini_stop_review_gate" in p and "not a bug" in p
               for _, p in problems), problems


def test_matching_state_is_silent(tmp_path):
    _write_ssot(tmp_path, {
        "glossary_pointer_inject": True,
        "cook_isolation_nudge": False,
    })
    _write_settings(tmp_path, ["glossary_pointer_inject"])
    assert verify_install.ssot_live_hook_drift_problems(tmp_path) == []


def test_no_settings_json_is_silent_not_crashing(tmp_path):
    _write_ssot(tmp_path, {"decision_reconcile_nudge": True})
    assert verify_install.ssot_live_hook_drift_problems(tmp_path) == []


def test_no_ssot_is_silent_not_crashing(tmp_path):
    _write_settings(tmp_path, ["decision_reconcile_nudge"])
    assert verify_install.ssot_live_hook_drift_problems(tmp_path) == []


def test_malformed_ssot_degrades_to_no_drift(tmp_path):
    (tmp_path / "harness" / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "harness" / "data" / "harness-hooks.yaml").write_text(
        "{not: yaml::", encoding="utf-8")
    _write_settings(tmp_path, [])
    assert verify_install.ssot_live_hook_drift_problems(tmp_path) == []


def test_class_default_hook_without_explicit_enabled_is_out_of_scope(tmp_path):
    # A hook resting on its class-default (no `enabled:` key at all in the
    # SSOT) is deliberately out of scope for this diff — only explicit
    # booleans are load-bearing here (see the function's own docstring).
    (tmp_path / "harness" / "data").mkdir(parents=True, exist_ok=True)
    (tmp_path / "harness" / "data" / "harness-hooks.yaml").write_text(
        "hooks:\n  some_class_default_hook: {}\n", encoding="utf-8")
    _write_settings(tmp_path, [])  # not wired, but never flagged (no explicit bool)
    assert verify_install.ssot_live_hook_drift_problems(tmp_path) == []


def _write_dispatch(root: Path, modules) -> None:
    (root / "harness" / "data").mkdir(parents=True, exist_ok=True)
    lines = ["groups:", '  "PreToolUse:Bash":']
    for m in modules:
        lines.append("    - {name: %s, module: %s, entry: core, class: compliance}" % (m, m))
    (root / "harness" / "data" / "hook-dispatch.yaml").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


def test_dispatcher_grouped_enabled_hook_is_not_flagged(tmp_path):
    # A hook that fires as a CORE of hook_dispatch.py (wired) — not by its own
    # command — must count as live, not "config claims ON, live is dead".
    _write_ssot(tmp_path, {"standards_drift_nudge": True})
    _write_settings(tmp_path, ["hook_dispatch"])  # dispatcher wired; hook not standalone
    _write_dispatch(tmp_path, ["standards_drift_nudge"])
    assert verify_install.ssot_live_hook_drift_problems(tmp_path) == []


def test_grouped_hook_still_flagged_when_dispatcher_unwired(tmp_path):
    # If hook_dispatch.py itself is NOT wired, its cores really are dead — flag it.
    _write_ssot(tmp_path, {"standards_drift_nudge": True})
    _write_settings(tmp_path, [])  # dispatcher NOT wired
    _write_dispatch(tmp_path, ["standards_drift_nudge"])
    problems = verify_install.ssot_live_hook_drift_problems(tmp_path)
    assert any("standards_drift_nudge" in p and "config claims ON" in p
               for _, p in problems), problems


def test_main_prints_drift_as_advisory_warn(tmp_path, capsys, monkeypatch):
    _write_ssot(tmp_path, {"decision_reconcile_nudge": True})
    _write_settings(tmp_path, [])
    (tmp_path / "harness").mkdir(exist_ok=True)
    (tmp_path / "harness" / "manifest.json").write_text(
        json.dumps({"files": {}}), encoding="utf-8")
    monkeypatch.delenv("HARNESS_BIN_ROOT", raising=False)
    rc = verify_install.main(["--root", str(tmp_path)])
    captured = capsys.readouterr()
    assert "decision_reconcile_nudge" in captured.err
    assert "WARN" in captured.err
    # advisory: never fails the run even without --strict escalation
    assert rc == 0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
