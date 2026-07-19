"""test_inject_prompt_context.py — UserPromptSubmit hook re-injecting the live
working context as additionalContext on every prompt.

CLAUDE.md is loaded once and drifts out of the working set over a long session;
this hook re-states the load-bearing conventions every turn (a deliberate
token-for-quality trade) and adds the DYNAMIC context a static file cannot: the
current git branch, the dated report/plan naming pattern, and the path layout.
Ported harness-native from ClaudeKit's context-builder — it must point at
harness/rules/, never at .claude/.

Telemetry-class + fail-open: advisory, never blocks. Disabled or any crash ->
plain continue (no context, exit 0). The hook is driven as a subprocess (the
real stdin/stdout contract); the build_context core is also exercised in-process.
"""
import json
import pytest
import os
import re
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_SCRIPTS))


def _env(tmp_path, **extra):
    env = dict(os.environ)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_HOOK_AUDIT_DISABLED"] = "1"
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_TELEMETRY_DISABLED", None)
    # BL-250: the real dev/CI shell may itself carry ANTHROPIC_MODEL/ANTHROPIC_DEFAULT_*
    # (this dev shell does, tagged [1m]) — reinject_config.fill_ratio's window resolver
    # now reads those, so an un-scrubbed subprocess env would make the adaptive-fill
    # tests' documented 200k-window math (fill_ratio ≈ 0.681) non-deterministic across
    # machines. Scrub them the same way HARNESS_* is scrubbed above.
    env.pop("ANTHROPIC_MODEL", None)
    env.pop("ANTHROPIC_DEFAULT_OPUS_MODEL", None)
    env.pop("ANTHROPIC_DEFAULT_SONNET_MODEL", None)
    env.pop("ANTHROPIC_DEFAULT_HAIKU_MODEL", None)
    env.update(extra)
    return env


def _run(tmp_path, stdin_obj, **extra):
    return subprocess.run(
        [sys.executable, str(_HOOKS / "inject_prompt_context.py")],
        input=json.dumps(stdin_obj), capture_output=True, text=True,
        env=_env(tmp_path, **extra),
    )


def _ctx(proc):
    out = json.loads(proc.stdout)
    hs = out.get("hookSpecificOutput") or {}
    return hs.get("additionalContext", "")


def test_emits_user_prompt_submit_event(tmp_path):
    proc = _run(tmp_path, {"prompt": "do the thing"})
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert out["hookSpecificOutput"]["additionalContext"]


def test_first_prompt_block_is_dynamic_only(tmp_path):
    # Diet B: the armed first-turn block carries only the DYNAMIC context a static
    # CLAUDE.md cannot (paths + active plan + naming + a one-line rule pointer). The
    # heavy static sections (Session/Rules/Rule-routing/Modularization) and the voice
    # register (a SessionStart dup, diet A) are gone from the first turn.
    ctx = _ctx(_run(tmp_path, {"prompt": "x"}))
    for header in ("## Paths", "## Plan Context", "## Naming", "## Rules"):
        assert header in ctx, f"missing dynamic section: {header}"
    assert "harness/rules" in ctx and "re-read on drift" in ctx.lower()  # 1-line pointer
    for absent in ("## Session", "## Rule routing (load on demand)",
                   "Modularization", "Terminal voice", "YAGNI",
                   "DO NOT create markdown files outside"):
        assert absent not in ctx, f"first-turn block must not carry: {absent}"


def test_points_at_harness_rules_not_claude(tmp_path):
    ctx = _ctx(_run(tmp_path, {"prompt": "x"}))
    assert "harness/rules" in ctx
    assert ".claude/rules" not in ctx
    assert ".claude/skills" not in ctx


def test_courier_project_paths_root_at_project_not_bin(tmp_path):
    # Under the courier/global layout HARNESS_BIN_ROOT is the SHARED read-only engine
    # home and CLAUDE_PROJECT_DIR is the writeable user repo. The injected Plans/
    # Reports/Docs/Plan-dir paths are project-scoped WRITES, so they must root at the
    # project, never at the read-only bin: a bin-rooted report path is blocked by
    # write_guard's shared-binary zone (the adopter then cannot create a plan/report
    # at all), and an unhardened bin makes every project share one plans/ dir. This is
    # the same root()->project_root() split the sibling hooks (gate_stage, agent_rbac,
    # simplify_gate, secret_scan) already apply. No-op under self-host (bin==project).
    bin_root = tmp_path / "engine"
    (bin_root / "harness").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    ctx = _ctx(_run(tmp_path, {"prompt": "x"},
                    HARNESS_BIN_ROOT=str(bin_root),
                    CLAUDE_PROJECT_DIR=str(project)))
    assert str(project) in ctx, "injected paths never mention the project root"
    for line in ctx.splitlines():
        if any(k in line for k in ("Reports:", "Plans:", "Plan dir:", "- Report:")):
            assert str(bin_root) not in line, (
                "project-scoped write path leaked into the read-only bin root: %s" % line)


# --- dispatcher cadence (the decay gate must survive the in-process dispatcher) ---
# The dispatcher runs a hook's registered entry directly; the decay cadence lives in
# run(), not core(). Wiring the raw core() re-injected the full block on EVERY prompt.
# core_gated is the gated entry; these pin (a) the registry uses it and (b) it throttles.

def _dispatch_ups(tmp_path, session="g"):
    """Drive the REAL dispatcher (hook_dispatch.py UserPromptSubmit) — a fake in-process
    core() call would not reproduce the every-prompt bug (that came from the wiring)."""
    env = _env(tmp_path)
    env["HARNESS_PY"] = sys.executable
    env["HARNESS_ROOT"] = str(_HOOKS.parent.parent)
    stdin = json.dumps({"session_id": session, "hook_event_name": "UserPromptSubmit",
                        "cwd": str(tmp_path), "prompt": "hi"})
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "hook_dispatch.py"), "UserPromptSubmit"],
        input=stdin, capture_output=True, text=True, env=env)
    out = json.loads(proc.stdout) if proc.stdout.strip() else {}
    ac = (out.get("hookSpecificOutput") or {}).get("additionalContext", "") or ""
    return bool(ac or out.get("systemMessage"))


def test_dispatcher_wires_gated_entry():
    import yaml
    disp = yaml.safe_load((_HOOKS.parent / "data" / "hook-dispatch.yaml").read_text(
        encoding="utf-8"))
    for grp in ("UserPromptSubmit", "SessionStart"):
        e = next((c for c in disp["groups"].get(grp, [])
                  if c.get("module") == "inject_prompt_context"), None)
        assert e is not None, "inject_prompt_context missing from %s" % grp
        assert e.get("entry") == "core_gated", (
            "%s must dispatch the decay-gated entry, not the raw core()" % grp)


def test_dispatcher_throttles_after_first_prompt(tmp_path):
    # THE BUG: through the dispatcher, the context block re-injected on EVERY prompt.
    # First prompt injects (armed default); the next two, same scope/sig, must stay
    # silent — the decay cadence, not a per-prompt dump.
    assert _dispatch_ups(tmp_path) is True, "first prompt must inject"
    assert _dispatch_ups(tmp_path) is False, "second prompt must be throttled by cadence"
    assert _dispatch_ups(tmp_path) is False, "third prompt must stay throttled"


def test_full_mode_knob_restores_legacy_block(tmp_path):
    # Rollback path: inject_refresh_mode=full restores the pre-diet heavy block
    # (Session/Rules/Rule-routing/Modularization). Exercised in-process against the
    # legacy builder so no config seam plumbing is needed.
    import inject_prompt_context as m
    root = Path(__file__).resolve().parents[2]
    ctx = m.build_full_context(root)
    for header in ("## Session", "## Rules", "## Rule routing (load on demand)",
                   "Modularization", "## Paths", "## Naming"):
        assert header in ctx, f"legacy full block missing: {header}"
    assert "harness-contract" in ctx and "verification-mechanism" in ctx
    assert "YAGNI" in ctx and "DO NOT create markdown files outside" in ctx
    # Even the legacy block drops the voice dup (A) and the team roster (personal-first).
    assert "Terminal voice" not in ctx and "## Team" not in ctx


def test_slim_refresh_carries_voice_first_block_does_not(tmp_path):
    import inject_prompt_context as m
    root = Path(__file__).resolve().parents[2]
    assert "voice_level=" in m.build_slim_context(root)      # slim keeps the register line
    assert "Terminal voice" not in m.build_context(root)     # first turn does not (A)


def test_naming_pattern_is_dated(tmp_path):
    ctx = _ctx(_run(tmp_path, {"prompt": "x"}))
    assert "{slug}" in ctx
    assert "{type}" in ctx
    # YYMMDD-HHMM stamp injected live
    assert re.search(r"\d{6}-\d{4}", ctx), "no dated naming stamp"


@pytest.mark.dev_repo
def test_branch_line_present_in_repo(tmp_path):
    # subprocess inherits cwd = repo root -> git branch resolves
    ctx = _ctx(_run(tmp_path, {"prompt": "x"}))
    assert "- Branch:" in ctx


def test_fail_open_when_disabled(tmp_path):
    proc = _run(tmp_path, {"prompt": "x"}, HARNESS_TELEMETRY_DISABLED="1")
    assert proc.returncode == 0
    assert _ctx(proc) == ""


def test_never_exits_two_on_garbage_stdin(tmp_path):
    proc = subprocess.run(
        [sys.executable, str(_HOOKS / "inject_prompt_context.py")],
        input="not json at all", capture_output=True, text=True,
        env=_env(tmp_path),
    )
    assert proc.returncode == 0


def test_build_context_core_is_pure():
    import inject_prompt_context as m
    text = m.build_context(Path("/tmp/some-repo-root"))
    assert "## Paths" in text            # dynamic block, no ## Session (diet B)
    assert "## Session" not in text
    assert "/tmp/some-repo-root/plans" in text
    assert ".claude/" not in text


def test_plan_context_reports_none_when_no_plans(tmp_path):
    import inject_prompt_context as m
    text = m.build_context(tmp_path)
    assert "- Plan: none" in text


def test_plan_context_resolves_latest_plan_abs_path(tmp_path):
    import inject_prompt_context as m
    # two plan dirs; the most-recently-touched plan.md is the active one
    old = tmp_path / "plans" / "260101-0900-old-thing"
    new = tmp_path / "plans" / "260617-0018-current-thing"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    (old / "plan.md").write_text("old", encoding="utf-8")
    plan = new / "plan.md"
    plan.write_text("current", encoding="utf-8")
    # make `new` strictly newer
    os.utime(old / "plan.md", (1_700_000_000, 1_700_000_000))
    os.utime(plan, (1_800_000_000, 1_800_000_000))

    text = m.build_context(tmp_path)
    assert "- Plan: %s" % plan in text
    assert "- Plan: none" not in text


# --- decay-aware cadence: pure decision ---------------------------------------

def test_decide_first_prompt_injects():
    import inject_prompt_context as m
    should, state = m.decide(None, "UserPromptSubmit")
    assert should is True
    assert state == {"turns": 0, "force": False, "sig": None}


def test_decide_throttles_right_after_injection():
    import inject_prompt_context as m
    should, state = m.decide({"turns": 0, "force": False}, "UserPromptSubmit")
    assert should is False
    assert state["turns"] == 1


def test_decide_reinjects_after_n_turns():
    import inject_prompt_context as m
    # turns just below N -> this prompt crosses the threshold
    should, state = m.decide({"turns": m._INJECT_EVERY_TURNS - 1, "force": False},
                             "UserPromptSubmit")
    assert should is True
    assert state == {"turns": 0, "force": False, "sig": None}


def test_decide_session_start_arms_next_prompt():
    import inject_prompt_context as m
    should, state = m.decide({"turns": 3, "force": False}, "SessionStart", "compact")
    assert should is False
    assert state == {"turns": 0, "force": True, "sig": None}
    # armed -> the following UserPromptSubmit injects
    should2, _ = m.decide(state, "UserPromptSubmit")
    assert should2 is True


def test_decide_reinjects_when_sig_changes():
    import inject_prompt_context as m
    # not armed, only one turn in, BUT the meaningful context changed (voice /
    # branch / plan) -> inject NOW, do not wait out the throttle.
    should, state = m.decide({"turns": 1, "force": False, "sig": "old"},
                             "UserPromptSubmit", current_sig="new")
    assert should is True
    assert state == {"turns": 0, "force": False, "sig": "new"}


def test_decide_throttles_when_sig_unchanged():
    import inject_prompt_context as m
    should, state = m.decide({"turns": 1, "force": False, "sig": "same"},
                             "UserPromptSubmit", current_sig="same")
    assert should is False
    assert state == {"turns": 2, "force": False, "sig": "same"}


# --- decay-aware cadence: end-to-end through the real state file --------------

def test_throttles_second_consecutive_prompt(tmp_path):
    p1 = _run(tmp_path, {"prompt": "a"})
    p2 = _run(tmp_path, {"prompt": "b"})   # shares HARNESS_STATE_DIR via tmp_path
    assert _ctx(p1)          # first prompt injects
    assert _ctx(p2) == ""    # immediately throttled


def test_compact_rearms_after_throttle(tmp_path):
    p1 = _run(tmp_path, {"prompt": "a"})                  # inject (consumes arm)
    p2 = _run(tmp_path, {"prompt": "b"})                  # throttled
    s = _run(tmp_path, {"hook_event_name": "SessionStart", "source": "compact"})
    p3 = _run(tmp_path, {"prompt": "c"})                  # re-armed by compact
    assert _ctx(p1)
    assert _ctx(p2) == ""
    assert _ctx(s) == ""      # SessionStart emits no context, only arms
    assert _ctx(p3)


# --- reinject knobs: decide() honors a resolved every_turns override ----------

def test_decide_honors_custom_every_turns():
    import inject_prompt_context as m
    # prior turns=2 -> this prompt's turns becomes 3; the default cadence (5)
    # would stay quiet at 3...
    should_default, _ = m.decide({"turns": 2, "force": False}, "UserPromptSubmit")
    assert should_default is False
    # ...but a resolved every_turns=3 crosses the threshold at the same turn count.
    should_custom, state = m.decide({"turns": 2, "force": False}, "UserPromptSubmit",
                                     every_turns=3)
    assert should_custom is True
    assert state == {"turns": 0, "force": False, "sig": None}


def test_decide_default_every_turns_unaffected_by_arg_omission():
    # back-compat: no every_turns arg keeps reading the module default (5).
    import inject_prompt_context as m
    should, state = m.decide({"turns": m._INJECT_EVERY_TURNS - 1, "force": False},
                              "UserPromptSubmit")
    assert should is True
    assert state == {"turns": 0, "force": False, "sig": None}


# --- reinject knobs: resolved slim budget --------------------------------------

def test_slim_budget_resolves_from_config(tmp_path, monkeypatch):
    import inject_prompt_context as m
    cfg = tmp_path / "reinject-strategy.yaml"
    cfg.write_text(
        "preset: lazy\n"
        "presets:\n"
        "  balanced: {every_turns: 5, slim_budget_chars: 1100}\n"
        "  lazy: {every_turns: 8, slim_budget_chars: 1000}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    assert m._slim_budget() == 1000


def test_slim_budget_fails_open_on_missing_config(tmp_path, monkeypatch):
    import inject_prompt_context as m
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(tmp_path / "missing.yaml"))
    assert m._slim_budget() == m.SLIM_BUDGET_CHARS


def test_slim_block_fits_under_each_preset_budget(tmp_path):
    # A neutral voice register (no persona-bundle pin) keeps the slim block's
    # length deterministic regardless of the developer's live dev-override
    # state, so the budget comparison below is not machine-dependent.
    import inject_prompt_context as m
    voice = tmp_path / "voice.yaml"
    voice.write_text("voice_level: 5\n", encoding="utf-8")
    old = os.environ.get("HARNESS_TERMINAL_VOICE")
    os.environ["HARNESS_TERMINAL_VOICE"] = str(voice)
    try:
        slim_len = len(m.build_slim_context(Path(__file__).resolve().parents[2]))
    finally:
        if old is None:
            os.environ.pop("HARNESS_TERMINAL_VOICE", None)
        else:
            os.environ["HARNESS_TERMINAL_VOICE"] = old
    for budget in (1400, 1100, 1000):  # aggressive, balanced, lazy
        assert slim_len <= budget, (
            "slim block (%d chars) must fit under every preset budget, got over %d"
            % (slim_len, budget))


# --- reinject knobs: session-boundary first injection stays full regardless ---

def test_session_boundary_first_injection_always_full_regardless_of_preset(tmp_path):
    cfg = tmp_path / "reinject-strategy.yaml"
    cfg.write_text(
        "preset: aggressive\n"
        "presets:\n"
        "  balanced: {every_turns: 5, slim_budget_chars: 1100}\n"
        "  aggressive: {every_turns: 3, slim_budget_chars: 1400}\n",
        encoding="utf-8",
    )
    env = {"HARNESS_REINJECT_STRATEGY": str(cfg)}
    _run(tmp_path, {"hook_event_name": "SessionStart", "source": "startup"}, **env)
    ctx = _ctx(_run(tmp_path, {"prompt": "first real prompt"}, **env))
    assert "## Rules" in ctx
    assert "## Naming" in ctx


# --- adaptive branch (P6/B3): decide() honors fill_ratio/threshold, pure --------

def test_decide_adaptive_injects_at_or_above_threshold():
    import inject_prompt_context as m
    should, state = m.decide({"turns": 1, "force": False, "sig": None}, "UserPromptSubmit",
                              fill_ratio=0.75, threshold=0.7)
    assert should is True
    assert state == {"turns": 0, "force": False, "sig": None}


def test_decide_adaptive_stays_quiet_below_threshold():
    import inject_prompt_context as m
    # turns=1 is well below the default turn-count cadence (5), and armed/changed
    # are both false -> only the adaptive ratio drives the decision here.
    should, state = m.decide({"turns": 1, "force": False, "sig": None}, "UserPromptSubmit",
                              fill_ratio=0.5, threshold=0.7)
    assert should is False
    assert state["turns"] == 2


def test_decide_adaptive_armed_still_forces_inject():
    # armed/changed still force-inject even when adaptive is active and the ratio
    # is below threshold (today's precedence is preserved).
    import inject_prompt_context as m
    should, _ = m.decide({"turns": 0, "force": True, "sig": None}, "UserPromptSubmit",
                          fill_ratio=0.1, threshold=0.7)
    assert should is True


def test_decide_fill_none_falls_back_to_turn_count_path():
    import inject_prompt_context as m
    # fill_ratio unavailable (unreadable transcript / unclassifiable model) -> the
    # turn-count path decides, never wedges quiet forever.
    should, _ = m.decide({"turns": 1, "force": False, "sig": None}, "UserPromptSubmit",
                          fill_ratio=None, threshold=0.7)
    assert should is False
    should2, _ = m.decide({"turns": m._INJECT_EVERY_TURNS - 1, "force": False, "sig": None},
                           "UserPromptSubmit", fill_ratio=None, threshold=0.7)
    assert should2 is True


def test_decide_threshold_none_falls_back_to_turn_count_path():
    # adaptive off (threshold None) even with a fill_ratio value present -> ignored,
    # the turn-count path decides exactly as if fill_ratio/threshold were never passed.
    import inject_prompt_context as m
    should, state = m.decide({"turns": 1, "force": False, "sig": None}, "UserPromptSubmit",
                              fill_ratio=0.99, threshold=None)
    assert should is False
    assert state["turns"] == 2


def test_adaptive_preset_end_to_end_injects_above_threshold_below_turn_cadence(tmp_path):
    # Wiring proof (not just the pure decide() branch above): with the `adaptive`
    # preset active and a transcript whose fill_ratio crosses `threshold`, the hook
    # injects even though the turn count is well under the balanced-preset cadence
    # (5) — the caller (run()) actually read the config + transcript_path and
    # passed fill_ratio/threshold into decide().
    cfg = tmp_path / "reinject-strategy.yaml"
    cfg.write_text(
        "preset: adaptive\n"
        "presets:\n"
        "  balanced: {every_turns: 5, slim_budget_chars: 1100}\n"
        "  adaptive: {every_turns: 5, slim_budget_chars: 1100, mode: fill, threshold: 0.5}\n",
        encoding="utf-8",
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "message": {"model": "claude-opus-4-8",
                     "usage": {"input_tokens": 37, "cache_read_input_tokens": 133413,
                               "cache_creation_input_tokens": 2658}},  # fill_ratio ≈ 0.681
    }) + "\n", encoding="utf-8")
    env = {"HARNESS_REINJECT_STRATEGY": str(cfg)}
    p1 = _run(tmp_path, {"prompt": "a", "transcript_path": str(transcript)}, **env)
    p2 = _run(tmp_path, {"prompt": "b", "transcript_path": str(transcript)}, **env)
    assert _ctx(p1), "first prompt injects (armed, regardless of preset)"
    assert _ctx(p2), (
        "second prompt: turns=1 is well under the turn-count cadence, but "
        "fill_ratio 0.681 >= threshold 0.5 under the adaptive preset -> must inject")


def test_adaptive_preset_end_to_end_stays_quiet_below_threshold(tmp_path):
    # Same wiring, but the ratio stays below threshold and turns are below the
    # turn-count cadence too -> quiet (adaptive does not over-inject).
    cfg = tmp_path / "reinject-strategy.yaml"
    cfg.write_text(
        "preset: adaptive\n"
        "presets:\n"
        "  balanced: {every_turns: 5, slim_budget_chars: 1100}\n"
        "  adaptive: {every_turns: 5, slim_budget_chars: 1100, mode: fill, threshold: 0.9}\n",
        encoding="utf-8",
    )
    transcript = tmp_path / "transcript.jsonl"
    transcript.write_text(json.dumps({
        "type": "assistant",
        "message": {"model": "claude-opus-4-8",
                     "usage": {"input_tokens": 37, "cache_read_input_tokens": 133413,
                               "cache_creation_input_tokens": 2658}},  # fill_ratio ≈ 0.681
    }) + "\n", encoding="utf-8")
    env = {"HARNESS_REINJECT_STRATEGY": str(cfg)}
    p1 = _run(tmp_path, {"prompt": "a", "transcript_path": str(transcript)}, **env)
    p2 = _run(tmp_path, {"prompt": "b", "transcript_path": str(transcript)}, **env)
    assert _ctx(p1)
    assert _ctx(p2) == "", "fill_ratio 0.681 < threshold 0.9 -> stays quiet"


def test_change_reinjects_before_throttle_window(tmp_path):
    # a mid-window config change (here: the terminal voice) must surface on the
    # NEXT prompt, not wait out the N-turn throttle. The env seam points voice at
    # a scratch file we mutate between prompts; branch/plan stay constant so the
    # sig changes ONLY because of the voice toggle.
    voice = tmp_path / "voice.yaml"
    voice.write_text("voice_level: 5\n", encoding="utf-8")
    env = {"HARNESS_TERMINAL_VOICE": str(voice)}
    p1 = _run(tmp_path, {"prompt": "a"}, **env)            # first prompt injects
    p2 = _run(tmp_path, {"prompt": "b"}, **env)            # immediately throttled
    voice.write_text("voice_level: 9\n", encoding="utf-8")  # toggle mid-window
    p3 = _run(tmp_path, {"prompt": "c"}, **env)            # sig changed -> inject
    assert _ctx(p1)
    assert _ctx(p2) == ""
    assert _ctx(p3)
    assert "voice_level=9" in _ctx(p3)
