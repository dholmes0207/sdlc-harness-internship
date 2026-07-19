"""Tests for discover_isolation_nudge.py — advisory /clear-before-plan reminder.

Nudge the user to isolate context between discovery and planning. Discovery
(hs:discover) carries heavy research/debate context that biases planning toward
a pre-baked solution; the brief is the handoff, not the raw exploration. This
hook fires on the hs:plan invocation (PreToolUse:Skill) and, best-effort, warns
when hs:discover ran earlier in the SAME session_id (discovery context not
cleared before planning).

Nudge class properties under test:
  - advisory + fail-open: NEVER blocks (exit 0) — even on malformed input.
  - default OFF: a disabled hook is fully inert even with a carryover signal.
  - signal-gated: only nudges on a real same-session discover→plan carryover.

Tested via subprocess + real stdin JSON, HARNESS_ROOT seam (mirrors the
cook_isolation_nudge test so the two context-isolation nudges stay in lockstep).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
HOOK_PATH = _HOOKS / "discover_isolation_nudge.py"

_ENABLED = "hooks:\n  discover_isolation_nudge: {enabled: true}\n"
_DISABLED = "hooks: {}\n"


def _run(root: Path, config: Path, payload, raw: bool = False):
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(root)
    # Pin the state seam where _seed writes: under the bin/data split a
    # self-hosted state_dir() rides data_root() (.harness/state), so the hook
    # would otherwise miss the legacy harness/state/ seed. HARNESS_STATE_DIR is
    # the canonical override (same seam test_telemetry_paths uses).
    env["HARNESS_STATE_DIR"] = str(root / "harness" / "state")
    env["HARNESS_HOOK_CONFIG"] = str(config)
    # Hermetic sink: pin the advisory to stderr so this DETECTION test is
    # independent of the shipped nudge-channels.yaml (which routes this hook to
    # systemMessage). The channel router is covered by test_nudge_channels.
    ch = root / "nudge-channels.yaml"
    ch.write_text("default: stderr\nchannels: {}\n", encoding="utf-8")
    env["HARNESS_NUDGE_CHANNELS"] = str(ch)
    stdin = payload if raw else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin, text=True, capture_output=True, env=env,
    )


def _seed(root: Path, skill: str, session: str):
    tdir = root / "harness" / "state" / "telemetry"
    tdir.mkdir(parents=True, exist_ok=True)
    with (tdir / "invocations.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(
            {"skill": skill, "session": session, "ts": "2026-06-15T00:00", "via": "t"}
        ) + "\n")


def _cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "hooks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _plan(session="S1"):
    return {"tool_name": "Skill", "tool_input": {"skill": "hs:plan"},
            "session_id": session}


def test_nudges_when_discover_same_session(tmp_path):
    _seed(tmp_path, "hs:discover", "S1")
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _plan("S1"))
    assert r.returncode == 0                       # nudge NEVER blocks
    assert "/clear" in r.stderr                     # carryover → advisory


def test_silent_when_discover_other_session(tmp_path):
    _seed(tmp_path, "hs:discover", "OTHER")
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _plan("S1"))
    assert r.returncode == 0
    assert "/clear" not in r.stderr                 # different session → no signal


def test_silent_when_no_discover(tmp_path):
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _plan("S1"))
    assert r.returncode == 0
    assert "/clear" not in r.stderr


def test_ignores_non_plan_skill(tmp_path):
    _seed(tmp_path, "hs:discover", "S1")
    payload = {"tool_name": "Skill", "tool_input": {"skill": "hs:cook"},
               "session_id": "S1"}
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), payload)
    assert r.returncode == 0
    assert "/clear" not in r.stderr


def test_disabled_by_default_is_inert(tmp_path):
    _seed(tmp_path, "hs:discover", "S1")            # carryover present...
    r = _run(tmp_path, _cfg(tmp_path, _DISABLED), _plan("S1"))
    assert r.returncode == 0
    assert "/clear" not in r.stderr                 # ...but hook OFF → inert


def test_never_blocks_on_malformed_input(tmp_path):
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), "}{ not json", raw=True)
    assert r.returncode == 0                        # fail-open
