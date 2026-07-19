"""test_register_block.py — shared register builder (SSOT for SessionStart +
SubagentStart context).

register_block owns build_register (the full register block: voice + floor +
scope-fence + audience/code_style essence + profile BODY) and body_for (reads a
profile file, slices to its MANDATORY/FORBIDDEN directives, fail-open). Both
hooks call it so they cannot drift.

Contract under test:
- body_for short-circuits on level=None BEFORE touching the filesystem (the
  default-install path — not an edge), returns "" and never logs a level-None path.
- body_for injects the real directive line for a set level.
- body_for slices to MANDATORY/FORBIDDEN only — no H1, no preamble, no embedded
  "Scope Fence" section (so the register's own scope-fence appears exactly once).
- a missing/unreadable profile file degrades to "" (never raises).
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import register_block  # noqa: E402


def _prefs(**over):
    """Minimal flat prefs dict (shape of output_config.resolve_all)."""
    base = {
        "voice_level": 5,
        "terminal_voice_level": 3,
        "persona": "none",
        "no_markdown": False,
        "interview_rigor": "standard",
        "action_prompting": "standard",
        "audience": None,
        "code_style": None,
        "humanize": None,
    }
    base.update(over)
    return base


def test_thinking_language_directive_emitted_only_when_it_differs_from_output():
    """The reasoning-language consumer: build_register injects a 'reason in <lang>' directive
    when thinking_language differs from the output language, and stays silent when they match
    (no point telling the model to think in the language it already writes)."""
    differ = register_block.build_register(_prefs(thinking_language="en", language="vi"))
    assert "Reasoning language" in differ
    assert "thinking_language=en" in differ
    assert "output stays in vi" in differ

    same = register_block.build_register(_prefs(thinking_language="en", language="en"))
    assert "Reasoning language" not in same


def test_thinking_language_absent_emits_nothing():
    """A prefs dict without the key (older resolve_all) must not crash or emit the directive."""
    out = register_block.build_register(_prefs())
    assert "Reasoning language" not in out


def test_body_for_none_short_circuits(monkeypatch):
    """level=None (default install) → "" without ever building a path or opening a
    file, and build_register on a default config carries the essence but no body
    and never raises. This is the FIRST red — the common path, not an edge."""
    calls = []
    real_open = open

    def _spy_open(*a, **k):
        calls.append(a[0] if a else None)
        return real_open(*a, **k)

    monkeypatch.setattr("builtins.open", _spy_open)
    assert register_block.body_for("code-style", None) == ""
    assert register_block.body_for("audience", None) == ""
    # No file access at all for the None path.
    assert not any("level-None" in str(c) for c in calls), (
        "body_for(None) built a level-None path: %s" % calls)

    ctx = register_block.build_register(_prefs())
    assert "voice_level=5" in ctx
    assert "Full code-style directives" not in ctx
    assert "Full audience directives" not in ctx


def test_register_has_essence_and_mandatory_read_pointer_no_body():
    """Diet C: a set level carries the one-line essence + a MANDATORY directive to
    READ the profile file first — NOT the dumped MANDATORY/FORBIDDEN body. The body
    text (e.g. 'Lead with trade-offs', 'So What') no longer inflates every turn."""
    ctx = register_block.build_register(_prefs(audience=0, code_style=3))
    # essence lines survive
    assert "audience=0/5" in ctx and "code_style=3/5" in ctx
    # mandatory read-pointer, per kind
    assert "output-styles/audience-level-0.md" in ctx
    assert "output-styles/code-style-level-3.md" in ctx
    assert ctx.count("MANDATORY: read") >= 2, "each set level needs a read directive"
    # the dumped MANDATORY/FORBIDDEN body is gone (essence words like "so what" /
    # "lead with trade-offs" legitimately remain in the one-line essence — assert on
    # body-only markers instead).
    assert "Partnership Voice" not in ctx, "audience body section leaked"
    assert "FORBIDDEN in this prose register" not in ctx, "audience body leaked"
    assert "MANDATORY CODE DIRECTIVES" not in ctx, "code-style body leaked"
    assert "--- Full audience directives" not in ctx
    assert "--- Full code-style directives" not in ctx


def test_default_none_levels_carry_no_audience_or_codestyle_section():
    """Diet C touches only the set-level branch; the default-install path (audience
    and code_style both None) emits no reader/code register at all — unchanged."""
    ctx = register_block.build_register(_prefs())
    assert "audience=" not in ctx and "code_style=" not in ctx
    assert "MANDATORY: read" not in ctx
    assert "voice_level=5" in ctx  # voice register itself is untouched


def test_body_slices_mandatory_forbidden_only():
    """body_for keeps the MANDATORY/FORBIDDEN blocks and drops the H1, preamble,
    and the embedded 'Scope Fence' section — so the register's scope-fence shows
    exactly once."""
    body = register_block.body_for("code-style", 3)
    assert "MANDATORY" in body and "FORBIDDEN" in body
    assert "# Code Style Level 3" not in body, "H1 leaked into body slice"
    assert "This profile shapes generated CODE" not in body, "preamble leaked"

    aud = register_block.body_for("audience", 0)
    assert "Scope Fence" not in aud, "embedded Scope Fence section leaked into body"

    ctx = register_block.build_register(_prefs(audience=0, code_style=3))
    assert ctx.count("Scope-fence:") == 1, (
        "register scope-fence must appear exactly once, got %d" % ctx.count("Scope-fence:"))


def test_body_missing_file_fallbacks_no_raise(monkeypatch):
    """A level that resolves to a missing/unreadable file degrades to "" — never
    raises (fail-open)."""
    def _boom(*a, **k):
        raise OSError("simulated missing profile")

    monkeypatch.setattr("builtins.open", _boom)
    try:
        out = register_block.body_for("code-style", 3)
    except Exception as e:  # noqa: BLE001
        pytest.fail("body_for raised on unreadable file: %s" % e)
    assert out == ""


def test_body_for_retained_for_on_demand_readers(monkeypatch):
    """C stops build_register from DUMPING the body, but body_for stays a public
    helper (a skill may slice a profile on demand). It still returns the sliced
    directives for a set level and fails open to "" on error."""
    assert "MANDATORY" in register_block.body_for("code-style", 3)

    def _boom(*a, **k):
        raise OSError("simulated")

    monkeypatch.setattr("builtins.open", _boom)
    assert register_block.body_for("audience", 0) == ""
