"""test_backlog_nudges.py — the two backlog nudges (capture + hygiene).

Both are nudge-class: HOOK_CLASS="nudge", fail-open (any internal error → exit 0,
silent). The shipped harness-hooks.yaml now ships both ON (dogfood default); they
stay nudge-class and can never be escalated to blocking. capture proposes a
`backlog_register.py add`
command when work is deferred — propose-then-confirm, it NEVER executes. hygiene
reminds, at a publish boundary, to `done`/`archive` stale open items. Neither can
be escalated to blocking by config.
"""
import sys
from pathlib import Path

import yaml

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for p in (_HOOKS, _SCRIPTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import backlog_capture_nudge as cap  # noqa: E402
import backlog_hygiene_nudge as hyg  # noqa: E402
import backlog_register as br  # noqa: E402
import hook_runtime  # noqa: E402


# ---------- HOOK_CLASS + class posture ----------

def test_both_nudges_are_nudge_class():
    assert cap.HOOK_CLASS == "nudge"
    assert hyg.HOOK_CLASS == "nudge"


def test_both_nudges_default_enabled():
    # shipped harness-hooks.yaml ships both backlog nudges ON (dogfood default).
    assert hook_runtime.hook_enabled("backlog_capture_nudge", "nudge") is True
    assert hook_runtime.hook_enabled("backlog_hygiene_nudge", "nudge") is True


# ---------- capture: propose, never execute ----------

def test_capture_nudge_proposes_never_executes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cap.hook_runtime, "hook_enabled", lambda *a, **k: True)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    cap.core({"session_id": "s1", "cwd": str(tmp_path)})
    err = capsys.readouterr().err
    assert "backlog_register.py add" in err
    # propose-then-confirm: the nudge must NOT have created any backlog SSOT
    assert not (tmp_path / "docs" / "backlog.yaml").exists()


def test_capture_nudge_records_observation_for_relay(tmp_path, monkeypatch):
    # H2-resolved: this is a MODEL-aimed nudge -> nudge_context_inject relays it
    # as additionalContext at the next UserPromptSubmit. That relay reads a
    # backlog_capture_observation trace event, so core() must record one.
    monkeypatch.setattr(cap.hook_runtime, "hook_enabled", lambda *a, **k: True)
    monkeypatch.setenv("TMPDIR", str(tmp_path))
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    cap.core({"session_id": "b-obs", "cwd": str(tmp_path)})
    trace_dir = tmp_path / "state" / "trace"
    events = []
    if trace_dir.is_dir():
        import json
        for f in trace_dir.glob("trace-*.jsonl"):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    events.append(json.loads(line))
    obs = [e for e in events if e.get("event") == "backlog_capture_observation"]
    assert obs, "core() must record a backlog_capture_observation for the relay"
    assert obs[0].get("session") == "b-obs"


def test_capture_nudge_failopen_exit0_on_error(monkeypatch):
    monkeypatch.setattr(cap.hook_runtime, "hook_enabled", lambda *a, **k: True)

    def _boom(*a, **k):
        raise RuntimeError("forced")

    monkeypatch.setattr(cap, "core", _boom)
    # main wraps core; a raise inside must degrade to a silent exit 0
    assert cap.main() == 0


def test_capture_nudge_noop_when_disabled(monkeypatch, capsys):
    monkeypatch.setattr(cap.hook_runtime, "hook_enabled", lambda *a, **k: False)
    assert cap.main() == 0
    assert "backlog_register.py add" not in capsys.readouterr().err


# ---------- hygiene: remind to close stale open items ----------

def test_hygiene_nudge_proposes_done_when_open_items(tmp_path, monkeypatch):
    br.add(tmp_path, text="stale item", type="bug", priority="P2")
    monkeypatch.setattr(hyg.hook_runtime, "hook_enabled", lambda *a, **k: True)
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    # core() now RETURNS the advisory (routing is the caller's job via emit_nudge)
    msg = hyg.core({"tool_name": "Skill", "tool_input": {"skill": "hs:ship"}}) or ""
    assert "backlog_register.py" in msg
    assert "done" in msg or "archive" in msg


def test_hygiene_nudge_silent_when_no_open_items(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(hyg.hook_runtime, "hook_enabled", lambda *a, **k: True)
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    hyg.core({"tool_name": "Skill", "tool_input": {"skill": "hs:ship"}})
    assert capsys.readouterr().err == ""


def test_hygiene_nudge_silent_on_unrelated_skill(tmp_path, monkeypatch, capsys):
    br.add(tmp_path, text="x", type="bug", priority="P2")
    monkeypatch.setattr(hyg.hook_runtime, "hook_enabled", lambda *a, **k: True)
    monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
    hyg.core({"tool_name": "Skill", "tool_input": {"skill": "hs:cook"}})
    assert capsys.readouterr().err == ""


def test_hygiene_nudge_failopen(monkeypatch):
    monkeypatch.setattr(hyg.hook_runtime, "hook_enabled", lambda *a, **k: True)

    def _boom(*a, **k):
        raise RuntimeError("forced")

    monkeypatch.setattr(hyg, "core", _boom)
    assert hyg.main() == 0


# ---------- registration co-presence ----------

def test_nudges_registered_in_hooks_registration():
    # both migrated into the in-process dispatcher: they fire as cores of hook_dispatch.py
    # (backlog_capture -> Stop, backlog_hygiene -> PreToolUse:Skill), registered in
    # hook-dispatch.yaml rather than their own commands.
    disp = yaml.safe_load(
        (_HOOKS.parent / "data" / "hook-dispatch.yaml").read_text(encoding="utf-8"))
    mods = {c.get("module") for grp in disp["groups"].values() for c in grp}
    assert "backlog_capture_nudge" in mods
    assert "backlog_hygiene_nudge" in mods
