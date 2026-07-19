"""Tests for cook_delegate_nudge.py — advisory reminder that a `mode: hard` plan's
per-phase implement (3.I) should go to a `@developer` subagent, not run inline.

The delegate-by-default posture in hs:cook is prose, not a gate — nothing blocks an
inline run. This nudge is the soft backstop: when the MAIN agent writes non-test
source while a `mode: hard` plan is in_progress, it fires ONCE (per plan/session) to
say "delegate 3.I or opt into inline explicitly".

Signal design (the reliable, stateless predicate — no phase-window counting):
  - agent identity is the discriminator. A write from a subagent (agent_type present)
    IS the delegation — stay silent. A write from MAIN (attribution absent) under a
    hard plan is the inline case worth flagging.
  - suppressed on: mode:fast plan, no active plan, test-file / doc / artifact writes,
    and a phase whose frontmatter opts into inline (`in_place: true`, owns-matched).

Nudge class properties under test:
  - advisory + fail-open: NEVER blocks (exit 0), even on malformed input.
  - config-gated: a disabled hook is fully inert.

Tested via subprocess + real stdin JSON (code-standards §7), HARNESS_ROOT/CLAUDE_PROJECT_DIR seam.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
HOOK_PATH = _HOOKS / "cook_delegate_nudge.py"

_ENABLED = "hooks:\n  cook_delegate_nudge: {enabled: true}\n"
_DISABLED = "hooks: {}\n"

# The token the advisory must carry — the actionable fix.
_TOKEN = "@developer"


def _run(root: Path, config: Path, payload, raw: bool = False):
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(root)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env["HARNESS_STATE_DIR"] = str(root / "harness" / "state")
    env["HARNESS_HOOK_CONFIG"] = str(config)
    # Hermetic dedupe: pin the marker dir into the test tree so a repeated
    # (session, plan) does not leak across tests.
    env["TMPDIR"] = str(root)
    # Hermetic sink: pin the advisory to stderr, independent of nudge-channels.yaml.
    ch = root / "nudge-channels.yaml"
    ch.write_text("default: stderr\nchannels: {}\n", encoding="utf-8")
    env["HARNESS_NUDGE_CHANNELS"] = str(ch)
    stdin = payload if raw else json.dumps(payload)
    return subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=stdin, text=True, capture_output=True, env=env,
    )


def _seed_plan(root: Path, *, mode: str, status: str = "in_progress",
               name: str = "260711-0217-obs", phases=None):
    """Write plans/<name>/plan.md with the given frontmatter mode + status. When
    `phases` is given, write each as phases/<file> with the supplied frontmatter body."""
    pdir = root / "plans" / name
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "plan.md").write_text(
        "---\n"
        f"id: {name}\n"
        f"status: {status}\n"
        f"mode: {mode}\n"
        "---\n\n# Plan\n",
        encoding="utf-8",
    )
    for fname, body in (phases or {}).items():
        phdir = pdir / "phases"
        phdir.mkdir(parents=True, exist_ok=True)
        (phdir / fname).write_text(body, encoding="utf-8")
    return pdir


def _cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "hooks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _write(root: Path, rel: str, *, agent_type=None, session="S1"):
    """A Write payload targeting <root>/<rel>. agent_type set → a subagent write."""
    payload = {
        "tool_name": "Write",
        "tool_input": {"file_path": str(root / rel)},
        "session_id": session,
    }
    if agent_type is not None:
        payload["agent_type"] = agent_type
    return payload


# --- fires --------------------------------------------------------------------

def test_fires_on_main_source_write_under_hard_plan(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _write(tmp_path, "orchestrator/observe/run_log.py"))
    assert r.returncode == 0
    assert _TOKEN in r.stderr


def test_fires_on_edit_too(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    payload = {"tool_name": "Edit", "tool_input": {"file_path": str(tmp_path / "orchestrator/x.py")},
               "session_id": "S1"}
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), payload)
    assert r.returncode == 0
    assert _TOKEN in r.stderr


# --- suppressed ---------------------------------------------------------------

def test_silent_for_subagent_write(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED),
             _write(tmp_path, "orchestrator/observe/run_log.py", agent_type="hs:developer"))
    assert r.returncode == 0
    assert _TOKEN not in r.stderr                    # the subagent write IS the delegation


def test_silent_on_fast_plan(tmp_path):
    _seed_plan(tmp_path, mode="fast")
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _write(tmp_path, "orchestrator/x.py"))
    assert r.returncode == 0
    assert _TOKEN not in r.stderr


def test_silent_on_test_file(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _write(tmp_path, "orchestrator/tests/test_run_log.py"))
    assert r.returncode == 0
    assert _TOKEN not in r.stderr                    # writing the RED test is 3.T, not 3.I


def test_silent_on_docs_and_artifacts(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    for rel in ("docs/x.md", "plans/260711-0217-obs/artifacts/verification.json", "README.md"):
        r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _write(tmp_path, rel))
        assert r.returncode == 0
        assert _TOKEN not in r.stderr, rel


def test_silent_when_no_active_plan(tmp_path):
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _write(tmp_path, "orchestrator/x.py"))
    assert r.returncode == 0
    assert _TOKEN not in r.stderr


def test_silent_when_plan_not_in_progress(tmp_path):
    _seed_plan(tmp_path, mode="hard", status="approved")
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _write(tmp_path, "orchestrator/x.py"))
    assert r.returncode == 0
    assert _TOKEN not in r.stderr


def test_silent_on_phase_in_place_override(tmp_path):
    _seed_plan(
        tmp_path, mode="hard",
        phases={"phase-1-x.md": (
            "---\n"
            "phase: 1\n"
            "in_place: true\n"
            "owns:\n"
            "  - orchestrator/**\n"
            "---\n\n# Phase 1\n"
        )},
    )
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), _write(tmp_path, "orchestrator/observe/run_log.py"))
    assert r.returncode == 0
    assert _TOKEN not in r.stderr                    # phase opted into inline


# --- nudge-class invariants ---------------------------------------------------

def test_disabled_is_inert(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    r = _run(tmp_path, _cfg(tmp_path, _DISABLED), _write(tmp_path, "orchestrator/x.py"))
    assert r.returncode == 0
    assert _TOKEN not in r.stderr


def test_never_blocks_on_malformed_input(tmp_path):
    r = _run(tmp_path, _cfg(tmp_path, _ENABLED), "}{ not json", raw=True)
    assert r.returncode == 0


def test_deduped_once_per_plan_session(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    cfg = _cfg(tmp_path, _ENABLED)
    r1 = _run(tmp_path, cfg, _write(tmp_path, "orchestrator/a.py"))
    r2 = _run(tmp_path, cfg, _write(tmp_path, "orchestrator/b.py"))
    assert r1.returncode == 0 and r2.returncode == 0
    assert _TOKEN in r1.stderr                        # first write nudges
    assert _TOKEN not in r2.stderr                    # second is deduped (once per plan/session)


# --- in-process dispatcher entry ----------------------------------------------
# The nudge runs as a core of hook_dispatch.py (PreToolUse:Write|Edit|MultiEdit).
# The dispatcher calls a single-arg core(data) and emits its return UNCONDITIONALLY
# (no dedupe), so the dispatcher entry must resolve root + apply the dedupe itself and
# return the advisory string (or None). core_dispatch is that entry.

def _dispatch_env(root: Path, session="S1"):
    env = dict(os.environ)
    env["HARNESS_ROOT"] = str(root)
    env["CLAUDE_PROJECT_DIR"] = str(root)
    env["TMPDIR"] = str(root)  # hermetic dedupe marker dir
    env["HARNESS_SESSION_ID"] = session
    return env


def _call_core_dispatch(root: Path, payload, session="S1"):
    """Import cook_delegate_nudge in a subprocess and call core_dispatch(payload) with
    the HARNESS_ROOT seam set — returns the printed result ('' for None)."""
    script = (
        "import json,sys; import cook_delegate_nudge as m;"
        "d=json.loads(sys.stdin.read());"
        "r=m.core_dispatch(d); sys.stdout.write(r if r else '')"
    )
    return subprocess.run(
        [sys.executable, "-c", script],
        input=json.dumps(payload), text=True, capture_output=True,
        env=_dispatch_env(root, session), cwd=str(_HOOKS),
    )


def test_core_dispatch_is_single_arg_and_returns_advisory(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    r = _call_core_dispatch(tmp_path, _write(tmp_path, "orchestrator/observe/run_log.py"))
    assert r.returncode == 0, r.stderr
    assert _TOKEN in r.stdout                          # returns the advisory string


def test_core_dispatch_silent_for_subagent_write(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    r = _call_core_dispatch(
        tmp_path, _write(tmp_path, "orchestrator/observe/run_log.py", agent_type="hs:developer"))
    assert r.returncode == 0, r.stderr
    assert r.stdout == ""                              # subagent write IS the delegation


def test_core_dispatch_dedupes_once_per_plan_session(tmp_path):
    _seed_plan(tmp_path, mode="hard")
    r1 = _call_core_dispatch(tmp_path, _write(tmp_path, "orchestrator/a.py"))
    r2 = _call_core_dispatch(tmp_path, _write(tmp_path, "orchestrator/b.py"))
    assert _TOKEN in r1.stdout                          # first nudges
    assert r2.stdout == ""                              # second deduped (dispatcher emits raw)


def test_registered_as_dispatch_core():
    import yaml
    disp = yaml.safe_load(
        (_HOOKS.parent / "data" / "hook-dispatch.yaml").read_text(encoding="utf-8"))
    grp = disp["groups"].get("PreToolUse:Write|Edit|MultiEdit", [])
    entry = next((c for c in grp if c.get("module") == "cook_delegate_nudge"), None)
    assert entry is not None, "cook_delegate_nudge must be a Write|Edit|MultiEdit dispatch core"
    assert entry.get("entry") == "core_dispatch"       # the single-arg dispatcher entry
    assert entry.get("class") == "nudge"
