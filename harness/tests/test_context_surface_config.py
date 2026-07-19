#!/usr/bin/env python3
"""Tests for context_surface_config — the human-facing systemMessage layer over
build_context reminder injection (ship/dev split via HARNESS_CONTEXT_SURFACE)."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import context_surface_config as cs  # noqa: E402


def _write(tmp_path, body):
    p = tmp_path / "context-surface.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def setup_function(_):
    cs._reset()


def teardown_function(_):
    os.environ.pop("HARNESS_CONTEXT_SURFACE", None)
    cs._reset()


def test_defaults_when_no_file(monkeypatch, tmp_path):
    # env points at a missing file → fall back to code defaults (fail-open),
    # not the tracked ship file, when we force the missing path with no ship fallback.
    missing = tmp_path / "nope.yaml"
    monkeypatch.setenv("HARNESS_CONTEXT_SURFACE", str(missing))
    # ship file exists in repo; loader falls back to it. Assert shape is well-formed.
    ups = cs.event("user_prompt_submit")
    stop = cs.event("stop")
    assert "system_message" in ups and "verbosity" in ups
    assert stop["model_channel"] in ("reason", "additionalContext")


def test_env_override_dev_double_verbose(monkeypatch, tmp_path):
    p = _write(tmp_path, (
        "user_prompt_submit:\n  system_message: true\n  verbosity: full\n"
        "stop:\n  system_message: true\n  verbosity: full\n"
        "  model_channel: additionalContext\n"))
    monkeypatch.setenv("HARNESS_CONTEXT_SURFACE", str(p))
    cs._reset()
    assert cs.event("user_prompt_submit") == {"system_message": True, "verbosity": "full"}
    stop = cs.event("stop")
    assert stop["system_message"] is True
    assert stop["verbosity"] == "full"
    assert stop["model_channel"] == "additionalContext"


def test_malformed_file_fails_open_to_defaults(monkeypatch, tmp_path):
    p = _write(tmp_path, "::: not yaml :::\n  - broken")
    monkeypatch.setenv("HARNESS_CONTEXT_SURFACE", str(p))
    cs._reset()
    # never raises; returns a well-formed shape
    stop = cs.event("stop")
    assert stop["model_channel"] in ("reason", "additionalContext")
    assert isinstance(cs.event("user_prompt_submit")["system_message"], bool)


def test_partial_file_merges_over_defaults(monkeypatch, tmp_path):
    # only stop.system_message set → other keys keep their default
    p = _write(tmp_path, "stop:\n  system_message: true\n")
    monkeypatch.setenv("HARNESS_CONTEXT_SURFACE", str(p))
    cs._reset()
    stop = cs.event("stop")
    assert stop["system_message"] is True
    assert stop["model_channel"] in ("reason", "additionalContext")  # default preserved


def test_render_human_full_is_verbatim_after_leading_newline():
    # A leading newline pushes the body below CC's "<Event> says:" label line.
    text = "## Paths\nReports: /x\n## Rules\n- rules: y"
    out = cs.render_human(text, "full")
    assert out == "\n" + text
    assert out.startswith("\n")


def test_render_human_summary_lists_headers_not_body():
    text = "## Paths\nReports: /x/y/z\n## Plan Context\n- Plan: p\n## Rules\n- rules: y"
    out = cs.render_human(text, "summary")
    assert out.startswith("\n")  # body sits on its own line under the label
    assert "Paths" in out and "Plan Context" in out and "Rules" in out
    # summary must NOT carry the heavy body lines
    assert "/x/y/z" not in out
    assert len(out) < len(text)


def test_render_human_summary_counts_folded_nudges():
    text = ("## Paths\nReports: /x\n\n"
            "[backlog-capture] deferred work...\n[goal-cycle] tick ended...\n")
    out = cs.render_human(text, "summary")
    assert "2" in out  # two folded nudges surfaced


def test_render_human_summary_lists_bracket_labels():
    # the voice/subagent register uses [Label ...] bracket tags, not ## headers;
    # summary must surface those short labels instead of falling back to "context".
    text = ("[Terminal voice - active session settings]\nvoice_level=9\n"
            "[Reader register - audience: plain]\n"
            "[Code style - senior]\n"
            "[Harness subagent: hs:tester] You are running inside a harness...\n")
    out = cs.render_human(text, "summary")
    assert "Terminal voice" in out
    assert "Reader register" in out
    assert "Code style" in out
    assert "Harness subagent" in out
    assert "context" not in out       # real labels found → no fallback
    assert "voice_level=9" not in out  # heavy body still excluded


def test_render_human_summary_bracket_excludes_nudge_tags():
    # nudge tags also start with '[' but are COUNTED, never listed as a section.
    text = "[Terminal voice - x]\n[backlog-capture] deferred\n[goal-cycle] tick\n"
    out = cs.render_human(text, "summary")
    assert "Terminal voice" in out
    assert "backlog-capture" not in out  # counted, not listed as a label
    assert "2" in out                    # both nudges still counted


def test_render_human_summary_mixed_headers_and_brackets():
    # a text with BOTH ## headers and [ ] labels surfaces both, in order.
    text = "[Terminal voice - x]\n## Paths\nReports: /y\n"
    out = cs.render_human(text, "summary")
    assert "Terminal voice" in out and "Paths" in out
    assert "/y" not in out


# --- unified emission chokepoint (build_payload / emit) ---------------------

def test_new_events_present_with_sane_shape():
    # session_start + subagent_start join the config surface so voice_inject and
    # subagent_init route through the SAME co-emit layer as UPS/Stop.
    for ev in ("session_start", "subagent_start"):
        knobs = cs.event(ev)
        assert isinstance(knobs.get("system_message"), bool)
        assert knobs.get("verbosity") in ("summary", "full")


def test_build_payload_ups_additionalcontext_no_sysmsg_when_disabled(monkeypatch, tmp_path):
    # system_message off -> model gets additionalContext, NO human systemMessage.
    p = _write(tmp_path, "user_prompt_submit:\n  system_message: false\n")
    monkeypatch.setenv("HARNESS_CONTEXT_SURFACE", str(p))
    cs._reset()
    out = cs.build_payload("user_prompt_submit", "## Paths\nx")
    assert out["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    assert out["hookSpecificOutput"]["additionalContext"] == "## Paths\nx"
    assert "systemMessage" not in out


def test_build_payload_ups_sysmsg_when_enabled(monkeypatch, tmp_path):
    p = _write(tmp_path, "user_prompt_submit:\n  system_message: true\n  verbosity: full\n")
    monkeypatch.setenv("HARNESS_CONTEXT_SURFACE", str(p))
    cs._reset()
    out = cs.build_payload("user_prompt_submit", "## Paths\nx")
    assert out["hookSpecificOutput"]["additionalContext"] == "## Paths\nx"
    assert out["systemMessage"] == "\n## Paths\nx"  # full = verbatim + leading \n


def test_build_payload_session_start_hookevent_name():
    out = cs.build_payload("session_start", "voice text")
    assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart"
    assert out["hookSpecificOutput"]["additionalContext"] == "voice text"


def test_build_payload_subagent_start_hookevent_name():
    out = cs.build_payload("subagent_start", "register text")
    assert out["hookSpecificOutput"]["hookEventName"] == "SubagentStart"


def test_build_payload_stop_reason_channel_default():
    # default stop model_channel = reason -> decision:block/reason, NOT hookSpecificOutput.
    out = cs.build_payload("stop", "loop text")
    assert out["decision"] == "block"
    assert out["reason"] == "loop text"
    assert "hookSpecificOutput" not in out


def test_build_payload_stop_additionalcontext_channel(monkeypatch, tmp_path):
    p = _write(tmp_path, "stop:\n  system_message: true\n  verbosity: full\n"
               "  model_channel: additionalContext\n")
    monkeypatch.setenv("HARNESS_CONTEXT_SURFACE", str(p))
    cs._reset()
    out = cs.build_payload("stop", "loop text")
    assert out["hookSpecificOutput"]["hookEventName"] == "Stop"
    assert out["hookSpecificOutput"]["additionalContext"] == "loop text"
    assert "decision" not in out
    assert out["systemMessage"].endswith("loop text")  # double render (dev), full=verbatim


def test_emit_writes_json_payload(capsys):
    cs.emit("user_prompt_submit", "hello")
    import json
    captured = json.loads(capsys.readouterr().out)
    assert captured["hookSpecificOutput"]["additionalContext"] == "hello"
