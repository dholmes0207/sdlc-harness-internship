"""Tests for loop_controller.py — the native AFK iteration driver.

Composes the parsed status + exit gate + stale guard + circuit breaker into one
loop, with an INJECTABLE invoker so the loop logic is fully testable without a
live Claude subprocess (the way ralph-cc's 1292 bats tests cover the loop).

Covers every termination path plus the is_error session reset, the restart
sentinel (exit 3), and the `--setting-sources` isolation in the real command.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "afk"))
import loop_controller as lc  # noqa: E402
import afk_termination as t  # noqa: E402


def _status_block(status="in_progress", exit_signal=False, files=0):
    import json
    body = json.dumps({"status": status, "exit_signal": exit_signal,
                       "files_modified": files})
    return "<<<AFK_STATUS>>>%s<<<END_AFK_STATUS>>>" % body


def _result(status="in_progress", exit_signal=False, files=0, diff=0, sig="",
            is_error=False, permission_denied=False, question=False, session=""):
    return lc.InvocationResult(
        stdout=_status_block(status, exit_signal, files),
        is_error=is_error, permission_denied=permission_denied, question=question,
        diff_count=diff, signature=sig, session_id=session)


def _scripted(results):
    """Invoker that returns each scripted result in turn; records sessions seen."""
    seen = {"sessions": []}

    def invoke(iteration, session_id):
        seen["sessions"].append(session_id)
        return results[iteration - 1]
    return invoke, seen


def test_reaches_max_iterations(tmp_path):
    # progress every turn (no CB), varied signatures (no stale), never exits
    results = [_result(diff=1, sig="s%d" % i) for i in range(5)]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path)
    assert c.run() is t.Termination.MAX_ITERATIONS


def test_dual_condition_exit(tmp_path):
    results = [
        _result(status="complete", exit_signal=True, diff=1, sig="a"),
        _result(status="complete", exit_signal=True, diff=1, sig="b"),
    ]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=10, state_dir=tmp_path)
    assert c.run() is t.Termination.EXIT_SIGNAL_CONFIRMED


def test_stale_loop_trips(tmp_path):
    # identical signature 3x, but progress True so the CB never opens first
    results = [_result(diff=1, sig="same") for _ in range(5)]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path, stale_at=3)
    assert c.run() is t.Termination.STALE_LOOP


def test_circuit_opens_on_no_progress(tmp_path):
    # no progress, varied signatures (no stale) → CB opens at 3
    results = [_result(diff=0, sig="s%d" % i) for i in range(5)]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path,
                          half_open_at=2, open_at=3)
    assert c.run() is t.Termination.CIRCUIT_OPEN


def test_permission_denied_opens_circuit(tmp_path):
    results = [_result(diff=1, sig="s%d" % i, permission_denied=(i == 0))
               for i in range(3)]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path)
    assert c.run() is t.Termination.CIRCUIT_OPEN


def test_sustained_question_streak_hands_off_to_user(tmp_path):
    # The CB suppresses the no-progress counter while Claude is blocked asking the
    # operator, so without a handoff the loop would burn every iteration and end as
    # MAX_ITERATIONS — a budget failure that hides "needs a decision". A sustained
    # blocked-on-question streak must instead hand off as AWAITING_USER (exit 42).
    results = [_result(diff=0, sig="s%d" % i, question=True) for i in range(5)]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path,
                          half_open_at=2, open_at=3)  # awaiting_user_at default 3
    assert c.run() is t.Termination.AWAITING_USER


def test_workspace_gone_terminates_with_honest_cause(tmp_path):
    # the repo vanished mid-run → WORKSPACE_GONE, not a no-progress CIRCUIT_OPEN
    r = _result(diff=0, sig="s0")
    r.workspace_gone = True
    invoke, _ = _scripted([r])
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path)
    assert c.run() is t.Termination.WORKSPACE_GONE


def test_repeating_question_loop_hands_off_not_stale(tmp_path):
    # The REALISTIC stuck case: a blocked-on-question loop repeats its output
    # verbatim, so the stale signature is identical every turn. AWAITING_USER
    # (exit 42, "needs a human decision") must win over STALE_LOOP (exit 1,
    # "budget/stall") — the stale guard must not pre-empt the handoff on a
    # question turn, mirroring how the circuit breaker suppresses its counter.
    results = [_result(diff=0, sig="same", question=True) for _ in range(5)]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path,
                          stale_at=3)  # awaiting_user_at default 3
    assert c.run() is t.Termination.AWAITING_USER


def test_brief_question_streak_then_progress_does_not_hand_off(tmp_path):
    # A blocked-on-question streak BELOW the threshold, broken by a progress turn,
    # must NOT hand off: a transient block that the loop recovers from is normal.
    results = [
        _result(diff=0, sig="q0", question=True),
        _result(diff=0, sig="q1", question=True),  # streak 2 (< default 3)
        _result(diff=1, sig="p0"),                  # progress resets the streak
        _result(diff=1, sig="p1"),
    ]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=4, state_dir=tmp_path,
                          half_open_at=2, open_at=3)
    assert c.run() is t.Termination.MAX_ITERATIONS


def test_awaiting_user_threshold_is_configurable(tmp_path):
    # The handoff threshold is a named knob; a stricter value hands off sooner.
    results = [_result(diff=0, sig="s%d" % i, question=True) for i in range(3)]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=3, state_dir=tmp_path,
                          half_open_at=2, open_at=3, awaiting_user_at=2)
    assert c.run() is t.Termination.AWAITING_USER


def test_blocked_status_without_explicit_question_also_hands_off(tmp_path):
    # The blocked signal is `question OR status=blocked` — a status=blocked stream
    # (no explicit question flag) is the same human-handoff condition.
    results = [_result(status="blocked", diff=0, sig="s%d" % i) for i in range(4)]
    invoke, _ = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=4, state_dir=tmp_path,
                          half_open_at=2, open_at=3)
    assert c.run() is t.Termination.AWAITING_USER


def test_is_error_drops_session(tmp_path):
    results = [
        _result(diff=1, sig="a", is_error=True, session="S1"),
        _result(diff=1, sig="b", session="S2"),
        _result(diff=1, sig="c", session="S2"),
    ]
    invoke, seen = _scripted(results)
    c = lc.LoopController(invoke, max_iterations=3, state_dir=tmp_path)
    c.run()
    # 1st call: no session yet (None); 2nd: prior was is_error → session dropped (None)
    assert seen["sessions"][0] is None
    assert seen["sessions"][1] is None
    # 3rd call resumes the good session from iteration 2
    assert seen["sessions"][2] == "S2"


def test_restart_sentinel_exits_three(tmp_path):
    sentinel = tmp_path / "restart-requested"
    sentinel.write_text("go", encoding="utf-8")
    invoke, _ = _scripted([_result(diff=1, sig="a")])
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path,
                          restart_sentinel=sentinel)
    term = c.run()
    assert term is t.Termination.RESTART_REQUESTED
    assert term.exit_code == 3


def test_stop_sentinel_clean_exit(tmp_path):
    sentinel = tmp_path / "stop-requested"
    sentinel.write_text("stop", encoding="utf-8")
    invoke, _ = _scripted([_result(diff=1, sig="a")])
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path,
                          stop_sentinel=sentinel)
    assert c.run() is t.Termination.EXPLICIT_STOP


def test_build_claude_command_isolates_settings():
    argv = lc.build_claude_command("do the thing", session_id=None)
    assert "--setting-sources" in argv
    i = argv.index("--setting-sources")
    assert argv[i + 1] == "project,local"


def test_build_claude_command_resumes_session():
    argv = lc.build_claude_command("p", session_id="SID-9")
    assert "--resume" in argv and "SID-9" in argv
    # and isolation is still present
    assert "--setting-sources" in argv


def test_live_invoker_fingerprints_identical_turns_equal(monkeypatch, tmp_path):
    """Two iterations that produce the SAME model stdout must fingerprint equal,
    regardless of iteration number — otherwise the stale guard can never trip on a
    live run. Guards against re-introducing an iteration prefix in the signature."""
    import subprocess as _sp

    class _Proc:
        returncode = 0
        stdout = "the exact same turn output"

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _Proc())
    # no git progress — keep the diff probe deterministic at 0
    monkeypatch.setattr(lc, "_git_diff_count", lambda repo_root: 0)

    invoke = lc.make_claude_invoker("plan", tmp_path)
    r1 = invoke(1, None)
    r7 = invoke(7, "some-session")
    assert r1.signature == r7.signature, "identical turns must fingerprint equal"


def test_live_invoker_drives_stale_loop_end_to_end(monkeypatch, tmp_path):
    """End-to-end through the live invoker: identical stdout every turn must reach
    STALE_LOOP, proving the signature feeds the guard correctly across iterations."""
    import subprocess as _sp

    class _Proc:
        returncode = 0
        stdout = "no progress, same words every time"

    monkeypatch.setattr(_sp, "run", lambda *a, **k: _Proc())
    monkeypatch.setattr(lc, "_git_diff_count", lambda repo_root: 1)  # fake progress so CB never opens first

    invoke = lc.make_claude_invoker("plan", tmp_path)
    c = lc.LoopController(invoke, max_iterations=5, state_dir=tmp_path, stale_at=3)
    assert c.run() is t.Termination.STALE_LOOP


def test_breaker_state_rehydrated_on_restart(tmp_path):
    """A controller built over an existing cb.jsonl must resume the breaker's
    accumulated no-progress count, not reset to CLOSED. Without rehydration a
    RESTART_REQUESTED re-invocation would silently reset the stagnation watchdog."""
    state = tmp_path
    # First controller: two no-progress turns prime the breaker toward OPEN,
    # writing cb.jsonl. Use varied sigs so STALE never fires first; run to max.
    results1 = [_result(diff=0, sig="x%d" % i) for i in range(2)]
    invoke1, _ = _scripted(results1)
    c1 = lc.LoopController(invoke1, max_iterations=2, state_dir=state,
                           half_open_at=2, open_at=3)
    assert c1.run() is t.Termination.MAX_ITERATIONS  # count reached 2 (HALF_OPEN)

    # Second controller over the SAME state dir: a single further no-progress turn
    # should tip the rehydrated breaker to OPEN (count 2 -> 3), proving the prior
    # state was restored rather than reset.
    results2 = [_result(diff=0, sig="y0")]
    invoke2, _ = _scripted(results2)
    c2 = lc.LoopController(invoke2, max_iterations=1, state_dir=state,
                           half_open_at=2, open_at=3)
    assert c2.run() is t.Termination.CIRCUIT_OPEN
