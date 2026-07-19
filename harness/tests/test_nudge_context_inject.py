"""test_nudge_context_inject.py — interactive-only decision-capture re-surfacer.

decision_capture_nudge records a `decision_capture_observation` audit event at
turn-end when a session shipped a decision-shaped change without the ledger
moving. This telemetry hook reads that trace at the NEXT UserPromptSubmit and
injects a one-line additionalContext pointing at /hs:remember — so the advisory
becomes live context instead of a stderr line the model never re-reads.

INTERACTIVE-ONLY by construction: the AFK loop drives `claude -p`, which never
fires UserPromptSubmit, so this inject is inert in an autonomous run (the bell
counter owns autonomous capture, not this hook).

Contract under test:
  - a recent same-session observation -> additionalContext mentioning /hs:remember
    and the recorded subjects.
  - no observation (or only other-session ones) -> plain {"continue": true} no-op.
  - an unreadable / malformed trace -> fail-open continue, never exit 2.
  - latest_observation picks the newest matching record and filters by session.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import nudge_context_inject as nci  # noqa: E402

_HOOK = _HOOKS / "nudge_context_inject.py"


def _obs(session, note, ts):
    return {"ts": ts, "actor": "user:x", "session": session,
            "hook": "decision_capture_nudge",
            "event": "decision_capture_observation", "status": "observed",
            "note": note}


def _write_trace(trace_dir, records, name="trace-20260101.jsonl"):
    trace_dir.mkdir(parents=True, exist_ok=True)
    p = trace_dir / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


def _run(stdin_obj, state_dir):
    env = dict(os.environ)
    env["HARNESS_STATE_DIR"] = str(state_dir)
    return subprocess.run([sys.executable, str(_HOOK)],
                          input=json.dumps(stdin_obj),
                          capture_output=True, text=True, env=env)


# --- module API ---------------------------------------------------------------

class TestLatestObservation:
    def test_picks_newest_same_session(self, tmp_path):
        trace = tmp_path / "trace"
        _write_trace(trace, [
            _obs("S1", "unrecorded_decision×1 — alpha", "2026-06-18T01:00:00+00:00"),
            _obs("S2", "unrecorded_decision×1 — other", "2026-06-18T02:00:00+00:00"),
            _obs("S1", "unrecorded_decision×2 — alpha, beta", "2026-06-18T03:00:00+00:00"),
        ])
        rec = nci.latest_observation("S1", trace)
        assert rec is not None
        assert rec["note"] == "unrecorded_decision×2 — alpha, beta"

    def test_other_session_only_is_none(self, tmp_path):
        trace = tmp_path / "trace"
        _write_trace(trace, [_obs("S2", "unrecorded_decision×1 — x", "2026-06-18T01:00:00+00:00")])
        assert nci.latest_observation("S1", trace) is None

    def test_missing_dir_is_none(self, tmp_path):
        assert nci.latest_observation("S1", tmp_path / "absent") is None

    def test_malformed_lines_skipped(self, tmp_path):
        trace = tmp_path / "trace"
        trace.mkdir(parents=True)
        (trace / "trace-20260101.jsonl").write_text(
            "{not json\n" + json.dumps(_obs("S1", "unrecorded_decision×1 — a",
                                            "2026-06-18T01:00:00+00:00")) + "\n",
            encoding="utf-8")
        rec = nci.latest_observation("S1", trace)
        assert rec is not None and rec["note"].endswith("a")


class TestBuildContext:
    def test_mentions_remember_and_subjects(self):
        obs = _obs("S1", "unrecorded_decision×2 — alpha, beta", "2026-06-18T03:00:00+00:00")
        text = nci.build_context(obs)
        assert "/hs:remember" in text
        assert "alpha" in text and "beta" in text


# --- hook integration (stdin/stdout, the telemetry path) ----------------------

class TestHookCli:
    def test_observation_injects_context(self, tmp_path):
        _write_trace(tmp_path / "trace",
                     [_obs("S1", "unrecorded_decision×1 — gamma", "2026-06-18T01:00:00+00:00")])
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        assert r.returncode == 0
        out = json.loads(r.stdout)
        ctx = out["hookSpecificOutput"]["additionalContext"]
        assert "/hs:remember" in ctx and "gamma" in ctx

    def test_no_observation_is_continue(self, tmp_path):
        (tmp_path / "trace").mkdir()
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        assert r.returncode == 0
        assert json.loads(r.stdout) == {"continue": True}

    def test_other_session_is_continue(self, tmp_path):
        _write_trace(tmp_path / "trace",
                     [_obs("S2", "unrecorded_decision×1 — z", "2026-06-18T01:00:00+00:00")])
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        assert json.loads(r.stdout) == {"continue": True}

    def test_malformed_trace_fail_open(self, tmp_path):
        trace = tmp_path / "trace"
        trace.mkdir()
        (trace / "trace-20260101.jsonl").write_text("{garbage\n", encoding="utf-8")
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        assert r.returncode == 0
        assert json.loads(r.stdout) == {"continue": True}

    def test_single_shot_does_not_renag(self, tmp_path):
        # The same observation surfaces once, then is suppressed on later prompts
        # (no re-nag until a newer observation supersedes it).
        _write_trace(tmp_path / "trace",
                     [_obs("S1", "unrecorded_decision×1 — gamma", "2026-06-18T01:00:00+00:00")])
        first = _run({"session_id": "S1", "prompt": "a",
                      "hook_event_name": "UserPromptSubmit"}, tmp_path)
        assert "/hs:remember" in json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
        second = _run({"session_id": "S1", "prompt": "b",
                       "hook_event_name": "UserPromptSubmit"}, tmp_path)
        assert json.loads(second.stdout) == {"continue": True}

    def test_newer_observation_resurfaces(self, tmp_path):
        trace = tmp_path / "trace"
        _write_trace(trace, [_obs("S1", "unrecorded_decision×1 — a", "2026-06-18T01:00:00+00:00")])
        _run({"session_id": "S1", "prompt": "a", "hook_event_name": "UserPromptSubmit"}, tmp_path)
        # a newer observation appears -> must surface again
        _write_trace(trace, [
            _obs("S1", "unrecorded_decision×1 — a", "2026-06-18T01:00:00+00:00"),
            _obs("S1", "unrecorded_decision×2 — a, b", "2026-06-18T05:00:00+00:00"),
        ])
        r = _run({"session_id": "S1", "prompt": "c",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "b" in ctx


# --- standards-drift re-surface (model-facing leg, per-kind marker) -----------

def _drift_obs(session, note, ts, event="standards_drift_observation"):
    return {"ts": ts, "actor": "user:x", "session": session,
            "hook": "standards_drift_nudge", "event": event,
            "status": "observed", "note": note}


class TestStandardsDriftResurface:
    def test_drift_injects_hs_docs(self, tmp_path):
        _write_trace(tmp_path / "trace",
                     [_drift_obs("S1", "2 — harness/scripts/x.py, harness/data/y.yaml",
                                 "2026-06-18T01:00:00+00:00")])
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "/hs:docs" in ctx and "standards-drift" in ctx
        assert "harness/scripts/x.py" in ctx

    def test_commit_variant_also_surfaces(self, tmp_path):
        _write_trace(tmp_path / "trace",
                     [_drift_obs("S1", "1 — harness/hooks/z.py", "2026-06-18T01:00:00+00:00",
                                 event="standards_drift_commit_observation")])
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "/hs:docs" in ctx

    def test_both_kinds_emit_together(self, tmp_path):
        _write_trace(tmp_path / "trace", [
            _obs("S1", "unrecorded_decision×1 — alpha", "2026-06-18T01:00:00+00:00"),
            _drift_obs("S1", "1 — harness/scripts/x.py", "2026-06-18T02:00:00+00:00"),
        ])
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "/hs:remember" in ctx and "/hs:docs" in ctx

    def test_per_kind_single_shot_independent(self, tmp_path):
        # surfacing drift once must NOT suppress a later decision observation:
        # the two kinds keep independent per-session markers.
        trace = tmp_path / "trace"
        _write_trace(trace, [_drift_obs("S1", "1 — harness/scripts/x.py",
                                        "2026-06-18T01:00:00+00:00")])
        first = _run({"session_id": "S1", "prompt": "a",
                      "hook_event_name": "UserPromptSubmit"}, tmp_path)
        assert "/hs:docs" in json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
        _write_trace(trace, [
            _drift_obs("S1", "1 — harness/scripts/x.py", "2026-06-18T01:00:00+00:00"),
            _obs("S1", "unrecorded_decision×1 — alpha", "2026-06-18T03:00:00+00:00"),
        ])
        second = _run({"session_id": "S1", "prompt": "b",
                       "hook_event_name": "UserPromptSubmit"}, tmp_path)
        ctx = json.loads(second.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "/hs:remember" in ctx and "/hs:docs" not in ctx


# --- memory-gap / backlog-capture / goal-cycle re-surface (H2-resolved) -------

def _mg_obs(session, note, ts):
    return {"ts": ts, "actor": "user:x", "session": session,
            "hook": "memory_gap_hook", "event": "memory_gap_observation",
            "status": "observed", "note": note}


def _backlog_obs(session, ts):
    return {"ts": ts, "actor": "user:x", "session": session,
            "hook": "backlog_capture_nudge", "event": "backlog_capture_observation",
            "status": "observed", "note": "deferred-work proposal surfaced"}


def _goal_cycle_obs(session, ts):
    return {"ts": ts, "actor": "user:x", "session": session,
            "hook": "goal_cycle_nudge", "event": "goal_cycle_observation",
            "status": "observed", "note": "cycle_N.md breadcrumb reminder surfaced"}


class TestMemoryGapRelay:
    def test_memory_gap_injects_context(self, tmp_path):
        _write_trace(tmp_path / "trace",
                     [_mg_obs("S1", "parse_error×1 — docs/x.md",
                              "2026-06-18T01:00:00+00:00")])
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "[memory-gap]" in ctx and "docs/x.md" in ctx

    def test_memory_gap_single_shot(self, tmp_path):
        trace = tmp_path / "trace"
        _write_trace(trace, [_mg_obs("S1", "parse_error×1 — a",
                                     "2026-06-18T01:00:00+00:00")])
        first = _run({"session_id": "S1", "prompt": "a",
                      "hook_event_name": "UserPromptSubmit"}, tmp_path)
        assert "[memory-gap]" in json.loads(first.stdout)["hookSpecificOutput"]["additionalContext"]
        second = _run({"session_id": "S1", "prompt": "b",
                       "hook_event_name": "UserPromptSubmit"}, tmp_path)
        assert json.loads(second.stdout) == {"continue": True}


class TestBacklogCaptureRelay:
    def test_backlog_injects_context(self, tmp_path):
        _write_trace(tmp_path / "trace", [_backlog_obs("S1", "2026-06-18T01:00:00+00:00")])
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "[backlog-capture]" in ctx and "backlog_register.py add" in ctx


class TestGoalCycleRelay:
    def test_goal_cycle_injects_context(self, tmp_path):
        _write_trace(tmp_path / "trace", [_goal_cycle_obs("S1", "2026-06-18T01:00:00+00:00")])
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "[goal-cycle]" in ctx and "cycle_N.md" in ctx


class TestAllFiveKindsIndependent:
    def test_all_kinds_can_emit_together(self, tmp_path):
        _write_trace(tmp_path / "trace", [
            _obs("S1", "unrecorded_decision×1 — alpha", "2026-06-18T01:00:00+00:00"),
            _drift_obs("S1", "1 — harness/scripts/x.py", "2026-06-18T01:00:00+00:00"),
            _mg_obs("S1", "parse_error×1 — a", "2026-06-18T01:00:00+00:00"),
            _backlog_obs("S1", "2026-06-18T01:00:00+00:00"),
            _goal_cycle_obs("S1", "2026-06-18T01:00:00+00:00"),
        ])
        r = _run({"session_id": "S1", "prompt": "hi",
                  "hook_event_name": "UserPromptSubmit"}, tmp_path)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        for marker in ("/hs:remember", "/hs:docs", "[memory-gap]",
                       "[backlog-capture]", "[goal-cycle]"):
            assert marker in ctx, (marker, ctx)


class TestGenericSystemMessageRelay:
    def test_generic_pass_surfaces_systemmessage_nudge(self, tmp_path):
        # A custom nudge routed to systemMessage records a probe_observation (model
        # bus, after the invariant fix). The generic resurface must surface it — a
        # systemMessage nudge reaches the MODEL, not only the human.
        _write_trace(tmp_path / "trace", [
            {"ts": "2026-06-18T01:00:00+00:00", "session": "S1", "hook": "probe",
             "event": "probe_observation", "status": "observed", "note": "TOKEN-abc"},
        ])
        ch = tmp_path / "nudge-channels.yaml"
        ch.write_text("channels:\n  probe: systemMessage\n", encoding="utf-8")
        env = dict(os.environ)
        env["HARNESS_STATE_DIR"] = str(tmp_path)
        env["HARNESS_NUDGE_CHANNELS"] = str(ch)
        r = subprocess.run([sys.executable, str(_HOOK)],
                           input=json.dumps({"session_id": "S1", "prompt": "hi",
                                             "hook_event_name": "UserPromptSubmit"}),
                           capture_output=True, text=True, env=env)
        ctx = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        assert "[nudge:probe] TOKEN-abc" in ctx
