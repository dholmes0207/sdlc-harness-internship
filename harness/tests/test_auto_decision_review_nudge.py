"""test_auto_decision_review_nudge.py — the Stop nudge for unreviewed must-review decisions.

Mirrors decision_capture_nudge: nudge-class, Stop event, fail-open. It counts must_review-
basket decisions that are still unreviewed (fold) and, when >0, surfaces ONE advisory via
route_relay_nudge — stderr/systemMessage, NEVER hookSpecificOutput.additionalContext (that
re-invokes the model and runs the loop away on Stop). Two ephemeral $TMPDIR flags kill the
noise: touched (store-path-keyed, set by the sink — count only a ledger actually written)
+ surfaced (session-keyed, set here — at most once per session).
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _p in (_HOOKS, _SCRIPTS):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import auto_decision_review_nudge as nudge  # noqa: E402
import auto_decision_log as adl  # noqa: E402
import hook_runtime  # noqa: E402

_LABELS = adl._DEFAULT_LABELS


def _ledger(tmp_path, *decisions):
    """Write a jsonl with the given (id, label, reviewed) decisions."""
    p = tmp_path / "auto-decisions.jsonl"
    lines = []
    for did, label, reviewed in decisions:
        lines.append(json.dumps({"type": "decision", "id": did, "label": label,
                                 "reviewed": False, "skill": "hs:cook", "mode": "auto",
                                 "what": "w", "why": "y", "evidence": "f.py:1",
                                 "in_plan": False}))
        if reviewed:
            lines.append(json.dumps({"type": "review", "target": did, "reviewed": True}))
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# --------------------------------------------------------------------------- class + count
def test_hook_class_is_nudge():
    assert nudge.HOOK_CLASS == "nudge"


def test_count_unreviewed_must_review_only(tmp_path):
    led = _ledger(tmp_path, ("a1", "ARCH", False), ("a2", "TRIVIAL", False),
                  ("a3", "SCOPE", True))
    # ARCH unreviewed counts; TRIVIAL is trace_only; SCOPE is reviewed → only 1
    assert nudge.count_unreviewed_must_review([led], adl) == 1


def test_count_drops_after_mark(tmp_path):
    led = tmp_path / "auto-decisions.jsonl"
    argv = ["--store", str(led), "--labels", str(_LABELS), "--no-render",
            "--skill", "hs:cook", "--mode", "auto", "--label", "ARCH",
            "--what", "w", "--why", "y", "--evidence", "f.py:1"]
    adl.main(argv)
    events = adl.load_events(led)
    the_id = [e["id"] for e in events if e.get("type") == "decision"][0]
    assert nudge.count_unreviewed_must_review([led], adl) == 1
    adl.main(["--store", str(led), "--no-render", "--mark-reviewed", the_id])
    assert nudge.count_unreviewed_must_review([led], adl) == 0


# --------------------------------------------------------------------------- handle_stop gates
def _drive(tmp_path, monkeypatch, led, *, touched, surfaced=False, enabled=True, session="S1"):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(nudge, "_enabled", lambda: enabled)
    # the nudge resolves stores the same way the sink does; stub it to our fixture store.
    monkeypatch.setattr(nudge, "_resolve_stores", lambda adl_mod: [led] if led else [])
    calls = []
    monkeypatch.setattr(nudge.hook_runtime, "route_relay_nudge",
                        lambda name, text, rec, **k: calls.append(text))
    if touched and led:
        # the touched-flag is keyed on the STORE PATH (set by the sink), not the session.
        adl.set_touched_flag(led)
    if surfaced:
        nudge._surfaced_flag(session).write_text("1", encoding="utf-8")
    rc = nudge.handle_stop({"session_id": session, "cwd": str(tmp_path)})
    return rc, calls


def test_surface_when_must_review_and_touched(tmp_path, monkeypatch):
    led = _ledger(tmp_path, ("a1", "ARCH", False), ("a2", "SCOPE", False))
    rc, calls = _drive(tmp_path, monkeypatch, led, touched=True)
    assert rc == 0
    assert len(calls) == 1
    assert "2" in calls[0]
    assert nudge._surfaced_flag("S1").exists()   # once-per-session flag set


def test_untouched_store_noop(tmp_path, monkeypatch):
    # the store was never written (no touched-flag) → an interactive session must not nudge,
    # even though the ledger fixture holds an unreviewed must-review line.
    led = _ledger(tmp_path, ("a1", "ARCH", False))
    rc, calls = _drive(tmp_path, monkeypatch, led, touched=False)
    assert rc == 0
    assert calls == []


def test_second_stop_same_session_noop(tmp_path, monkeypatch):
    led = _ledger(tmp_path, ("a1", "ARCH", False))
    rc, calls = _drive(tmp_path, monkeypatch, led, touched=True, surfaced=True)
    assert rc == 0
    assert calls == []   # surfaced already → no second surface


def test_zero_count_noop(tmp_path, monkeypatch):
    led = _ledger(tmp_path, ("a1", "TRIVIAL", False))  # trace-only only
    rc, calls = _drive(tmp_path, monkeypatch, led, touched=True)
    assert rc == 0
    assert calls == []


def test_disabled_noop(tmp_path, monkeypatch):
    led = _ledger(tmp_path, ("a1", "ARCH", False))
    rc, calls = _drive(tmp_path, monkeypatch, led, touched=True, enabled=False)
    assert rc == 0
    assert calls == []


def test_missing_ledger_fail_open(tmp_path, monkeypatch):
    missing = tmp_path / "nope.jsonl"
    rc, calls = _drive(tmp_path, monkeypatch, missing, touched=True)
    assert rc == 0
    assert calls == []


def test_degraded_visible_on_import_error(tmp_path, monkeypatch):
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setattr(nudge, "_enabled", lambda: True)

    def _boom():
        raise ImportError("simulated broken chain")

    monkeypatch.setattr(nudge, "_import_log", _boom)
    events = []
    monkeypatch.setattr(nudge.trace_log, "append_event",
                        lambda **kw: events.append(kw))
    rc = nudge.handle_stop({"session_id": "S1", "cwd": str(tmp_path)})
    assert rc == 0
    assert any("degraded" in (e.get("event") or "") for e in events)


# --------------------------------------------------------------------------- end-to-end
def _git(args, cwd):
    subprocess.run(["git"] + args, cwd=str(cwd), check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def test_e2e_real_sink_then_nudge_fires(tmp_path):
    """The regression guard for the session-mismatch bug: the REAL sink (Bash-tool env, no
    session id) writes a must-review decision, then the REAL nudge (hook env, a real uuid
    session) fires. A session-keyed touched-flag would never match across the two — only the
    store-path key does. Also re-asserts R3 (no additionalContext on Stop)."""
    import os
    session = "11111111-2222-3333-4444-555555555555"
    tdir = tmp_path / "tmp"; tdir.mkdir()
    proj = tmp_path / "proj"; proj.mkdir()
    slug = "260101-0000-e2e-plan"
    (proj / "plans" / slug).mkdir(parents=True)
    (proj / "plans" / slug / "plan.md").write_text(
        "---\nid: %s\nstatus: in_progress\n---\n# p\n" % slug, encoding="utf-8")
    _git(["init", "-q"], proj)
    _git(["config", "user.email", "t@t.t"], proj)
    _git(["config", "user.name", "t"], proj)
    _git(["add", "-A"], proj)
    _git(["commit", "-qm", "init"], proj)

    # faithful Bash-tool env: scrub every HARNESS_* (no session, no active-plan override leak).
    base_env = {k: v for k, v in os.environ.items() if not k.startswith("HARNESS_")}
    base_env["TMPDIR"] = str(tdir)
    base_env.pop("CLAUDE_PROJECT_DIR", None)

    # 1) REAL sink appends a must-review decision — no --session, no CLAUDE_PROJECT_DIR.
    rs = subprocess.run(
        [sys.executable, str(_SCRIPTS / "auto_decision_log.py"),
         "--skill", "hs:cook", "--mode", "auto", "--label", "ARCH",
         "--what", "w", "--why", "y", "--evidence", "f.py:1", "--no-render"],
        cwd=str(proj), capture_output=True, text=True, env=base_env)
    assert rs.returncode == 0, rs.stderr
    store = proj / "plans" / slug / "artifacts" / "auto-decisions.jsonl"
    assert store.is_file(), (rs.stderr, list((proj / "plans").rglob("*.jsonl")))

    # 2) REAL nudge on Stop — hook env, a real uuid session, config ON.
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text("hooks:\n  auto_decision_review_nudge:\n    enabled: true\n", encoding="utf-8")
    nudge_env = dict(base_env)
    nudge_env["HARNESS_HOOK_CONFIG"] = str(cfg)
    nudge_env["CLAUDE_PROJECT_DIR"] = str(proj)
    nudge_env["HARNESS_USER"] = "tester"
    rn = subprocess.run(
        [sys.executable, str(_HOOKS / "auto_decision_review_nudge.py")],
        input=json.dumps({"session_id": session, "cwd": str(proj)}),
        cwd=str(proj), capture_output=True, text=True, env=nudge_env)
    assert rn.returncode == 0, rn.stderr
    # never additionalContext / hookSpecificOutput on Stop; stdout is the bare continue.
    assert "additionalContext" not in rn.stdout
    assert "hookSpecificOutput" not in rn.stdout
    assert json.loads(rn.stdout).get("continue") is True
    # #1: the nudge ACTUALLY fires despite the sink being sessionless and the nudge holding a
    # real uuid — proof the touched-flag is store-path-keyed, not session-keyed.
    safe = hook_runtime.safe_session_id(session)
    assert (tdir / ("harness-adl-surfaced-%s" % safe)).exists(), rn.stderr
    assert "auto-decision ledger" in rn.stderr
