"""Round-trip + fail-open contract for model_policy.resolve (LESSONS a + red-team F3).

A config field only persists if EVERY read path carries it, so each mode is round-tripped
through the file with a non-default value, and the per-agent mode override is proven wired
(not an inert advertised knob). A broken config must fail OPEN — a reader that raises would
brick every Explore spawn.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import model_policy  # noqa: E402


def _write(tmp_path, text):
    p = tmp_path / "model-policy.yaml"
    p.write_text(text, encoding="utf-8")
    return {"HARNESS_MODEL_POLICY": str(p)}


def test_required_model_read(tmp_path):
    env = _write(tmp_path, "schema_version: '1.0'\nmode: block\nagents:\n  explore:\n    required_model: haiku\n    mode: block\n")
    r = model_policy.resolve("explore", env=env)
    assert r["required_model"] == "haiku"


def test_global_mode_roundtrip(tmp_path):
    for mode in ("block", "advisory", "off"):
        env = _write(tmp_path, "mode: %s\nagents:\n  explore:\n    required_model: haiku\n" % mode)
        assert model_policy.resolve("explore", env=env)["mode"] == mode, mode


def test_per_agent_mode_overrides_global(tmp_path):
    # F3: global block, per-agent advisory → resolve reads the per-agent value FIRST.
    env = _write(tmp_path, "mode: block\nagents:\n  explore:\n    required_model: haiku\n    mode: advisory\n")
    assert model_policy.resolve("explore", env=env)["mode"] == "advisory"


def test_per_agent_mode_absent_falls_back_to_global(tmp_path):
    env = _write(tmp_path, "mode: advisory\nagents:\n  explore:\n    required_model: haiku\n")
    assert model_policy.resolve("explore", env=env)["mode"] == "advisory"


def test_normalize_agent_type(tmp_path):
    env = _write(tmp_path, "mode: block\nagents:\n  explore:\n    required_model: haiku\n")
    for name in ("Explore", "EXPLORE", "hs:explore", " explore "):
        r = model_policy.resolve(name, env=env)
        assert r["required_model"] == "haiku", name


def test_unknown_agent_has_no_requirement(tmp_path):
    env = _write(tmp_path, "mode: block\nagents:\n  explore:\n    required_model: haiku\n")
    r = model_policy.resolve("researcher", env=env)
    assert r["required_model"] is None


def test_broken_config_fails_open(tmp_path):
    # Corrupt YAML → permissive (mode off / no requirement), never a raise.
    p = tmp_path / "model-policy.yaml"
    p.write_text("mode: [this: is: not: valid\n  ::::\n", encoding="utf-8")
    r = model_policy.resolve("explore", env={"HARNESS_MODEL_POLICY": str(p)})
    assert r["mode"] == "off"
    assert r["required_model"] is None


def test_missing_config_fails_open(tmp_path):
    env = {"HARNESS_MODEL_POLICY": str(tmp_path / "nope.yaml")}
    r = model_policy.resolve("explore", env=env)
    assert r["mode"] == "off"
    assert r["required_model"] is None


# --- directional bounds: resolve() carries the new keys -----------------------------------

def test_resolve_carries_all_bound_keys(tmp_path):
    env = _write(tmp_path, (
        "mode: block\nagents:\n"
        "  plan:            {min_model: sonnet, mode: advisory}\n"
        "  general-purpose: {max_model: sonnet}\n"
        "  claude:          {require_explicit: true}\n"
        "  code-reviewer:   {required_model: opus, self_pinned: true}\n"
    ))
    assert model_policy.resolve("plan", env=env)["min_model"] == "sonnet"
    assert model_policy.resolve("general-purpose", env=env)["max_model"] == "sonnet"
    assert model_policy.resolve("claude", env=env)["require_explicit"] is True
    r = model_policy.resolve("code-reviewer", env=env)
    assert r["required_model"] == "opus" and r["self_pinned"] is True


# --- classify_tier: maps THROUGH the env, never a hardcoded family substring --------------

def test_classify_via_env_mapping():
    env = {
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5[1m]",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8[1m]",
    }
    # transcript ids drop the [1m] tag — still classify by env mapping
    assert model_policy.classify_tier("claude-opus-4-8", env=env) == "opus"
    assert model_policy.classify_tier("claude-sonnet-5", env=env) == "sonnet"
    assert model_policy.classify_tier("claude-haiku-4-5", env=env) == "haiku"


def test_classify_custom_ids_without_family_names():
    # A custom mapping whose ids carry NO family substring must still classify via env.
    env = {
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "fast-1",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "mid-2",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "big-3",
    }
    assert model_policy.classify_tier("big-3", env=env) == "opus"
    assert model_policy.classify_tier("fast-1", env=env) == "haiku"
    assert model_policy.classify_tier("unknown-x", env=env) is None


def test_classify_collapsed_mapping_is_ambiguous():
    # All three tiers mapped to ONE id → cannot rank → AMBIGUOUS (callers fail open).
    env = {
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "one-model",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "one-model",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "one-model",
    }
    assert model_policy.classify_tier("one-model", env=env) == "AMBIGUOUS"


def test_classify_substring_fallback_when_env_absent():
    assert model_policy.classify_tier("claude-opus-4-8[1m]", env={}) == "opus"
    assert model_policy.classify_tier("gpt-4o", env={}) is None


# --- evaluate: exact / ceiling / floor / require_explicit + session-awareness -------------

_STD_ENV = {
    "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-4-5",
    "ANTHROPIC_DEFAULT_SONNET_MODEL": "claude-sonnet-5",
    "ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8",
}


def _r(**kw):
    base = {"required_model": None, "max_model": None, "min_model": None,
            "require_explicit": False, "self_pinned": False, "mode": "block"}
    base.update(kw)
    return base


def test_evaluate_exact_explicit_match_and_mismatch():
    ok = model_policy.evaluate("claude-haiku-4-5", "", _r(required_model="haiku"), env=_STD_ENV)
    assert ok["ok"] is True
    bad = model_policy.evaluate("claude-opus-4-8", "", _r(required_model="haiku"), env=_STD_ENV)
    assert bad["ok"] is False and bad["kind"] == "exact"


def test_evaluate_exact_inherit_uses_live_session_model():
    # No explicit model, not self_pinned → effective = the LIVE session model.
    blocked = model_policy.evaluate("", "claude-opus-4-8", _r(required_model="haiku"), env=_STD_ENV)
    assert blocked["ok"] is False  # session opus != required haiku (explore's case)
    passed = model_policy.evaluate("", "claude-haiku-4-5", _r(required_model="haiku"), env=_STD_ENV)
    assert passed["ok"] is True


def test_evaluate_self_pinned_inherit_is_trusted():
    # hs agent, bare inherit → frontmatter applies its model → satisfied regardless of session.
    r = model_policy.evaluate("", "claude-sonnet-5", _r(required_model="opus", self_pinned=True), env=_STD_ENV)
    assert r["ok"] is True
    # but an EXPLICIT deviating override is still evaluated.
    r2 = model_policy.evaluate("claude-haiku-4-5", "", _r(required_model="opus", self_pinned=True), env=_STD_ENV)
    assert r2["ok"] is False


def test_evaluate_ceiling_session_at_or_under_cap_passes():
    # "hiện tại <= cái gọi thì cho qua": session sonnet, cap <= sonnet → pass on inherit.
    r = model_policy.evaluate("", "claude-sonnet-5", _r(max_model="sonnet"), env=_STD_ENV)
    assert r["ok"] is True
    # session opus, cap <= sonnet → block.
    r2 = model_policy.evaluate("", "claude-opus-4-8", _r(max_model="sonnet"), env=_STD_ENV)
    assert r2["ok"] is False and r2["kind"] == "ceiling"


def test_evaluate_floor_below_is_violation():
    r = model_policy.evaluate("claude-haiku-4-5", "", _r(min_model="sonnet"), env=_STD_ENV)
    assert r["ok"] is False and r["kind"] == "floor"
    r2 = model_policy.evaluate("claude-opus-4-8", "", _r(min_model="sonnet"), env=_STD_ENV)
    assert r2["ok"] is True


def test_evaluate_require_explicit():
    assert model_policy.evaluate("", "claude-opus-4-8", _r(require_explicit=True), env=_STD_ENV)["ok"] is False
    assert model_policy.evaluate("claude-opus-4-8", "", _r(require_explicit=True), env=_STD_ENV)["ok"] is True


def test_evaluate_fails_open_on_collapsed_custom_mapping():
    # User maps all three tiers to ONE id — tier bounds become meaningless → never false-block.
    env = {
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "one-model",
        "ANTHROPIC_DEFAULT_SONNET_MODEL": "one-model",
        "ANTHROPIC_DEFAULT_OPUS_MODEL": "one-model",
    }
    assert model_policy.evaluate("", "one-model", _r(max_model="haiku"), env=env)["ok"] is True
    assert model_policy.evaluate("one-model", "", _r(required_model="haiku"), env=env)["ok"] is True
    assert model_policy.evaluate("", "one-model", _r(min_model="opus"), env=env)["ok"] is True


def test_evaluate_fable_exact_only():
    # fable classifies to no tier; exact matches by id/substring, and it can't rank a bound.
    r = model_policy.evaluate("claude-fable-5", "", _r(required_model="fable"), env=_STD_ENV)
    assert r["ok"] is True
    # a fable ceiling is unrankable → no enforcement (fails open).
    r2 = model_policy.evaluate("", "claude-opus-4-8", _r(max_model="fable"), env=_STD_ENV)
    assert r2["ok"] is True and r2["kind"] == "none"


def test_evaluate_blank_inherit_forces_explicit_not_fail_open():
    # No explicit model AND no session model to reason about → a bound violation (force an
    # explicit choice), NOT a fail-open. This preserves the original explore "must pin" case.
    assert model_policy.evaluate("", "", _r(required_model="haiku"), env=_STD_ENV)["ok"] is False
    assert model_policy.evaluate("", "", _r(max_model="sonnet"), env=_STD_ENV)["ok"] is False
    assert model_policy.evaluate("", "", _r(min_model="sonnet"), env=_STD_ENV)["ok"] is False


def test_evaluate_no_bound_passes():
    assert model_policy.evaluate("", "", _r(), env=_STD_ENV)["ok"] is True


# --- drift guard: shipped self_pinned entries must match the real agent frontmatter --------

def test_shipped_self_pinned_matches_frontmatter():
    """Every self_pinned entry in the SHIPPED model-policy.yaml must carry a required_model
    equal to that hs agent's actual frontmatter `model:`. Reads real files in the test suite
    (never the runtime hot path) so a frontmatter change that drifts from the policy is caught
    at CI, not by a false-block in production."""
    import yaml
    root = Path(__file__).resolve().parents[1]
    policy = yaml.safe_load((root / "data" / "model-policy.yaml").read_text(encoding="utf-8"))
    agents_dir = root / "plugins" / "hs" / "agents"
    pinned = {n: e for n, e in policy["agents"].items()
              if isinstance(e, dict) and e.get("self_pinned")}
    assert pinned, "expected self_pinned hs entries in the shipped policy"
    for name, entry in pinned.items():
        md = agents_dir / ("%s.md" % name)
        assert md.is_file(), "self_pinned '%s' has no agent file at %s" % (name, md)
        fm_model = None
        for line in md.read_text(encoding="utf-8").splitlines():
            if line.startswith("model:"):
                fm_model = line.split(":", 1)[1].strip()
                break
        assert fm_model, "agent '%s' declares no frontmatter model:" % name
        assert entry["required_model"] == fm_model, (
            "policy drift: %s required_model=%r but frontmatter model=%r"
            % (name, entry["required_model"], fm_model))
