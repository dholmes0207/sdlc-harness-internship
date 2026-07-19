"""test_setup_nudge.py — SessionStart advisory that injects two onboarding
nudges as additionalContext (telemetry-class, fail-open):

1. RESTART reminder: guard/stage posture is env-bound (HARNESS_GUARD_POLICY
   / HARNESS_STAGE_POLICY). When .claude/settings.json wires those env vars but the
   RUNNING session's environment doesn't match, the session predates the wiring —
   the gate is reading stale posture until the user restarts. Terminal voice is
   file-discovered (no restart) so it is deliberately NOT checked here.
2. SETUP reminder: an empty reviewer roster means the approval gate cannot work →
   point the user at /hs:setup.

Emits nothing when there is nothing to nudge (a configured, freshly-restarted
session is silent).
"""
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import setup_nudge as n  # noqa: E402


# --- stale-env detection (pure) -----------------------------------------------

def test_stale_when_settings_env_absent_from_environ():
    settings_env = {"HARNESS_GUARD_POLICY": "/x/guard.yaml",
                    "HARNESS_STAGE_POLICY": "/x/stage.yaml"}
    stale = n.stale_posture_keys(settings_env, environ={})
    assert set(stale) == {"HARNESS_GUARD_POLICY", "HARNESS_STAGE_POLICY"}


def test_not_stale_when_environ_matches():
    settings_env = {"HARNESS_GUARD_POLICY": "/x/guard.yaml"}
    stale = n.stale_posture_keys(
        settings_env, environ={"HARNESS_GUARD_POLICY": "/x/guard.yaml"})
    assert stale == []


def test_stale_when_value_differs():
    settings_env = {"HARNESS_STAGE_POLICY": "/new/stage.yaml"}
    stale = n.stale_posture_keys(
        settings_env, environ={"HARNESS_STAGE_POLICY": "/old/stage.yaml"})
    assert stale == ["HARNESS_STAGE_POLICY"]


def test_voice_env_is_not_a_restart_key():
    # terminal voice is file-discovered → a mismatch there must NOT nudge restart
    settings_env = {"HARNESS_TERMINAL_VOICE": "/x/voice.yaml"}
    assert n.stale_posture_keys(settings_env, environ={}) == []


# --- message assembly (pure) --------------------------------------------------

def test_build_nudge_restart_message():
    text = n.build_nudge(["HARNESS_GUARD_POLICY"], roster_unset=False)
    assert text and "restart" in text.lower()
    assert "HARNESS_GUARD_POLICY" in text


def test_build_nudge_setup_message():
    text = n.build_nudge([], roster_unset=True)
    assert text and "/hs:setup" in text


def test_build_nudge_none_when_clean():
    assert n.build_nudge([], roster_unset=False) is None


# --- missing-standards detection (mirrors installer _check_standards) ----------

def test_standards_missing_lists_absent(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "code-standards.md").write_text("x" * 60 + "\n")
    # system-architecture.md absent entirely
    assert n.standards_missing(tmp_path) == ["system-architecture.md"]


def test_standards_missing_counts_thin(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "code-standards.md").write_text("x")  # < 40 chars => thin
    (tmp_path / "docs" / "system-architecture.md").write_text("y" * 60 + "\n")
    assert n.standards_missing(tmp_path) == ["code-standards.md"]


def test_standards_missing_none_when_present(tmp_path):
    (tmp_path / "docs").mkdir()
    for f in ("code-standards.md", "system-architecture.md"):
        (tmp_path / "docs" / f).write_text("z" * 60 + "\n")
    assert n.standards_missing(tmp_path) == []


def test_build_nudge_standards_message():
    text = n.build_nudge([], roster_unset=False,
                         standards_missing=["code-standards.md"])
    assert text and "/hs:docs" in text and "code-standards.md" in text


# --- run() integration: emits additionalContext only when there's a nudge -----

def _ctx(out: str) -> str:
    return (json.loads(out).get("hookSpecificOutput") or {}).get("additionalContext", "")


def test_run_emits_context_when_stale(tmp_path, monkeypatch, capsys):
    # settings.json wires a posture env the process env lacks → stale → nudge
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps(
        {"env": {"HARNESS_GUARD_POLICY": "/x/guard.yaml"}}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.delenv("HARNESS_GUARD_POLICY", raising=False)
    n.run(raw=json.dumps({"hook_event_name": "SessionStart"}))
    assert "restart" in _ctx(capsys.readouterr().out).lower()


def test_run_silent_when_nothing_to_nudge(tmp_path, monkeypatch, capsys):
    claude = tmp_path / ".claude"
    claude.mkdir()
    (claude / "settings.json").write_text(json.dumps({"env": {}}), encoding="utf-8")
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    # roster set + standards present so every nudge stays silent
    monkeypatch.setattr(n, "roster_unset", lambda root: False)
    monkeypatch.setattr(n, "standards_missing", lambda root: [])
    n.run(raw=json.dumps({"hook_event_name": "SessionStart"}))
    out = capsys.readouterr().out
    assert _ctx(out) == ""        # no additionalContext
    assert json.loads(out).get("continue") is True


def test_run_never_raises_on_junk(tmp_path, monkeypatch, capsys):
    # point at an empty project dir so the output depends only on fail-open
    # behavior, not on this repo's real settings.json / roster / standards
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))
    monkeypatch.setattr(n, "standards_missing", lambda root: [])
    n.run(raw="not json")
    # fail-open: a continue is emitted, no exception escapes
    assert json.loads(capsys.readouterr().out).get("continue") is True
