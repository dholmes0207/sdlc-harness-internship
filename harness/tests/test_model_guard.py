"""Subprocess exit-code contract for model_guard (LESSONS b + red-team F1/F4/F5).

The fail-closed path is proven by running the real hook as a subprocess over stdin JSON —
never by importing internals, which would not measure the exit-2 contract the host sees.

Key adversarial anchors:
  - #1 runs with HARNESS_DELEGATE_CONSENT UNSET: the clone MUST NOT ship a dark env-gate
    (F1). A hook that inherited delegate_consent's `_knob_on()` would pass here and never
    block — this test is the trap for that.
  - #13 (marker + empty session) proves the escape cannot authorize a session-less spawn
    (F5): consume_marker returns False for an empty session, so the block still fires.
"""
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_HOOK = _ROOT / "harness" / "hooks" / "model_guard.py"
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _policy(tmp_path, text):
    p = tmp_path / "model-policy.yaml"
    p.write_text(text, encoding="utf-8")
    return str(p)


def _run(payload, env_extra, state_dir):
    env = {
        "HARNESS_HOOK_AUDIT_DISABLED": "1",
        "HARNESS_STATE_DIR": str(state_dir),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    env.update(env_extra)
    # Deliberately DO NOT set HARNESS_DELEGATE_CONSENT — the env is clean (F1).
    proc = subprocess.run(
        [sys.executable, str(_HOOK)],
        input=json.dumps(payload),
        capture_output=True, text=True, env=env,
    )
    return proc


_BLOCK = "schema_version: '1.0'\nmode: block\nagents:\n  explore:\n    required_model: haiku\n    mode: block\n"


def test_1_block_no_model_env_clean(tmp_path):
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK)}
    p = _run({"tool_input": {"subagent_type": "Explore"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 2, p.stderr
    assert "haiku" in p.stderr
    assert "explore_override" in p.stderr or "escape" in p.stderr.lower()


def test_2_model_haiku_passes(tmp_path):
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK)}
    p = _run({"tool_input": {"subagent_type": "Explore", "model": "haiku"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_2b_dated_haiku_id_passes(tmp_path):
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK)}
    p = _run({"tool_input": {"subagent_type": "Explore", "model": "claude-haiku-4-5-20251001"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_3_escape_marker_allows(tmp_path):
    import explore_override
    state = tmp_path / "st"
    explore_override.write_marker("sess-esc", "need opus for X", env={"HARNESS_STATE_DIR": str(state)})
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK)}
    p = _run({"tool_input": {"subagent_type": "Explore"}, "session_id": "sess-esc"}, env, state)
    assert p.returncode == 0, p.stderr


def test_5_advisory_passes_with_nudge(tmp_path):
    cfg = "mode: advisory\nagents:\n  explore:\n    required_model: haiku\n"
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, cfg)}
    p = _run({"tool_input": {"subagent_type": "Explore"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr
    assert p.stderr.strip() != ""


def test_6_off_is_silent(tmp_path):
    cfg = "mode: off\nagents:\n  explore:\n    required_model: haiku\n"
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, cfg)}
    p = _run({"tool_input": {"subagent_type": "Explore"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr
    assert p.stderr.strip() == ""


def test_7_casing_normalized(tmp_path):
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK)}
    for name in ("Explore", "EXPLORE", "explore"):
        p = _run({"tool_input": {"subagent_type": name}, "session_id": "s1"}, env, tmp_path / "st")
        assert p.returncode == 2, (name, p.stderr)


def test_8_non_explore_free(tmp_path):
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK)}
    p = _run({"tool_input": {"subagent_type": "researcher"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_9_garbage_config_fails_open(tmp_path):
    p_cfg = tmp_path / "model-policy.yaml"
    p_cfg.write_text("::: not yaml [[[\n  ::\n", encoding="utf-8")
    env = {"HARNESS_MODEL_POLICY": str(p_cfg)}
    p = _run({"tool_input": {"subagent_type": "Explore"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_10_disabled_registration_noop(tmp_path):
    hooks_cfg = tmp_path / "harness-hooks.yaml"
    hooks_cfg.write_text("hooks:\n  model_guard: {enabled: false}\n", encoding="utf-8")
    env = {
        "HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK),
        "HARNESS_HOOK_CONFIG": str(hooks_cfg),
    }
    p = _run({"tool_input": {"subagent_type": "Explore"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_11_unbounded_agent_free(tmp_path):
    # An agent with no entry in the policy carries no bound → passes (global block notwithstanding).
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK)}
    p = _run({"tool_input": {"subagent_type": "general-purpose"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_12_per_agent_advisory_over_global_block(tmp_path):
    cfg = "mode: block\nagents:\n  explore:\n    required_model: haiku\n    mode: advisory\n"
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, cfg)}
    p = _run({"tool_input": {"subagent_type": "Explore"}, "session_id": "s1"}, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_14_advisory_does_not_consume_marker(tmp_path):
    # A granted override is spent only to convert a real block into a pass. Advisory mode
    # never blocks, so it must leave the marker intact (marker economy).
    import explore_override
    state = tmp_path / "st"
    env_marker = {"HARNESS_STATE_DIR": str(state)}
    explore_override.write_marker("sess-adv", "keep me", env=env_marker)
    cfg = "mode: advisory\nagents:\n  explore:\n    required_model: haiku\n"
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, cfg)}
    p = _run({"tool_input": {"subagent_type": "Explore"}, "session_id": "sess-adv"}, env, state)
    assert p.returncode == 0, p.stderr
    # Marker survives — advisory did not burn it.
    assert explore_override.read_marker("sess-adv", env=env_marker) is not None


def test_13_marker_empty_session_still_blocks(tmp_path):
    # F5: a marker keyed to an empty session is impossible → block still fires.
    import explore_override
    state = tmp_path / "st"
    # Even if someone tried to grant for "", write_marker refuses an empty session.
    assert explore_override.write_marker("", "x", env={"HARNESS_STATE_DIR": str(state)}) is False
    env = {"HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK)}
    p = _run({"tool_input": {"subagent_type": "Explore"}, "session_id": ""}, env, state)
    assert p.returncode == 2, p.stderr


# --- generalized directional bounds -------------------------------------------------------

_STD = {
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8",
}

_MULTI = (
    "schema_version: '2.0'\nmode: block\nagents:\n"
    "  general-purpose:  {max_model: sonnet}\n"
    "  claude:           {require_explicit: true}\n"
    "  statusline-setup: {required_model: haiku}\n"
    "  plan:             {min_model: sonnet, mode: advisory}\n"
    "  code-reviewer:    {required_model: opus, self_pinned: true}\n"
)


def _transcript(tmp_path, live_model):
    """A minimal transcript whose last assistant message stamps `live_model`."""
    p = tmp_path / "t.jsonl"
    p.write_text(
        json.dumps({"message": {"role": "user", "content": "hi"}}) + "\n"
        + json.dumps({"message": {"role": "assistant", "model": live_model, "content": []}}) + "\n",
        encoding="utf-8",
    )
    return str(p)


def test_gp_ceiling_blocks_opus_session(tmp_path):
    env = dict(_STD); env["HARNESS_MODEL_POLICY"] = _policy(tmp_path, _MULTI)
    payload = {"tool_input": {"subagent_type": "general-purpose"},
               "session_id": "s1", "transcript_path": _transcript(tmp_path, "claude-opus-4-8")}
    p = _run(payload, env, tmp_path / "st")
    assert p.returncode == 2, p.stderr
    assert "sonnet" in p.stderr and "specialized agent-type" in p.stderr


def test_gp_ceiling_passes_when_session_at_or_under_cap(tmp_path):
    # "hiện tại <= cái gọi thì cho qua": live session already sonnet → inherit passes.
    env = dict(_STD); env["HARNESS_MODEL_POLICY"] = _policy(tmp_path, _MULTI)
    payload = {"tool_input": {"subagent_type": "general-purpose"},
               "session_id": "s1", "transcript_path": _transcript(tmp_path, "claude-sonnet-5")}
    p = _run(payload, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_claude_require_explicit_blocks_inherit(tmp_path):
    env = dict(_STD); env["HARNESS_MODEL_POLICY"] = _policy(tmp_path, _MULTI)
    payload = {"tool_input": {"subagent_type": "claude"},
               "session_id": "s1", "transcript_path": _transcript(tmp_path, "claude-opus-4-8")}
    p = _run(payload, env, tmp_path / "st")
    assert p.returncode == 2, p.stderr
    assert "explicit" in p.stderr.lower()


def test_claude_explicit_model_passes(tmp_path):
    env = dict(_STD); env["HARNESS_MODEL_POLICY"] = _policy(tmp_path, _MULTI)
    payload = {"tool_input": {"subagent_type": "claude", "model": "claude-sonnet-5"}, "session_id": "s1"}
    p = _run(payload, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_plan_floor_advisory_nudges_not_blocks(tmp_path):
    env = dict(_STD); env["HARNESS_MODEL_POLICY"] = _policy(tmp_path, _MULTI)
    payload = {"tool_input": {"subagent_type": "plan", "model": "claude-haiku-4-5"}, "session_id": "s1"}
    p = _run(payload, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr            # advisory never blocks
    assert p.stderr.strip() != "" and "sonnet" in p.stderr


def test_self_pinned_hs_agent_inherit_passes(tmp_path):
    # code-reviewer inherits on a sonnet session, but self_pinned trusts its opus frontmatter.
    env = dict(_STD); env["HARNESS_MODEL_POLICY"] = _policy(tmp_path, _MULTI)
    payload = {"tool_input": {"subagent_type": "code-reviewer"},
               "session_id": "s1", "transcript_path": _transcript(tmp_path, "claude-sonnet-5")}
    p = _run(payload, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


def test_self_pinned_explicit_override_still_checked(tmp_path):
    env = dict(_STD); env["HARNESS_MODEL_POLICY"] = _policy(tmp_path, _MULTI)
    payload = {"tool_input": {"subagent_type": "code-reviewer", "model": "claude-haiku-4-5"}, "session_id": "s1"}
    p = _run(payload, env, tmp_path / "st")
    assert p.returncode == 2, p.stderr

def test_statusline_exact_inherit_uses_live_model(tmp_path):
    env = dict(_STD); env["HARNESS_MODEL_POLICY"] = _policy(tmp_path, _MULTI)
    payload = {"tool_input": {"subagent_type": "statusline-setup"},
               "session_id": "s1", "transcript_path": _transcript(tmp_path, "claude-opus-4-8")}
    p = _run(payload, env, tmp_path / "st")
    assert p.returncode == 2, p.stderr            # live opus != required haiku


def test_collapsed_custom_mapping_never_false_blocks(tmp_path):
    # All three tiers → one custom id. Tier bounds are meaningless → must NOT block.
    env = {
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "co-model",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "co-model",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "co-model",
        "HARNESS_MODEL_POLICY": _policy(tmp_path, _MULTI),
    }
    payload = {"tool_input": {"subagent_type": "general-purpose"},
               "session_id": "s1", "transcript_path": _transcript(tmp_path, "co-model")}
    p = _run(payload, env, tmp_path / "st")
    assert p.returncode == 0, p.stderr


# --- session auto-resolve + reasoning-two-way block message (friction + degrade-oan fixes) ---

def test_resolve_session_prefers_official_env(tmp_path):
    import explore_override
    # OFFICIAL env source wins over any transcript mtime heuristic.
    got = explore_override.resolve_current_session({"CLAUDE_CODE_SESSION_ID": "sess-official"})
    assert got == "sess-official"
    # legacy alias also honored
    assert explore_override.resolve_current_session({"CLAUDE_SESSION_ID": "sess-alias"}) == "sess-alias"


def test_grant_auto_resolves_session_no_flag(tmp_path):
    import explore_override
    env = {"CLAUDE_CODE_SESSION_ID": "sess-auto", "HARNESS_STATE_DIR": str(tmp_path)}
    session = explore_override._session_or_resolve("", env)
    assert session == "sess-auto"
    assert explore_override.write_marker(session, "auto-grant test", count=1, env=env) is True
    # the guard consumes by the SAME session key → marker is found
    assert explore_override.consume_marker("sess-auto", env=env) is True


def test_empty_session_resolves_empty_keeps_f5(tmp_path):
    import explore_override
    # No env key and a project dir with no transcripts → '' (grant would no-op, F5 intact).
    got = explore_override.resolve_current_session({"CLAUDE_PROJECT_DIR": str(tmp_path)})
    assert got == ""


def test_block_message_is_reasoning_two_way():
    import model_guard as g
    msg = g._block_reason("general-purpose",
                          {"kind": "ceiling", "effective": "claude-opus-4-8"},
                          {"max_model": "sonnet"}, "claude-opus-4-8")
    # opens by making the model reason, not reflexively downgrade
    assert "FIRST decide" in msg
    # both exits present: fit the bound (a) AND justify crossing it (b)
    assert "(a)" in msg and "(b)" in msg
    # escape names the command with NO <id> placeholder (auto-resolves the session)
    assert "explore_override.py --grant --reason" in msg
    assert "<id>" not in msg
    # still forbids dodging via a different agent-type
    assert "agent-type" in msg


def test_block_message_every_kind_carries_subagent_type_hint():
    import model_guard as g
    cases = [
        ("explicit", {}),
        ("ceiling", {"max_model": "haiku"}),
        ("floor", {"min_model": "opus"}),
        ("required", {"required_model": "sonnet"}),
    ]
    for kind, resolved in cases:
        msg = g._block_reason("general-purpose", {"kind": kind, "effective": "opus"},
                              resolved, "opus")
        # every block reason names the exact spawn param + the silent-fallback failure mode,
        # so a blocked spawn is told the right way to re-call regardless of which bound tripped.
        assert "subagent_type" in msg, kind
        assert "subject_type" in msg, kind


# --- dispatcher-driven: prove the gate still fires under the renamed module -------------

def test_dispatcher_driven_block_fires_under_renamed_module(tmp_path):
    """The rename (explore_model_guard -> model_guard) touches the module name AND the
    hook-dispatch.yaml row. Proving the file rename alone would miss a stale dispatch-row
    reference (module: explore_model_guard) that leaves the in-process dispatcher unable
    to import the hook at all — so this drives the REAL dispatcher subprocess
    (hook_dispatch.py PreToolUse "Agent|Task"), never model_guard.py directly."""
    dispatch = _ROOT / "harness" / "hooks" / "hook_dispatch.py"
    env = {
        "HARNESS_HOOK_AUDIT_DISABLED": "1",
        "HARNESS_STATE_DIR": str(tmp_path / "st"),
        "HARNESS_MODEL_POLICY": _policy(tmp_path, _BLOCK),
        "PATH": "/usr/bin:/bin:/usr/local/bin",
    }
    payload = json.dumps({"tool_name": "Agent", "tool_input": {"subagent_type": "Explore"},
                          "session_id": "s1"})
    proc = subprocess.run(
        [sys.executable, str(dispatch), "PreToolUse", "Agent|Task"],
        input=payload, capture_output=True, text=True, env=env,
    )
    assert proc.returncode == 2, proc.stderr
    assert "haiku" in proc.stderr
