"""test_trigger_eval.py — trigger-eval runner for skill-creator (per-skill activation).

trigger_eval measures whether ONE skill's description causes claude -p to activate
that skill across a set of queries. Ported from ClaudeKit run_eval.py: it drops a
throwaway command file for the candidate skill, runs `claude -p` with stream-json,
and detects a Skill/Read tool_use carrying the skill's unique name.

These tests exercise the pure parsing core (parse_activation) and the subprocess
wiring (env scrub, command-file cleanup, aggregation) with canned stream-json and a
stubbed process — no real claude invocation.
"""
import glob
import json
import os
import sys
from pathlib import Path

import pytest

_SC_SCRIPTS = (
    Path(__file__).resolve().parent.parent
    / "plugins" / "hs" / "skills" / "skill-creator" / "scripts"
)
if str(_SC_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SC_SCRIPTS))

import trigger_eval as te  # noqa: E402


def _ev(d):
    return json.dumps(d)


# --- parse_activation (pure) ---------------------------------------------------

def test_parse_activation_skill_tool():
    name = "demo-skill-abcd1234"
    lines = [
        _ev({"type": "stream_event", "event": {"type": "content_block_start",
             "content_block": {"type": "tool_use", "name": "Skill"}}}),
        _ev({"type": "stream_event", "event": {"type": "content_block_delta",
             "delta": {"type": "input_json_delta",
                       "partial_json": '{"skill":"' + name + '"}'}}}),
    ]
    assert te.parse_activation(lines, name) is True


def test_parse_activation_read_tool():
    name = "demo-skill-abcd1234"
    lines = [
        _ev({"type": "stream_event", "event": {"type": "content_block_start",
             "content_block": {"type": "tool_use", "name": "Read"}}}),
        _ev({"type": "stream_event", "event": {"type": "content_block_delta",
             "delta": {"type": "input_json_delta",
                       "partial_json": '{"file_path":".claude/commands/' + name + '.md"}'}}}),
    ]
    assert te.parse_activation(lines, name) is True


def test_parse_activation_none():
    lines = [_ev({"type": "stream_event", "event": {"type": "message_stop"}})]
    assert te.parse_activation(lines, "demo-skill-x") is False


def test_parse_activation_other_tool():
    # A non-Skill/Read tool_use means the model did the task directly → not triggered.
    lines = [_ev({"type": "stream_event", "event": {"type": "content_block_start",
             "content_block": {"type": "tool_use", "name": "Bash"}}})]
    assert te.parse_activation(lines, "demo-skill-x") is False


def test_parse_activation_assistant_fallback():
    # Non-partial path: detection from a full assistant message tool_use.
    name = "demo-skill-abcd1234"
    lines = [_ev({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Skill", "input": {"skill": name}}]}})]
    assert te.parse_activation(lines, name) is True


def test_parse_activation_result_only():
    lines = [_ev({"type": "result"})]
    assert te.parse_activation(lines, "demo-skill-x") is False


# --- _safe_token (path-traversal hardening) -----------------------------------

def test_safe_token_strips_separators():
    assert "/" not in te._safe_token("../../etc/passwd")
    assert "\\" not in te._safe_token("a\\b")
    assert te._safe_token("a/b/c") == "a_b_c"
    assert te._safe_token("") == "skill"
    assert te._safe_token("../..") == "skill"
    # legitimate names with dots/dashes survive
    assert te._safe_token("hs:ui-ux") == "hs_ui-ux"


# --- run_single_query (subprocess wiring) -------------------------------------

class _FakeProc:
    def __init__(self):
        self.stdout = None
        self._killed = False

    def poll(self):
        return 0  # already exited → finally won't kill

    def kill(self):
        self._killed = True

    def wait(self):
        return 0


def _patch_uuid(monkeypatch, hex8="abcd1234ff"):
    class _U:
        hex = hex8
    monkeypatch.setattr(te.uuid, "uuid4", lambda: _U())


def test_run_single_query_scrubs_claudecode(tmp_path, monkeypatch):
    captured = {}

    def fake_popen(cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        captured["cmd"] = cmd
        captured["cwd"] = kwargs.get("cwd")
        return _FakeProc()

    _patch_uuid(monkeypatch)
    clean_name = "demo-skill-abcd1234"  # skill_name + "-skill-" + hex[:8]
    monkeypatch.setattr(te.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(te, "_read_stream_lines", lambda proc, timeout: iter([
        _ev({"type": "stream_event", "event": {"type": "content_block_start",
             "content_block": {"type": "tool_use", "name": "Skill"}}}),
        _ev({"type": "stream_event", "event": {"type": "content_block_delta",
             "delta": {"type": "input_json_delta",
                       "partial_json": '{"skill":"' + clean_name + '"}'}}}),
    ]))
    monkeypatch.setenv("CLAUDECODE", "1")

    res = te.run_single_query("a query", "demo", "the description",
                              project_root=str(tmp_path))
    assert res is True
    assert "CLAUDECODE" not in captured["env"]
    assert "claude" in captured["cmd"][0]
    assert "--include-partial-messages" in captured["cmd"]


def test_run_single_query_unlinks_command_on_exception(tmp_path, monkeypatch):
    _patch_uuid(monkeypatch)
    monkeypatch.setattr(te.subprocess, "Popen", lambda cmd, **kw: _FakeProc())

    def boom(proc, timeout):
        raise RuntimeError("stream blew up")

    monkeypatch.setattr(te, "_read_stream_lines", boom)
    with pytest.raises(RuntimeError):
        te.run_single_query("q", "demo", "desc", project_root=str(tmp_path))

    # throwaway command file must be cleaned up even on the exception path
    leftover = glob.glob(str(tmp_path / ".claude" / "commands" / "demo-skill-*.md"))
    assert leftover == []


def test_run_single_query_kills_live_process(tmp_path, monkeypatch):
    _patch_uuid(monkeypatch)
    proc = _FakeProc()
    proc.poll = lambda: None  # still running → finally must kill it
    monkeypatch.setattr(te.subprocess, "Popen", lambda cmd, **kw: proc)
    monkeypatch.setattr(te, "_read_stream_lines", lambda p, t: iter([]))  # no detection
    res = te.run_single_query("q", "demo", "desc", project_root=str(tmp_path))
    assert res is False
    assert proc._killed is True


# --- _read_stream_lines (IO seam: discard trailing partial) -------------------

class _PipeProc:
    """A process-like object whose stdout is a real OS pipe pre-loaded with bytes."""

    def __init__(self, data: bytes):
        r, w = os.pipe()
        os.write(w, data)
        os.close(w)  # EOF after the data
        self.stdout = os.fdopen(r, "rb", buffering=0)

    def poll(self):
        return None  # rely on EOF (os.read → b"") to end the loop

    def kill(self):
        pass

    def wait(self):
        return 0


def test_read_stream_lines_drops_trailing_partial():
    # Two complete lines + one incomplete tail (no newline). The tail is discarded.
    proc = _PipeProc(b'{"x":1}\n{"y":2}\n{"partial"')
    lines = list(te._read_stream_lines(proc, timeout=5))
    assert lines == ['{"x":1}', '{"y":2}']


class _ExitedPipeProc(_PipeProc):
    """A process that has ALREADY EXITED (poll() != None) with complete lines still
    buffered — models a fast/cached run that emits its whole stream then exits before
    the select() loop drains it."""

    def poll(self):
        return 0  # already exited → forces the poll-exit branch


def test_read_stream_lines_drains_complete_lines_after_process_exit():
    # The poll-exit branch must yield the COMPLETE buffered lines, not drop them; the
    # trailing partial is still discarded. Dropping these scored an activated skill
    # as a no-trigger and silently biased the optimization oracle.
    proc = _ExitedPipeProc(b'{"x":1}\n{"y":2}\n{"partial"')
    lines = list(te._read_stream_lines(proc, timeout=5))
    assert lines == ['{"x":1}', '{"y":2}']


# --- run_eval (aggregation) ---------------------------------------------------

def test_run_eval_aggregates(tmp_path, monkeypatch):
    plan = {"q1": True, "q2": False, "q3": False}

    def stub(query, skill_name, description, *, timeout, project_root, model=None):
        return plan[query]

    monkeypatch.setattr(te, "run_single_query", stub)
    eval_set = [
        {"query": "q1", "should_trigger": True},   # triggers, expected → pass
        {"query": "q2", "should_trigger": False},  # no trigger, expected → pass
        {"query": "q3", "should_trigger": True},   # no trigger, expected trigger → fail
    ]
    out = te.run_eval(eval_set, "demo", "desc", num_workers=1,
                      project_root=tmp_path, runs_per_query=1)
    assert out["summary"]["total"] == 3
    assert out["summary"]["passed"] == 2
    assert out["summary"]["failed"] == 1
    by = {r["query"]: r["pass"] for r in out["results"]}
    assert by["q1"] is True
    assert by["q2"] is True
    assert by["q3"] is False


# --- main() CLI (validate-mode entrypoint) ------------------------------------

def test_cli_parses_eval_set(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(
        te, "run_single_query",
        lambda q, n, d, *, timeout, project_root, model=None: q == "yes-query")
    desc = tmp_path / "desc.txt"
    desc.write_text("A demo skill. Use when demoing.")
    es = tmp_path / "eval.json"
    es.write_text(json.dumps([
        {"query": "yes-query", "should_trigger": True},
        {"query": "no-query", "should_trigger": False},
    ]))
    rc = te.main(["--skill", "hs:demo", "--description-file", str(desc),
                  "--eval-set", str(es), "--project-root", str(tmp_path), "--runs", "1"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["summary"]["total"] == 2
    assert out["summary"]["passed"] == 2


def test_eval_validate_reference_clean():
    import re as _re
    ref = (Path(__file__).resolve().parent.parent / "plugins" / "hs" / "skills"
           / "skill-creator" / "references" / "eval-validate.md")
    assert ref.exists()
    text = ref.read_text()
    assert not _re.search(r"\.claude/(skills|hooks)/", text)
    assert "claudekit" not in text.lower()
    assert not _re.search(r"\bck:[a-z]", text)


# --- eval agent templates (Phase C: bundled prompt templates) -----------------

def test_eval_templates_present_and_clean():
    import re as _re
    agents = (Path(__file__).resolve().parent.parent / "plugins" / "hs" / "skills"
              / "skill-creator" / "agents")
    for name in ("grader.md", "comparator.md", "analyzer.md"):
        f = agents / name
        assert f.exists(), f"missing eval template {name}"
        text = f.read_text()
        assert not _re.search(r"\.claude/(skills|hooks)/", text), name
        assert "claudekit" not in text.lower(), name
        assert not _re.search(r"\bck:[a-z]", text), name


def test_eval_templates_not_registered_agents():
    # The eval templates are bundled assets, NOT roster agents.
    roster = (Path(__file__).resolve().parent.parent / "plugins" / "hs" / "agents")
    for name in ("grader.md", "comparator.md", "analyzer.md"):
        assert not (roster / name).exists(), f"{name} must not be a registered agent"
