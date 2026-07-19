"""test_pending_decisions_resurface.py — SessionStart hook that re-surfaces the
most-recent still-open AskUserQuestion after a compaction.

Contract:
  - nudge-class, fail-open: never raises, never exits 2.
  - gate HARD on source=="compact" — any other source (startup/resume/clear) is
    silent, even with a pending question (re-surface is a compaction repair only).
  - on source=="compact", call transcript_questions.last_unresolved_question; a
    dict -> inject an additionalContext nudge naming the question + why; None ->
    silent.
  - a missing transcript / a raising extractor -> silent (fail-open), not a crash.

Driven as a subprocess (the real stdin/stdout contract). The hook is enabled via a
scratch HARNESS_HOOK_CONFIG (nudge class is OFF by default); transcript_path points
at a fixture jsonl built with the real CC v2.1.195 AUQ shape.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_HOOKS.parent / "scripts"))


# ----------------------------------------------------------------- fixtures ---

def _auq_record(tool_use_id, question, labels):
    return {"type": "assistant", "message": {"role": "assistant", "content": [
        {"type": "tool_use", "name": "AskUserQuestion", "id": tool_use_id,
         "input": {"questions": [{"question": question, "header": "H",
                   "multiSelect": False,
                   "options": [{"label": l, "description": "d"} for l in labels]}]}}]}}


def _answer_record(tool_use_id, question, answer):
    return {"type": "user", "message": {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": tool_use_id, "content": "answered"}]},
        "toolUseResult": {"answers": {question: answer}}}


def _write_transcript(tmp_path, records):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(p)


def _config(tmp_path, enabled=True):
    import yaml
    p = tmp_path / "harness-hooks.yaml"
    p.write_text(yaml.safe_dump({"hooks": {"pending_decisions_resurface": {"enabled": enabled}}}),
                 encoding="utf-8")
    return p


def _env(tmp_path, enabled=True):
    env = dict(os.environ)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_HOOK_AUDIT_DISABLED"] = "1"
    env["HARNESS_HOOK_CONFIG"] = str(_config(tmp_path, enabled))
    env.pop("PYTEST_CURRENT_TEST", None)
    return env


def _run(tmp_path, payload, enabled=True):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "pending_decisions_resurface.py")],
        input=json.dumps(payload), capture_output=True, text=True,
        env=_env(tmp_path, enabled),
    )
    assert proc.returncode == 0, proc.stderr
    return proc


def _ctx(proc):
    out = json.loads(proc.stdout)
    hs = out.get("hookSpecificOutput") or {}
    return hs.get("additionalContext", "")


# ------------------------------------------------------- 1. compact + unanswered ---

def test_compact_unanswered_injects_nudge(tmp_path):
    t = _write_transcript(tmp_path, [_auq_record("u1", "Pick a branch?", ["A", "B"])])
    ctx = _ctx(_run(tmp_path, {"source": "compact", "transcript_path": t}))
    assert "Pick a branch?" in ctx
    assert "CHƯA trả lời" in ctx
    assert "A | B" in ctx          # options surfaced


# ------------------------------------------------------- 2. compact + free_text ---

def test_compact_free_text_injects_nudge(tmp_path):
    typed = "let's do A but also preserve voice"
    t = _write_transcript(tmp_path, [
        _auq_record("u1", "Pick a branch?", ["A", "B"]),
        _answer_record("u1", "Pick a branch?", typed)])
    ctx = _ctx(_run(tmp_path, {"source": "compact", "transcript_path": t}))
    assert "Pick a branch?" in ctx
    assert typed in ctx
    assert "tự-do" in ctx


# ------------------------------------------------- 3. compact + clean pick -> silent ---

def test_compact_clean_pick_is_silent(tmp_path):
    t = _write_transcript(tmp_path, [
        _auq_record("u1", "Pick a branch?", ["A", "B"]),
        _answer_record("u1", "Pick a branch?", "A")])
    assert _ctx(_run(tmp_path, {"source": "compact", "transcript_path": t})) == ""


# ------------------------------------------------- 4. startup gate (source != compact) ---

def test_startup_is_silent_even_with_pending(tmp_path):
    t = _write_transcript(tmp_path, [_auq_record("u1", "Pick a branch?", ["A", "B"])])
    assert _ctx(_run(tmp_path, {"source": "startup", "transcript_path": t})) == ""


def test_resume_and_clear_are_silent(tmp_path):
    t = _write_transcript(tmp_path, [_auq_record("u1", "Pick a branch?", ["A", "B"])])
    for src in ("resume", "clear"):
        assert _ctx(_run(tmp_path, {"source": src, "transcript_path": t})) == ""


# ------------------------------------------------------------ 5. fail-open ---

def test_missing_transcript_is_silent_no_raise(tmp_path):
    proc = _run(tmp_path, {"source": "compact"})  # no transcript_path
    assert _ctx(proc) == ""


def test_garbage_stdin_never_exits_2(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "pending_decisions_resurface.py")],
        input="not json at all", capture_output=True, text=True,
        env=_env(tmp_path),
    )
    assert proc.returncode == 0


def test_extractor_raise_is_silent(monkeypatch, tmp_path):
    # In-process: force the extractor to raise; the hook must swallow it (fail-open).
    import pending_decisions_resurface as hook

    def _boom(_p):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(hook.transcript_questions, "last_unresolved_question", _boom)
    monkeypatch.setenv("HARNESS_HOOK_AUDIT_DISABLED", "1")
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(_config(tmp_path)))
    # must not raise
    hook.run(raw=json.dumps({"source": "compact", "transcript_path": "/x"}))


# ------------------------------------------------------- 6. disabled -> silent ---

def test_disabled_in_config_is_silent(tmp_path):
    t = _write_transcript(tmp_path, [_auq_record("u1", "Pick a branch?", ["A", "B"])])
    ctx = _ctx(_run(tmp_path, {"source": "compact", "transcript_path": t}, enabled=False))
    assert ctx == ""
