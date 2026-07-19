"""test_goal_cycle_nudge.py — the cycle-memory breadcrumb nudge + convention doc.

goal_cycle_nudge reminds an unattended run, at a tick/Stop boundary, to append a
`cycle_N.md` breadcrumb the next tick reads — the file-based fix for built-in
/goal and built-in /loop being memory-blind between ticks. It is nudge-class:
HOOK_CLASS="nudge", fail-open, shipped ON (dogfood default), and NEVER writes the
breadcrumb itself. P7 must not clobber P3's two backlog nudges when it registers.
"""
import sys
import pytest
from pathlib import Path

import yaml

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SKILLS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "skills"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import goal_cycle_nudge as gcn  # noqa: E402
import hook_runtime  # noqa: E402

_CONVENTION = _SKILLS / "goal" / "references" / "cycle-convention.md"


# --- goal-active transcript helpers (mirror test_reinject_stop_context) --------
# A /goal tick stamps a jsonl record carrying a top-level `attachment`:
#   {"type":"goal_status","met":<bool>,"condition":<str>}. A plain interactive
# Stop has NO such marker — that is the signal the nudge must gate on.

def _gs_line(met, condition="x"):
    import json
    att = {"type": "goal_status", "met": met, "condition": condition}
    return json.dumps({"timestamp": "2026-06-25T17:46:15Z", "attachment": att})


def _write_transcript(tmp_path, *lines):
    p = tmp_path / "transcript.jsonl"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(p)


def test_goal_cycle_nudge_is_nudge_class():
    assert gcn.HOOK_CLASS == "nudge"


def test_goal_cycle_nudge_default_on():
    # shipped harness-hooks.yaml ships this nudge ON (dogfood default).
    assert hook_runtime.hook_enabled("goal_cycle_nudge", "nudge") is True


def test_goal_cycle_nudge_failopen_on_error(monkeypatch):
    monkeypatch.setattr(gcn.hook_runtime, "hook_enabled", lambda *a, **k: True)

    def _boom(*a, **k):
        raise RuntimeError("forced")

    monkeypatch.setattr(gcn, "core", _boom)
    assert gcn.main() == 0


def test_goal_cycle_nudge_proposes_breadcrumb_never_writes(tmp_path, monkeypatch,
                                                           capsys):
    monkeypatch.setattr(gcn.hook_runtime, "hook_enabled", lambda *a, **k: True)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    tp = _write_transcript(tmp_path, _gs_line(False))  # a live /goal tick
    gcn.core({"session_id": "g1", "transcript_path": tp})
    err = capsys.readouterr().err
    assert "cycle_" in err  # names the breadcrumb
    # advisory only — it must not create any cycle file
    assert not list(tmp_path.glob("cycle_*.md"))


def test_goal_cycle_nudge_silent_without_goal_marker(tmp_path, monkeypatch, capsys):
    # THE BUG FIX: a plain interactive Stop (no goal_status marker in the
    # transcript) must emit NOTHING — the nudge is goal/loop-scoped, not universal.
    monkeypatch.setattr(gcn.hook_runtime, "hook_enabled", lambda *a, **k: True)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    import json
    tp = _write_transcript(
        tmp_path, json.dumps({"timestamp": "t", "attachment": {"type": "other"}}))
    gcn.core({"session_id": "n1", "transcript_path": tp})
    assert capsys.readouterr().err == ""  # no false-positive nudge


def test_goal_cycle_nudge_silent_when_no_transcript(tmp_path, monkeypatch, capsys):
    # No transcript_path at all (session_id only) — the exact repro of the
    # fire-on-every-interactive-Stop bug. Must stay silent.
    monkeypatch.setattr(gcn.hook_runtime, "hook_enabled", lambda *a, **k: True)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    gcn.core({"session_id": "n2"})
    assert capsys.readouterr().err == ""


def test_goal_cycle_nudge_records_observation_for_relay(tmp_path, monkeypatch):
    # H2-resolved: this is a MODEL-aimed nudge -> nudge_context_inject relays it
    # as additionalContext at the next UserPromptSubmit. That relay reads a
    # goal_cycle_observation trace event, so core() must record one.
    monkeypatch.setattr(gcn.hook_runtime, "hook_enabled", lambda *a, **k: True)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    tp = _write_transcript(tmp_path, _gs_line(False))  # a live /goal tick
    gcn.core({"session_id": "g-obs", "transcript_path": tp})
    trace_dir = tmp_path / "state" / "trace"
    events = []
    if trace_dir.is_dir():
        import json
        for f in trace_dir.glob("trace-*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
    obs = [e for e in events if e.get("event") == "goal_cycle_observation"]
    assert obs, "core() must record a goal_cycle_observation for the relay"
    assert obs[0].get("session") == "g-obs"


def test_harness_hooks_retains_backlog_nudges_after_p7():
    cfg = yaml.safe_load((_HOOKS.parent / "data" / "harness-hooks.yaml").read_text(
        encoding="utf-8"))
    hooks = cfg.get("hooks", {})
    # P3's two backlog nudges must survive P7's merge, plus the new one
    assert "backlog_capture_nudge" in hooks
    assert "backlog_hygiene_nudge" in hooks
    assert "goal_cycle_nudge" in hooks


def test_goal_cycle_nudge_registered():
    # migrated into the in-process dispatcher: it fires as a Stop core of hook_dispatch.py,
    # registered in hook-dispatch.yaml rather than its own command.
    disp = yaml.safe_load((_HOOKS.parent / "data" /
                           "hook-dispatch.yaml").read_text(encoding="utf-8"))
    mods = {c.get("module") for grp in disp["groups"].values() for c in grp}
    assert "goal_cycle_nudge" in mods


@pytest.mark.dev_repo
def test_cycle_convention_doc_states_intra_run_only():
    text = _CONVENTION.read_text(encoding="utf-8").lower()
    assert "intra-run" in text or "within one run" in text
    assert "cross-run" in text  # explicitly names the non-goal
    # one convention, two built-in entry points; hs:loop explicitly out
    assert "/goal" in text and "/loop" in text
    assert "hs:loop" in text
