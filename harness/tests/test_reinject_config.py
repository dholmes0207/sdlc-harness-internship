"""test_reinject_config.py — reader for reinject-strategy.yaml (cadence + budget
presets consumed by inject_prompt_context.py).

Mirrors model_policy's fail-open config-reader shape: an env-override test seam
(HARNESS_REINJECT_STRATEGY), never raises, permissive default on a missing or
corrupt file. The default (`balanced`) preset must reproduce today's welded
constants exactly — (every_turns=5, slim_budget_chars=1100) — so lifting the
constants into config never changes shipped default behavior.

Also covers `fill_ratio()` (P6/B3: adaptive-by-context-fill) + `resolve_adaptive()` —
the separate 2-key resolver that keeps the money contract above untouched (see
test_resolve_adaptive_never_mutates_resolve_two_key_contract).
"""
import json
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import reinject_config  # noqa: E402


def _write(tmp_path, text: str) -> Path:
    p = tmp_path / "reinject-strategy.yaml"
    p.write_text(text, encoding="utf-8")
    return p


# --- T1: default preset == today's behavior ------------------------------------

def test_default_preset_matches_shipped_config():
    # no env override -> reads the real shipped harness/data/reinject-strategy.yaml
    assert reinject_config.resolve() == {"every_turns": 5, "slim_budget_chars": 1100}


def test_explicit_balanced_preset_matches_today(tmp_path, monkeypatch):
    cfg = _write(tmp_path, """
preset: balanced
presets:
  balanced: {every_turns: 5, slim_budget_chars: 1100}
  aggressive: {every_turns: 3, slim_budget_chars: 1400}
  lazy: {every_turns: 8, slim_budget_chars: 1000}
""")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    assert reinject_config.resolve() == {"every_turns": 5, "slim_budget_chars": 1100}


# --- T2: named presets resolve to distinct non-default values ------------------

def test_aggressive_preset_shorter_cadence_larger_budget(tmp_path, monkeypatch):
    cfg = _write(tmp_path, """
preset: aggressive
presets:
  balanced: {every_turns: 5, slim_budget_chars: 1100}
  aggressive: {every_turns: 3, slim_budget_chars: 1400}
  lazy: {every_turns: 8, slim_budget_chars: 1000}
""")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    out = reinject_config.resolve()
    # exact non-default numbers, not just "!= default" (round-trip rule)
    assert out == {"every_turns": 3, "slim_budget_chars": 1400}
    assert out["every_turns"] < 5          # shorter cadence than balanced
    assert out["slim_budget_chars"] > 1100  # larger budget than balanced


def test_lazy_preset_longer_cadence_smaller_budget(tmp_path, monkeypatch):
    cfg = _write(tmp_path, """
preset: lazy
presets:
  balanced: {every_turns: 5, slim_budget_chars: 1100}
  aggressive: {every_turns: 3, slim_budget_chars: 1400}
  lazy: {every_turns: 8, slim_budget_chars: 1000}
""")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    out = reinject_config.resolve()
    assert out == {"every_turns": 8, "slim_budget_chars": 1000}
    assert out["every_turns"] > 5           # longer cadence than balanced
    assert out["slim_budget_chars"] < 1100  # smaller budget than balanced


def test_top_level_override_wins_over_preset(tmp_path, monkeypatch):
    cfg = _write(tmp_path, """
preset: aggressive
every_turns: 4
presets:
  balanced: {every_turns: 5, slim_budget_chars: 1100}
  aggressive: {every_turns: 3, slim_budget_chars: 1400}
""")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    out = reinject_config.resolve()
    # top-level every_turns overrides the aggressive preset's 3; budget still
    # comes from the preset since no top-level override for it.
    assert out == {"every_turns": 4, "slim_budget_chars": 1400}


# --- T5: fail-open on corrupt/missing config ------------------------------------

def test_missing_file_fails_open(tmp_path, monkeypatch):
    missing = tmp_path / "does-not-exist.yaml"
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(missing))
    assert reinject_config.resolve() == {"every_turns": 5, "slim_budget_chars": 1100}


def test_corrupt_yaml_fails_open(tmp_path, monkeypatch):
    cfg = _write(tmp_path, "preset: [this is not\n  a valid: mapping :::")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    assert reinject_config.resolve() == {"every_turns": 5, "slim_budget_chars": 1100}


def test_non_mapping_yaml_fails_open(tmp_path, monkeypatch):
    cfg = _write(tmp_path, "- just\n- a\n- list\n")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    assert reinject_config.resolve() == {"every_turns": 5, "slim_budget_chars": 1100}


def test_unknown_active_preset_fails_open_to_defaults(tmp_path, monkeypatch):
    cfg = _write(tmp_path, """
preset: does-not-exist
presets:
  balanced: {every_turns: 5, slim_budget_chars: 1100}
""")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    assert reinject_config.resolve() == {"every_turns": 5, "slim_budget_chars": 1100}


def test_never_raises_on_garbage_bytes(tmp_path, monkeypatch):
    cfg = tmp_path / "reinject-strategy.yaml"
    cfg.write_bytes(b"\xff\xfe\x00garbage-not-utf8")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    # must not raise
    assert reinject_config.resolve() == {"every_turns": 5, "slim_budget_chars": 1100}


# --- fill_ratio(): P6/B3 adaptive-by-context-fill signal ------------------------
# Real P8 probe numbers (probe-log.md): a live transcript's last assistant usage
# input=37, cache_read=133413, cache_creation=2658 -> fill=136108 tokens.
# 136108/200000 = 0.68054 | 136108/1_000_000 = 0.136108

_P8_USAGE = {"input_tokens": 37, "cache_read_input_tokens": 133413,
             "cache_creation_input_tokens": 2658}


def _transcript(tmp_path, records, name="transcript.jsonl") -> Path:
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return p


def _usage_record(model, usage=None):
    return {"type": "assistant", "message": {"model": model, "usage": usage or dict(_P8_USAGE)}}


# T1: fill math on the P8 numbers, default 200k window. env={} isolates the
# assertion from the real process environment — this dev shell itself carries
# ANTHROPIC_MODEL=...[1m] (BL-250's own trigger condition), so a test that wants
# "no env resolves" must say so explicitly rather than inherit os.environ.
def test_fill_ratio_math_default_200k_window(tmp_path):
    p = _transcript(tmp_path, [_usage_record("claude-opus-4-8")])
    ratio = reinject_config.fill_ratio(str(p), None, env={})
    assert ratio == pytest.approx(0.68054, abs=1e-3)


# T2: [1m]-tagged model id -> 1,000,000 window.
def test_fill_ratio_1m_tag_uses_1m_window(tmp_path):
    p = _transcript(tmp_path, [_usage_record("claude-opus-4-8[1m]")])
    ratio = reinject_config.fill_ratio(str(p), None, env={})
    assert ratio == pytest.approx(0.136108, abs=1e-3)


# T3: a non-opus/sonnet custom model id still gets the 1M window from its [1m] tag
# alone — proving the window classification is tag-based, not a family substring.
def test_fill_ratio_custom_model_id_no_hardcoded_family_substring(tmp_path):
    p = _transcript(tmp_path, [_usage_record("acme-widget-9[1m]")])
    ratio = reinject_config.fill_ratio(str(p), None, env={})
    assert ratio == pytest.approx(0.136108, abs=1e-3)


def test_fill_ratio_model_arg_overrides_transcript_model(tmp_path):
    p = _transcript(tmp_path, [_usage_record("claude-opus-4-8")])
    ratio = reinject_config.fill_ratio(str(p), "claude-opus-4-8[1m]", env={})
    assert ratio == pytest.approx(0.136108, abs=1e-3)


# --- BL-250: multi-source window resolution (env recovers the [1m] tag the ----
# transcript's message.model strips even on a live 1M session) --------------

# The core regression: a REAL 1M session's transcript records message.model
# WITHOUT the [1m] tag (CC's own recording behavior), so the raw transcript tag
# alone always under-detects. env ANTHROPIC_MODEL — the live session id CC sets,
# confirmed to carry the tag — must recover the true window. Before the fix this
# read ~0.68054 (the 200k mis-detection); after, ~0.136108 (correct 1M).
def test_fill_ratio_recovers_1m_via_anthropic_model_env(tmp_path):
    p = _transcript(tmp_path, [_usage_record("claude-opus-4-8")])  # untagged, as CC records it
    env = {"ANTHROPIC_MODEL": "claude-opus-4-8[1m]"}
    ratio = reinject_config.fill_ratio(str(p), None, env=env)
    assert ratio == pytest.approx(0.136108, abs=1e-3)


# No ANTHROPIC_MODEL in env (e.g. an older CC build, or a wrapped invocation that
# doesn't set it) — fall back to matching the transcript's untagged model against
# the tier-default env vars; a tagged match still recovers 1M.
def test_fill_ratio_recovers_1m_via_tier_default_match(tmp_path):
    p = _transcript(tmp_path, [_usage_record("claude-opus-4-8")])
    env = {"ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8[1m]"}
    ratio = reinject_config.fill_ratio(str(p), None, env=env)
    assert ratio == pytest.approx(0.136108, abs=1e-3)


# A tier-default match that does NOT itself carry the tag must not manufacture
# one — falls through to the untagged raw-id default (200k), never guesses 1M.
def test_fill_ratio_tier_default_match_without_tag_stays_200k(tmp_path):
    p = _transcript(tmp_path, [_usage_record("claude-opus-4-8")])
    env = {"ANTHROPIC_DEFAULT_OPUS_MODEL": "claude-opus-4-8"}  # no [1m]
    ratio = reinject_config.fill_ratio(str(p), None, env=env)
    assert ratio == pytest.approx(0.68054, abs=1e-3)


# Nothing resolvable at all (empty env, an id no env var matches) — the window
# resolver itself never returns None or guesses 1M; it fails open LOW (200k).
def test_resolve_window_fails_open_to_200k_when_nothing_resolves():
    assert reinject_config._resolve_window(None, "totally-unknown-vendor-id", {}) == 200_000
    assert reinject_config._resolve_window(None, None, {}) == 200_000


# T4: unclassifiable model / unreadable transcript -> None (fail-open to turn-count).
def test_fill_ratio_missing_transcript_returns_none(tmp_path):
    missing = tmp_path / "does-not-exist.jsonl"
    assert reinject_config.fill_ratio(str(missing), None) is None


def test_fill_ratio_no_transcript_path_returns_none():
    assert reinject_config.fill_ratio(None, None) is None
    assert reinject_config.fill_ratio("", None) is None


def test_fill_ratio_no_usage_record_returns_none(tmp_path):
    p = _transcript(tmp_path, [{"type": "user", "message": {"content": "hi"}}])
    assert reinject_config.fill_ratio(str(p), None) is None


def test_fill_ratio_empty_model_id_returns_none(tmp_path):
    p = _transcript(tmp_path, [_usage_record("")])
    assert reinject_config.fill_ratio(str(p), None) is None


def test_fill_ratio_torn_json_lines_tolerated(tmp_path):
    p = tmp_path / "transcript.jsonl"
    p.write_text(json.dumps(_usage_record("claude-opus-4-8")) + "\n"
                 + '{"type": "assistant", "message": {"broken"\n', encoding="utf-8")
    ratio = reinject_config.fill_ratio(str(p), None, env={})
    assert ratio == pytest.approx(0.68054, abs=1e-3)


# --- fill_ratio(): supporting behavior -------------------------------------------

def test_fill_ratio_uses_last_usage_record(tmp_path):
    older = _usage_record("claude-opus-4-8", {"input_tokens": 10, "cache_read_input_tokens": 10,
                                               "cache_creation_input_tokens": 10})
    newest = _usage_record("claude-opus-4-8")
    p = _transcript(tmp_path, [older, newest])
    ratio = reinject_config.fill_ratio(str(p), None, env={})
    assert ratio == pytest.approx(0.68054, abs=1e-3)


def test_fill_ratio_missing_usage_keys_treated_as_zero(tmp_path):
    p = _transcript(tmp_path, [_usage_record("claude-opus-4-8", {"input_tokens": 100})])
    ratio = reinject_config.fill_ratio(str(p), None, env={})
    assert ratio == pytest.approx(100 / 200000)


def test_fill_ratio_never_raises_on_garbage_bytes(tmp_path):
    p = tmp_path / "transcript.jsonl"
    p.write_bytes(b"\xff\xfe\x00garbage-not-utf8")
    assert reinject_config.fill_ratio(str(p), None) is None


# --- resolve_adaptive(): a SEPARATE resolver — never mutates resolve()'s 2-key ---
# contract (test_default_preset_matches_shipped_config above asserts EXACT dict
# equality; adding keys there would break it).

def test_resolve_adaptive_default_preset_is_off():
    # the shipped active preset is `balanced`, which carries no mode/threshold
    assert reinject_config.resolve_adaptive() == {"mode": None, "threshold": None}


def test_resolve_adaptive_reads_the_active_adaptive_preset(tmp_path, monkeypatch):
    cfg = _write(tmp_path, """
preset: adaptive
presets:
  balanced: {every_turns: 5, slim_budget_chars: 1100}
  adaptive: {mode: fill, threshold: 0.7}
""")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    assert reinject_config.resolve_adaptive() == {"mode": "fill", "threshold": 0.7}


def test_resolve_adaptive_never_mutates_resolve_two_key_contract(tmp_path, monkeypatch):
    cfg = _write(tmp_path, """
preset: adaptive
presets:
  balanced: {every_turns: 5, slim_budget_chars: 1100}
  adaptive: {every_turns: 5, slim_budget_chars: 1100, mode: fill, threshold: 0.7}
""")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    assert reinject_config.resolve() == {"every_turns": 5, "slim_budget_chars": 1100}
    assert reinject_config.resolve_adaptive() == {"mode": "fill", "threshold": 0.7}


def test_resolve_adaptive_fails_open_on_missing_file(tmp_path, monkeypatch):
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(tmp_path / "missing.yaml"))
    assert reinject_config.resolve_adaptive() == {"mode": None, "threshold": None}


def test_resolve_adaptive_fails_open_on_corrupt_yaml(tmp_path, monkeypatch):
    cfg = _write(tmp_path, "preset: [this is not\n  a valid: mapping :::")
    monkeypatch.setenv("HARNESS_REINJECT_STRATEGY", str(cfg))
    assert reinject_config.resolve_adaptive() == {"mode": None, "threshold": None}


def test_resolve_adaptive_shipped_config_stays_balanced_default():
    # the real shipped harness/data/reinject-strategy.yaml must NOT default-flip to
    # adaptive — adaptive ships opt-in only.
    assert reinject_config.resolve_adaptive() == {"mode": None, "threshold": None}
