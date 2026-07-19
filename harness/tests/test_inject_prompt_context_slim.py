"""test_inject_prompt_context_slim.py — the slim-refresh channel.

The context re-injection hook re-states the load-bearing rules on a decay-aware
cadence. Emitting the full ~2.3K-token block on EVERY refresh re-pays for rules
that were already loaded once this session; the model only needs the full block
the first time the window turns over. This suite covers the slim channel: the
first injection after a SessionStart stays full, later refreshes emit a compact
~slim block, and a config toggle (inject_refresh_mode: full) restores the old
behaviour verbatim for A/B + rollback.

Two seams, same as the sibling suite: subprocess for the real stdin/stdout +
state-file contract, in-process for the pure builders.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_SCRIPTS))

_REPO = Path(__file__).resolve().parents[2]


def _env(tmp_path, **extra):
    env = dict(os.environ)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_HOOK_AUDIT_DISABLED"] = "1"
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_TELEMETRY_DISABLED", None)
    env.update(extra)
    return env


def _run(tmp_path, stdin_obj, **extra):
    return subprocess.run(
        [sys.executable, str(_HOOKS / "inject_prompt_context.py")],
        input=json.dumps(stdin_obj), capture_output=True, text=True,
        env=_env(tmp_path, **extra),
    )


def _ctx(proc):
    try:
        out = json.loads(proc.stdout)
    except Exception:
        return ""
    hs = out.get("hookSpecificOutput") or {}
    return hs.get("additionalContext", "")


# --- pure builder: slim content ------------------------------------------------

def test_cadence_refresh_is_slim():
    # slim drops the heavy full-block headings but keeps the load-bearing bits.
    import inject_prompt_context as m
    slim = m.build_slim_context(_REPO)
    assert "## Naming" not in slim
    assert "## Modularization" not in slim
    assert "## Session" not in slim
    assert len(slim) <= m.SLIM_BUDGET_CHARS
    # still carries a rule re-read pointer + at least one directive
    assert "harness/rules" in slim
    assert "directive" in slim.lower()


def test_slim_pointer_names_reading_order_docs():
    # On a decayed /goal loop the slim reread pointer must NAME the load-bearing files
    # (CLAUDE.md + the auto-read prose standards), not just a bare rules dir — so a
    # re-grounding after a big Stop-gap needs zero indirection to find what to reopen.
    import inject_prompt_context as m
    slim = m.build_slim_context(_REPO)
    assert "reread" in slim.lower()
    assert "CLAUDE.md" in slim
    assert "system-architecture" in slim   # names the auto-read arch standard
    assert "code-standards" in slim        # names the auto-read code standard
    assert len(slim) <= m.SLIM_BUDGET_CHARS  # still within the token diet


def test_slim_carries_literal_voice_level():
    # substring `voice_level=<n>` (NOT `vl=9`) so the sibling suite's
    # substring assertion on a sig-change reinject survives when that turn is slim.
    import inject_prompt_context as m
    slim = m.build_slim_context(_REPO)
    assert re.search(r"voice_level=\d", slim), "slim must carry literal voice_level=<n>"


def test_slim_keeps_paths_and_stamp(tmp_path):
    # the hook's reason-for-existing — absolute Reports/Plans paths + dated
    # stamp so a deep CWD never spawns a stray plans/ subtree.
    import inject_prompt_context as m
    slim = m.build_slim_context(tmp_path)
    assert "Reports:" in slim
    assert "Plans:" in slim
    assert str(tmp_path) in slim               # absolute, rooted at repo
    assert re.search(r"stamp:\d{6}-\d{4}", slim), "slim must carry a YYMMDD-HHMM stamp"


def test_slim_drops_roster_ok():
    # the injected roster is advisory only (the gate reads team.yaml directly), so
    # slim may drop it without weakening any gate.
    import inject_prompt_context as m
    slim = m.build_slim_context(_REPO)
    assert "## Team" not in slim


def test_slim_is_far_smaller_than_full():
    import inject_prompt_context as m
    # "full" is now the legacy rollback block; the diet first-turn block is itself
    # already a cut, so the far-smaller invariant is measured against build_full_context.
    full = m.build_full_context(_REPO)
    slim = m.build_slim_context(_REPO)
    # slim runs ~0.26 of full; the bound is 0.30 so a small drift in either block
    # doesn't trip a hard fail while still proving slim is a real cut, not a trim.
    assert len(slim) < len(full) * 0.30, "slim must be a real cut, not a trim"


# --- end-to-end: the full-vs-slim branch --------------------------------------

def test_first_injection_after_sessionstart_is_full(tmp_path):
    # SessionStart arms; the NEXT prompt (armed) must be the full block.
    _run(tmp_path, {"hook_event_name": "SessionStart", "source": "startup"})
    ctx = _ctx(_run(tmp_path, {"prompt": "first real prompt"}))
    assert "## Rules" in ctx
    assert "## Naming" in ctx


def test_sig_change_emits_slim_with_new_values(tmp_path):
    # after the armed full injection, a mid-window voice toggle forces a reinject
    # on the next prompt — that reinject is a REFRESH (not armed) -> slim, and it
    # must reflect the new value immediately.
    voice = tmp_path / "voice.yaml"
    voice.write_text("voice_level: 5\n", encoding="utf-8")
    env = {"HARNESS_TERMINAL_VOICE": str(voice)}
    p1 = _run(tmp_path, {"prompt": "a"}, **env)          # armed -> full
    _run(tmp_path, {"prompt": "b"}, **env)               # throttled
    voice.write_text("voice_level: 9\n", encoding="utf-8")
    p3 = _run(tmp_path, {"prompt": "c"}, **env)          # sig changed -> slim reinject
    assert "## Naming" in _ctx(p1)                        # first was full
    slim = _ctx(p3)
    assert slim
    assert "## Naming" not in slim                        # refresh is slim
    assert "voice_level=9" in slim


def test_full_mode_toggle_always_full(tmp_path):
    # inject_refresh_mode: full -> every refresh is the full block (rollback / A/B baseline).
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text("inject_refresh_mode: full\nhooks: {}\n", encoding="utf-8")
    voice = tmp_path / "voice.yaml"
    voice.write_text("voice_level: 5\n", encoding="utf-8")
    env = {"HARNESS_HOOK_CONFIG": str(cfg), "HARNESS_TERMINAL_VOICE": str(voice)}
    _run(tmp_path, {"prompt": "a"}, **env)               # armed -> full
    _run(tmp_path, {"prompt": "b"}, **env)               # throttled
    voice.write_text("voice_level: 9\n", encoding="utf-8")
    p3 = _run(tmp_path, {"prompt": "c"}, **env)          # sig changed -> reinject, mode=full
    assert "## Naming" in _ctx(p3), "full mode must keep refreshes full"


def test_default_mode_is_slim(tmp_path):
    # with no config knob, the shipped default is slim.
    import inject_prompt_context as m
    cfg = tmp_path / "harness-hooks.yaml"
    cfg.write_text("hooks: {}\n", encoding="utf-8")
    old = os.environ.get("HARNESS_HOOK_CONFIG")
    os.environ["HARNESS_HOOK_CONFIG"] = str(cfg)
    try:
        import hook_runtime
        hook_runtime._reset_config_cache()
        assert m._refresh_mode() == "slim"
    finally:
        if old is None:
            os.environ.pop("HARNESS_HOOK_CONFIG", None)
        else:
            os.environ["HARNESS_HOOK_CONFIG"] = old


# --- P2: Lever C tool-economy (soft rule + slim pointer) ----------------------

def test_rule_has_tool_economy_section():
    # the tool-economy directives land in the existing operational-discipline rule
    # (no new hook, no new file) — soft input-discipline the slim channel points at.
    rule = (_REPO / "harness" / "rules" / "agent-operational-discipline.md").read_text(encoding="utf-8")
    low = rule.lower()
    assert "tool cost" in low or "minimize tool" in low, "no tool-economy section"
    assert "limit" in low and "offset" in low, "missing Read limit/offset directive"
    assert "grep" in low, "missing grep-not-cat directive"
    assert "cache_read" in low or "read back each turn" in low, "missing why (history re-read cost)"


def test_slim_points_to_tool_economy():
    import inject_prompt_context as m
    slim = m.build_slim_context(_REPO)
    assert "agent-operational-discipline" in slim, "slim must point at the tool-economy rule"
    assert len(slim) <= m.SLIM_BUDGET_CHARS, "the pointer must not blow the slim budget"


def test_slim_directive_carries_probe_first():
    # The load-bearing working principle — probe/verify empirically before building on a
    # guess — must re-surface on the every-cadence slim channel, not only when the
    # operational-discipline rule is loaded on demand. Kept within the slim budget.
    import inject_prompt_context as m
    slim = m.build_slim_context(_REPO)
    assert "probe" in slim.lower(), "slim directive must re-surface probe-before-building"
    assert len(slim) <= m.SLIM_BUDGET_CHARS, "the probe pointer must not blow the slim budget"


# --- P3: Lever A scope-hint wiring (fire on the heavy-skill turn) --------------
#
# The advisory ships default-OFF (a top-level `scope_hint_advisory` knob); these
# wiring tests opt it ON via a throwaway config so the classifier→emit path is
# still exercised. `_hint_on` writes a minimal harness-hooks.yaml (no `hooks:`
# block, so inject_prompt_context keeps its telemetry class-default ON) and points
# HARNESS_HOOK_CONFIG at it.

def _hint_on(tmp_path):
    cfg = tmp_path / "hooks-hint-on.yaml"
    cfg.write_text("scope_hint_advisory: true\n", encoding="utf-8")
    return {"HARNESS_HOOK_CONFIG": str(cfg)}


def test_scope_hint_default_off(tmp_path):
    # with no flag set (the shipped default), a trivial task under a heavy skill
    # earns NO advisory — the nudge is opt-in, not on-by-default.
    ctx = _ctx(_run(tmp_path, {"prompt": "/hs:plan fix typo"}))
    assert ctx                      # first prompt still injects the context block
    assert "scope-hint" not in ctx  # ...but no advisory rides it when the flag is off


def test_heavy_skill_trivial_emits_suggestion_on_full_turn(tmp_path):
    # a trivial task under a heavy skill on the FIRST (full) turn gets the
    # advisory appended right then — not buried in a later slim refresh.
    ctx = _ctx(_run(tmp_path, {"prompt": "/hs:plan fix typo"}, **_hint_on(tmp_path)))
    assert "scope-hint" in ctx, "trivial-under-heavy must append the advisory when enabled"


def test_risky_heavy_skill_gets_no_suggestion(tmp_path):
    ctx = _ctx(_run(tmp_path, {"prompt": "/hs:plan migrate the auth schema"}, **_hint_on(tmp_path)))
    assert "scope-hint" not in ctx, "risky scope must never be nudged toward a lighter mode"


def test_prose_heavy_skill_gets_no_suggestion(tmp_path):
    # the corrected classifier: a prose architectural task under a heavy skill is
    # `standard`, not `trivial` — so even with the flag ON it earns no advisory.
    ctx = _ctx(_run(tmp_path, {"prompt": "/hs:plan redesign the whole caching layer"}, **_hint_on(tmp_path)))
    assert "scope-hint" not in ctx, "a prose heavy-skill task must not read trivial"


def test_no_heavy_skill_no_suggestion(tmp_path):
    ctx = _ctx(_run(tmp_path, {"prompt": "fix typo in README"}, **_hint_on(tmp_path)))
    assert ctx                      # first prompt injects
    assert "scope-hint" not in ctx  # trivial but no heavy skill -> nothing to suggest


def test_throttled_turn_caches_then_emits(tmp_path):
    # a heavy+trivial verdict on a THROTTLED turn is cached and surfaces on the
    # next actual injection (here forced by a voice sig-change), never lost.
    voice = tmp_path / "voice.yaml"
    voice.write_text("voice_level: 5\n", encoding="utf-8")
    env = {"HARNESS_TERMINAL_VOICE": str(voice), **_hint_on(tmp_path)}
    _run(tmp_path, {"prompt": "hello"}, **env)                       # armed -> full inject
    p2 = _run(tmp_path, {"prompt": "/hs:plan fix typo"}, **env)      # throttled -> cache
    assert _ctx(p2) == ""                                            # nothing emitted this turn
    voice.write_text("voice_level: 9\n", encoding="utf-8")
    p3 = _run(tmp_path, {"prompt": "unrelated follow-up"}, **env)    # sig change -> inject
    assert "scope-hint" in _ctx(p3), "cached suggestion must surface on the next injection"


# --- fail-open: an exception inside slim must still plain-continue -------------

def test_hook_fail_open_never_exit2_on_garbage(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "inject_prompt_context.py")],
        input="not json at all", capture_output=True, text=True,
        env=_env(tmp_path),
    )
    assert proc.returncode == 0


def test_slim_exception_fails_open(monkeypatch, capsys):
    # an exception raised while building slim must degrade to a plain continue,
    # never an exit 2 / raised exception.
    import inject_prompt_context as m

    def _boom(_root):
        raise RuntimeError("slim exploded")

    monkeypatch.setattr(m, "build_slim_context", _boom)
    # a scope that is NOT armed (force=False) so run() takes the slim branch, and
    # a turn count at the cadence so `should` is True.
    state = {"turns": m._INJECT_EVERY_TURNS - 1, "force": False, "sig": None}
    monkeypatch.setattr(m, "_load_state", lambda: {os.getcwd(): state})
    monkeypatch.setattr(m, "_save_state", lambda s: None)
    monkeypatch.setattr(m, "_refresh_mode", lambda: "slim")
    m.run(raw=json.dumps({"prompt": "x", "hook_event_name": "UserPromptSubmit"}))
    out = capsys.readouterr().out
    # a plain continue, not the slim payload (which exploded) and not a crash
    assert '"additionalContext"' not in out
