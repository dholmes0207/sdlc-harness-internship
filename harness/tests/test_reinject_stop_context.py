"""reinject_stop_context — re-inject working context on a /goal-style continuation.

The hook re-emits inject_prompt_context.build_slim_context() via a Stop `decision: block` + `reason`
ONLY when the Stop is a continuation (stop_hook_active), and fails open otherwise.
(That the block+reason reaches the model and re-invokes it is probe-verified on CC 2.1.201 — these tests pin the hook's own contract.)
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "harness" / "hooks"))
sys.path.insert(0, str(ROOT / "harness" / "scripts"))
import reinject_stop_context as r  # noqa: E402
import inject_prompt_context  # noqa: E402


def test_continuation_emits_context(monkeypatch, capsys, tmp_path):
    monkeypatch.setenv("HARNESS_REINJECT_STOP", "1")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    # the goal-active gate now requires a live /goal (last goal_status met:false)
    tp = _write_transcript(tmp_path, _gs_line(False))
    r.run(raw=_raw_stop(transcript_path=tp))
    d = json.loads(capsys.readouterr().out)
    assert d["decision"] == "block"
    assert d["reason"] == "VOICE+RULES"


def test_continuation_carries_pending_nudge_context(monkeypatch, capsys, tmp_path):
    # In a /goal loop UserPromptSubmit never fires, so the nudge additionalContext
    # channel is dead — reinject must carry pending nudge observations in its reason
    # so the MODEL (not just the human systemMessage) receives them.
    import nudge_context_inject
    monkeypatch.setenv("HARNESS_REINJECT_STOP", "1")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(nudge_context_inject, "core", lambda data: "NUDGE: run /hs:remember")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    tp = _write_transcript(tmp_path, _gs_line(False))
    r.run(raw=_raw_stop(transcript_path=tp))
    d = json.loads(capsys.readouterr().out)
    assert d["decision"] == "block"
    assert "VOICE+RULES" in d["reason"]
    assert "NUDGE: run /hs:remember" in d["reason"]


def test_nudge_resurface_failure_does_not_break_reinject(monkeypatch, capsys, tmp_path):
    # A broken nudge resurface must never kill the re-injection (fail-open): the
    # base context still emits.
    import nudge_context_inject

    def boom(data):
        raise ValueError("nudge boom")

    monkeypatch.setenv("HARNESS_REINJECT_STOP", "1")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(nudge_context_inject, "core", boom)
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    tp = _write_transcript(tmp_path, _gs_line(False))
    r.run(raw=_raw_stop(transcript_path=tp))
    d = json.loads(capsys.readouterr().out)
    assert d["reason"] == "VOICE+RULES"


def test_normal_stop_emits_no_context(monkeypatch, capsys):
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    r.run(raw='{"hook_event_name":"Stop","stop_hook_active":false}')
    assert '"decision"' not in capsys.readouterr().out


def test_disabled_emits_no_context(monkeypatch, capsys):
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: False)
    r.run(raw='{"hook_event_name":"Stop","stop_hook_active":true}')
    assert '"decision"' not in capsys.readouterr().out


def test_build_error_fails_open(monkeypatch, capsys, tmp_path):
    def boom(root):
        raise ValueError("boom")

    monkeypatch.setenv("HARNESS_REINJECT_STOP", "1")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", boom)
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    # marker met:false clears the gate so build_slim_context is reached, then throws
    tp = _write_transcript(tmp_path, _gs_line(False))
    # must not raise — re-injection never breaks the loop
    r.run(raw=_raw_stop(transcript_path=tp))
    assert '"decision"' not in capsys.readouterr().out


def test_default_on_when_unset(monkeypatch, capsys, tmp_path):
    # default-ON: env unset + continuation + live /goal (met:false) -> emit
    monkeypatch.delenv("HARNESS_REINJECT_STOP", raising=False)
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    tp = _write_transcript(tmp_path, _gs_line(False))
    r.run(raw=_raw_stop(transcript_path=tp))
    d = json.loads(capsys.readouterr().out)
    assert d["reason"] == "VOICE+RULES"


def test_explicit_disable_zero(monkeypatch, capsys, tmp_path):
    # opt-out: HARNESS_REINJECT_STOP=0 silences even a live /goal continuation
    monkeypatch.setenv("HARNESS_REINJECT_STOP", "0")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    tp = _write_transcript(tmp_path, _gs_line(False))
    r.run(raw=_raw_stop(transcript_path=tp))
    assert '"decision"' not in capsys.readouterr().out


def test_disable_zero_overrides_goal_alive(monkeypatch, capsys, tmp_path):
    # precedence: =0 hard-disable wins over an active-unmet /goal
    monkeypatch.setenv("HARNESS_REINJECT_STOP", "0")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    tp = _write_transcript(tmp_path, _gs_line(False), _gs_line(False))
    r.run(raw=_raw_stop(transcript_path=tp))
    assert '"decision"' not in capsys.readouterr().out


def test_goal_met_silent_regardless_of_env(monkeypatch, capsys, tmp_path):
    # the goal gate sits above the env switch: met:true is silent even default-ON
    monkeypatch.delenv("HARNESS_REINJECT_STOP", raising=False)
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    tp = _write_transcript(tmp_path, _gs_line(False), _gs_line(True))
    r.run(raw=_raw_stop(transcript_path=tp))
    assert '"decision"' not in capsys.readouterr().out


# --- _last_goal_status tail-read helper (goal-active gate) -------------------
# Marker shape pinned from a real CC v2.1.195 transcript (6da86acc-...jsonl):
# a jsonl record carries a top-level `attachment` field
#   {"type":"goal_status","met":<bool>,"sentinel":<bool?>,"condition":<str>}.

def _gs_line(met, sentinel=None, condition="x"):
    att = {"type": "goal_status", "met": met, "condition": condition}
    if sentinel is not None:
        att["sentinel"] = sentinel
    return json.dumps({"timestamp": "2026-06-25T17:46:15Z", "attachment": att})


def _write_transcript(tmp_path, *lines):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(lines) + "\n")
    return str(p)


def _raw_stop(stop_hook_active=True, transcript_path=None):
    payload = {"hook_event_name": "Stop", "stop_hook_active": stop_hook_active}
    if transcript_path is not None:
        payload["transcript_path"] = transcript_path
    return json.dumps(payload)


def test_last_goal_status_returns_last_met_false(tmp_path):
    path = _write_transcript(tmp_path, _gs_line(True), _gs_line(False))
    gs = r._last_goal_status(path)
    assert gs is not None and gs.get("met") is False


def test_last_goal_status_returns_last_met_true(tmp_path):
    path = _write_transcript(tmp_path, _gs_line(False), _gs_line(True))
    gs = r._last_goal_status(path)
    assert gs is not None and gs.get("met") is True


def test_last_goal_status_none_when_absent(tmp_path):
    path = _write_transcript(
        tmp_path, json.dumps({"timestamp": "t", "attachment": {"type": "other"}})
    )
    assert r._last_goal_status(path) is None


def test_last_goal_status_none_on_missing_file(tmp_path):
    assert r._last_goal_status(str(tmp_path / "nope.jsonl")) is None


def test_last_goal_status_skips_corrupt_lines(tmp_path):
    path = _write_transcript(
        tmp_path, _gs_line(True), "{not valid json", _gs_line(False)
    )
    gs = r._last_goal_status(path)
    assert gs is not None and gs.get("met") is False


def test_last_goal_status_none_on_none_path():
    assert r._last_goal_status(None) is None


# --- goal-active gate on run() (runaway-guard) ------------------------------
# env SET ("1") throughout P2 tests to isolate the goal gate from the default
# flip (P3); only the last goal_status marker decides emit vs silent.


def test_silent_when_goal_met(monkeypatch, capsys, tmp_path):
    # continuation + last marker met:true (goal reached) -> silent (runaway guard)
    monkeypatch.setenv("HARNESS_REINJECT_STOP", "1")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    tp = _write_transcript(tmp_path, _gs_line(False), _gs_line(True))
    r.run(raw=_raw_stop(transcript_path=tp))
    assert '"decision"' not in capsys.readouterr().out


def test_silent_when_goal_cleared(monkeypatch, capsys, tmp_path):
    # manual clear writes met:true,sentinel:true as the last marker -> silent
    monkeypatch.setenv("HARNESS_REINJECT_STOP", "1")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    tp = _write_transcript(tmp_path, _gs_line(False), _gs_line(True, sentinel=True))
    r.run(raw=_raw_stop(transcript_path=tp))
    assert '"decision"' not in capsys.readouterr().out


def test_silent_when_no_goal_status(monkeypatch, capsys, tmp_path):
    # no goal_status marker at all -> not a /goal loop -> silent
    monkeypatch.setenv("HARNESS_REINJECT_STOP", "1")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    tp = _write_transcript(tmp_path, json.dumps({"timestamp": "t", "attachment": {"type": "other"}}))
    r.run(raw=_raw_stop(transcript_path=tp))
    assert '"decision"' not in capsys.readouterr().out


def test_silent_when_no_transcript_path(monkeypatch, capsys):
    # payload missing transcript_path -> gate cannot confirm /goal -> silent, no raise
    monkeypatch.setenv("HARNESS_REINJECT_STOP", "1")
    monkeypatch.setattr(inject_prompt_context, "build_slim_context", lambda root: "VOICE+RULES")
    monkeypatch.setattr(r.hook_runtime, "hook_enabled", lambda *a, **k: True)
    r.run(raw=_raw_stop(transcript_path=None))
    assert '"decision"' not in capsys.readouterr().out
