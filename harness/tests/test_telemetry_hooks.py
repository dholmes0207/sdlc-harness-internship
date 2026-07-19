"""test_telemetry_hooks.py — integration suite for the Python telemetry hooks.

Each hook is exercised by importing it directly (no subprocess) with the sink
redirected to a tmp HARNESS_STATE_DIR so real sinks are untouched.
PYTEST_CURRENT_TEST is cleared so writes actually happen inside the test body
(the module-level disabled() check reads it at call time).

Harness re-home of the source corpus suite: env knobs are HARNESS_* (not CK_*),
sinks live under HARNESS_STATE_DIR/telemetry, the script matcher targets
harness/scripts and harness/e2e (no skill-tree path-shape), and hooks import
from harness/hooks + harness/scripts.

Covers:
  - track_skill_invocation: PreToolUse:Skill, UserPromptExpansion, non-skill
  - track_script_execution: harness-script match, error exit inference, non-script
  - emit_session_summary: transcript → skills + files + duration
  - fail-open: HARNESS_TELEMETRY_DISABLED → continue:true, no writes
  - JSONL non-forgery: skill name with embedded newline → 1 physical line
  - dedup: same session|skill|minute → exactly 1 record
  - script-path filter: only harness/scripts|e2e scripts in execution position recorded
"""

import importlib
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for _d in (str(_SCRIPTS), str(_HOOKS)):
    if _d not in sys.path:
        sys.path.insert(0, _d)


def _reload_lib(tmp_path, monkeypatch, extra=None):
    """Reload telemetry_paths with the sink redirected to tmp_path/state."""
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("HARNESS_SESSIONS_DIR", str(tmp_path / "sessions"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    monkeypatch.setenv("HARNESS_USER", "alice")  # hermetic actor (no git shell-out)
    if extra:
        for k, v in extra.items():
            monkeypatch.setenv(k, v)
    # Drop hook_runtime too so its per-process config cache is re-read fresh
    # (no HARNESS_HOOK_CONFIG here → the real config loads; telemetry defaults on).
    # telemetry_paths is reloaded in place (not popped) so its module identity
    # survives for other test files that hold a reference and reload it.
    for mod_name in ("hook_runtime",):
        sys.modules.pop(mod_name, None)
    import telemetry_paths
    importlib.reload(telemetry_paths)
    return telemetry_paths


def _reload_hook(hook_module, tmp_path, monkeypatch, extra=None):
    """Reload a hook module with fresh env."""
    _reload_lib(tmp_path, monkeypatch, extra)
    sys.modules.pop(hook_module, None)
    mod = importlib.import_module(hook_module)
    importlib.reload(mod)
    return mod


def _sink_lines(tmp_path, name):
    # Union legacy sink + per-session shards (P2 writes session records to
    # `<stem>-<disc>.jsonl`). Mirrors the read-side union so a write test reads
    # every record regardless of sharding, without weakening its count.
    d = tmp_path / "state" / "telemetry"
    if not d.exists():
        return []
    stem, dot, ext = name.rpartition(".")
    pats = [name] if not dot else [name, "%s-*.%s" % (stem, ext)]
    files = []
    for pat in pats:
        files.extend(sorted(d.glob(pat)))
    seen, out = set(), []
    for p in files:
        if p in seen or p.name.endswith(".bak"):
            continue
        seen.add(p)
        out.extend(json.loads(l) for l in p.read_text().splitlines() if l.strip())
    return out


# ---------------------------------------------------------------------------
# track_skill_invocation
# ---------------------------------------------------------------------------

class TestTrackSkillInvocation:
    def test_pretooluse_skill_payload_writes_one_invocations_line(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_skill_invocation", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))

        mod.main(json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "hs:plan"},
            "session_id": "s1",
        }))

        out = "".join(out_parts)
        assert '"continue": true' in out or '"continue":true' in out
        lines = _sink_lines(tmp_path, "invocations.jsonl")
        assert len(lines) == 1
        assert lines[0]["skill"] == "hs:plan"
        assert lines[0]["via"] == "PreToolUse:Skill"

    def test_userpromptexpansion_payload_slash_stripped(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_skill_invocation", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))

        mod.main(json.dumps({
            "hook_event_name": "UserPromptExpansion",
            "command": "/cook --auto plan.md",
            "session_id": "s2",
        }))

        out = "".join(out_parts)
        assert '"continue"' in out
        lines = _sink_lines(tmp_path, "invocations.jsonl")
        assert len(lines) == 1
        assert lines[0]["skill"] == "cook"
        assert lines[0]["via"] == "UserPromptExpansion"

    def test_userpromptexpansion_command_name_field_is_recorded(self, tmp_path, monkeypatch):
        # The live host carries the skill in `command_name` (a structured field),
        # not the older `command`. A user-typed /hs:test must be captured.
        mod = _reload_hook("track_skill_invocation", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))

        mod.main(json.dumps({
            "hook_event_name": "UserPromptExpansion",
            "command_name": "hs:test",
            "command_args": "kill old cron and test the slash capture",
            "expansion_type": "slash_command",
            "command_source": "plugin",
            "session_id": "s-cmdname",
        }))

        lines = _sink_lines(tmp_path, "invocations.jsonl")
        assert len(lines) == 1
        assert lines[0]["skill"] == "hs:test"
        assert lines[0]["via"] == "UserPromptExpansion"

    def test_non_skill_pretooluse_bash_no_write(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_skill_invocation", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))

        mod.main(json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "ls"},
            "session_id": "s3",
        }))

        assert '"continue"' in "".join(out_parts)
        assert _sink_lines(tmp_path, "invocations.jsonl") == []

    def test_dedup_same_session_skill_minute_yields_one_record(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_skill_invocation", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))

        # Same session_id and skill; main() computes minute bucket from now —
        # both calls happen within the same minute in CI.
        payload = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": "sample-skill"},
            "session_id": "sess-dedup",
        })
        mod.main(payload)
        # Reload hook but keep same telemetry_paths (same dedup dir).
        sys.modules.pop("track_skill_invocation", None)
        mod2 = importlib.import_module("track_skill_invocation")
        importlib.reload(mod2)
        out_parts2 = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts2.append(s))
        mod2.main(payload)

        lines = _sink_lines(tmp_path, "invocations.jsonl")
        assert len(lines) == 1, "same session|skill|minute must collapse to 1 record"

    def test_jsonl_non_forgery_embedded_newline_in_skill_name(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_skill_invocation", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)

        evil = 'evil\n{"injected":true}'
        mod.main(json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "Skill",
            "tool_input": {"skill": evil},
            "session_id": "s-evil",
        }))

        # session s-evil shards to invocations-s-evil.jsonl (P2)
        raw = (tmp_path / "state" / "telemetry" / "invocations-s-evil.jsonl").read_text()
        physical = [l for l in raw.splitlines() if l.strip()]
        assert len(physical) == 1, "embedded newline must not produce extra JSONL lines"
        assert json.loads(physical[0])["skill"] == evil


# ---------------------------------------------------------------------------
# track_script_execution
# ---------------------------------------------------------------------------

class TestTrackScriptExecution:
    def test_harness_script_bash_writes_hook_telemetry_line_exit_0(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)

        mod.main(json.dumps({
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": "python3 harness/scripts/verify_install.py --root ."},
            "tool_response": {"stdout": "ok", "stderr": ""},
        }))

        lines = _sink_lines(tmp_path, "hook-telemetry.jsonl")
        assert len(lines) == 1
        assert lines[0]["script"] == "scripts/verify_install.py"
        assert lines[0]["exit"] == 0
        assert lines[0]["source"] == "hook:bash"

    def test_script_record_carries_session_join_key(self, tmp_path, monkeypatch):
        # The record must carry `session` so the workflow/health lenses can join it
        # to the other sinks (which all record it).
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)
        mod.main(json.dumps({
            "hook_event_name": "PostToolUse",
            "tool_name": "Bash",
            "session_id": "sess-join-1",
            "tool_input": {"command": "python3 harness/scripts/verify_install.py --root ."},
            "tool_response": {"stdout": "ok", "stderr": ""},
        }))
        rec = _sink_lines(tmp_path, "hook-telemetry.jsonl")[0]
        assert rec["session"] == "sess-join-1"

    def test_error_in_tool_response_infers_exit_1(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)

        mod.main(json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "python3 harness/scripts/preflight_deps.py"},
            "tool_response": {"is_error": True, "stderr": "Traceback (most recent call last):"},
        }))

        lines = _sink_lines(tmp_path, "hook-telemetry.jsonl")
        assert len(lines) == 1
        assert lines[0]["exit"] == 1

    def test_non_script_bash_git_no_write(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)

        mod.main(json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "git status"},
            "tool_response": {"stdout": ""},
        }))

        assert _sink_lines(tmp_path, "hook-telemetry.jsonl") == []

    def test_script_path_filter_ignores_paths_outside_harness(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)

        # A command containing .py but NOT under harness/scripts|e2e.
        mod.main(json.dumps({
            "tool_name": "Bash",
            "tool_input": {"command": "python3 /tmp/random_script.py"},
            "tool_response": {"stdout": ""},
        }))

        assert _sink_lines(tmp_path, "hook-telemetry.jsonl") == []

    def test_reference_only_command_is_not_counted_as_run(self, tmp_path, monkeypatch):
        # grep/ls/cat that merely REFERENCE a harness-script path must not be
        # recorded as an execution — otherwise a read-back over check_*.py inflates
        # the run signal with greps.
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)
        for cmd in (
            "grep -n foo harness/scripts/check_fence.py",
            "ls harness/scripts/verify_install.py",
            "cat harness/e2e/run_vertical_slice.py",
        ):
            mod.main(json.dumps({"tool_name": "Bash", "tool_input": {"command": cmd},
                                 "tool_response": {"stdout": "x"}}))
        assert _sink_lines(tmp_path, "hook-telemetry.jsonl") == []

    def test_paren_prefixed_path_in_code_is_not_counted_as_run(self, tmp_path, monkeypatch):
        # A harness path sitting right after a '(' that opens a regex/code group —
        # NOT a subshell command position — must not be recorded as a run. The '('
        # of a capture group inside `python3 -c '... re.compile(r"(harness/...")'`
        # looks like a command boundary to a naive matcher; it is not.
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)
        mod.main(json.dumps({"tool_name": "Bash", "tool_input": {"command":
            'python3 -c \'import re; re.compile(r"(harness/scripts/foo.py)")\''},
            "tool_response": {"stdout": ""}}))
        assert _sink_lines(tmp_path, "hook-telemetry.jsonl") == []

    def test_direct_and_compound_execution_are_counted(self, tmp_path, monkeypatch):
        # path at command start (direct exec) and after an interpreter in a
        # cd && python3 compound both count as real runs.
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)
        mod.main(json.dumps({"tool_name": "Bash",
            "tool_input": {"command": "harness/scripts/verify_install.py --root ."},
            "tool_response": {"stdout": "ok"}}))
        mod.main(json.dumps({"tool_name": "Bash",
            "tool_input": {"command": "cd /repo && python3 harness/e2e/run_vertical_slice.py"},
            "tool_response": {"stdout": "ok"}}))
        lines = _sink_lines(tmp_path, "hook-telemetry.jsonl")
        assert [l["script"] for l in lines] == [
            "scripts/verify_install.py", "e2e/run_vertical_slice.py"]

    def test_absolute_and_var_prefixed_execution_are_counted(self, tmp_path, monkeypatch):
        # An executed script reached via an absolute or "$CLAUDE_PROJECT_DIR"-prefixed
        # path still counts; the leading dir prefix must not break detection and
        # group(1) stays the harness-relative path. (A grep/ls of such a path stays
        # rejected — the prefix cannot bridge the space at an argument position.)
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda _: None)
        mod.main(json.dumps({"tool_name": "Bash",
            "tool_input": {"command": "python3 /home/u/proj/harness/scripts/spec_graph.py --root ."},
            "tool_response": {"stdout": "ok"}}))
        mod.main(json.dumps({"tool_name": "Bash",
            "tool_input": {"command": '"$CLAUDE_PROJECT_DIR"/harness/scripts/preflight_deps.py'},
            "tool_response": {"stdout": "ok"}}))
        lines = _sink_lines(tmp_path, "hook-telemetry.jsonl")
        assert [l["script"] for l in lines] == [
            "scripts/spec_graph.py", "scripts/preflight_deps.py"]

    def test_always_emits_continue_true(self, tmp_path, monkeypatch):
        mod = _reload_hook("track_script_execution", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))

        mod.main("{bad json")

        assert '"continue"' in "".join(out_parts)


# ---------------------------------------------------------------------------
# emit_session_summary
# ---------------------------------------------------------------------------

class TestEmitSessionSummary:
    def test_reads_transcript_path_writes_sessions_line(self, tmp_path, monkeypatch):
        mod = _reload_hook("emit_session_summary", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))

        transcript = tmp_path / "fake-session.jsonl"
        rows = [
            {"timestamp": "2026-06-06T10:00:00Z", "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "hs:plan"}}]}},
            {"timestamp": "2026-06-06T10:01:00Z", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "/a.md"}}]}},
            {"timestamp": "2026-06-06T10:05:00Z", "message": {"content": [{"type": "tool_use", "name": "Task",  "input": {}}]}},
        ]
        transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n")

        mod.main(json.dumps({"session_id": "sess-x", "transcript_path": str(transcript)}))

        assert '"continue"' in "".join(out_parts)
        lines = _sink_lines(tmp_path, "sessions.jsonl")
        assert len(lines) == 1
        rec = lines[0]
        assert rec["skills"] == ["hs:plan"]
        assert rec["files_modified"] == 1
        assert rec["subagents"] == 1
        assert rec["duration_s"] == 300
        assert rec["session"] == "sess-x"

    def test_non_object_transcript_line_does_not_drop_the_summary(self, tmp_path, monkeypatch):
        # an untrusted transcript with a parseable-but-non-object line must be
        # SKIPPED, not crash the summary (which would drop the whole session record).
        mod = _reload_hook("emit_session_summary", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))
        transcript = tmp_path / "fake-session.jsonl"
        transcript.write_text(
            '[1,2,3]\n'
            '{"timestamp":"2026-06-06T10:00:00Z","message":{"content":'
            '[{"type":"tool_use","name":"Skill","input":{"skill":"hs:plan"}}]}}\n'
            '"junk-string"\n', encoding="utf-8")
        mod.main(json.dumps({"session_id": "sess-y", "transcript_path": str(transcript)}))
        assert '"continue"' in "".join(out_parts)
        lines = _sink_lines(tmp_path, "sessions.jsonl")
        assert len(lines) == 1                      # summary emitted, not dropped
        assert lines[0]["skills"] == ["hs:plan"]     # the valid row still counted

    def test_list_valued_message_field_does_not_crash_the_summary(self, tmp_path, monkeypatch):
        # a record whose `message` is a LIST (not a dict) must be skipped, not
        # crash with "'list' object has no attribute 'get'" — the exact signature
        # the hook-crash log surfaced. Mirrors the isinstance(msg, dict) guard the
        # sibling transcript hook (track_subagent_outcome) already applies.
        mod = _reload_hook("emit_session_summary", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))
        transcript = tmp_path / "fake-session.jsonl"
        transcript.write_text(
            '{"timestamp":"2026-06-06T10:00:00Z","message":[1,2,3]}\n'
            '{"timestamp":"2026-06-06T10:01:00Z","message":{"content":'
            '[{"type":"tool_use","name":"Skill","input":{"skill":"hs:plan"}}]}}\n',
            encoding="utf-8")
        mod.main(json.dumps({"session_id": "sess-z", "transcript_path": str(transcript)}))
        assert '"continue"' in "".join(out_parts)
        lines = _sink_lines(tmp_path, "sessions.jsonl")
        assert len(lines) == 1                      # summary emitted, not dropped
        assert lines[0]["skills"] == ["hs:plan"]     # the valid row still counted

    def test_record_carries_harness_version_and_kit_digest(self, tmp_path, monkeypatch):
        # The session summary stamps the harness identity once per session so any
        # state record (joined by `session`) is traceable to the harness version
        # that produced it. Read fresh from release.json under HARNESS_ROOT.
        monkeypatch.setenv("HARNESS_ROOT", str(tmp_path))
        (tmp_path / "harness").mkdir(parents=True, exist_ok=True)
        (tmp_path / "harness" / "release.json").write_text(
            json.dumps({"schema_version": "1.0", "harness_version": "9.9.9",
                        "channel": "stable", "kit_digest": "testdigest"}),
            encoding="utf-8")
        mod = _reload_hook("emit_session_summary", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda s: None)
        transcript = tmp_path / "t.jsonl"
        transcript.write_text(json.dumps(
            {"timestamp": "2026-06-06T10:00:00Z", "message": {"content": [
                {"type": "tool_use", "name": "Write",
                 "input": {"file_path": "/a.md"}}]}}) + "\n")
        mod.main(json.dumps({"session_id": "sv", "transcript_path": str(transcript)}))
        rec = _sink_lines(tmp_path, "sessions.jsonl")[0]
        assert rec["harness_version"] == "9.9.9"
        assert rec["kit_digest"] == "testdigest"

    def test_duration_nonzero_when_first_record_has_no_timestamp(self, tmp_path, monkeypatch):
        # A leading meta/summary record with no timestamp must not zero out duration:
        # scan forward to the first record that has one.
        mod = _reload_hook("emit_session_summary", tmp_path, monkeypatch)
        monkeypatch.setattr(sys.stdout, "write", lambda s: None)
        transcript = tmp_path / "no-leading-ts.jsonl"
        rows = [
            {"type": "summary", "summary": "meta record, no timestamp"},
            {"timestamp": "2026-06-06T10:00:00Z", "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "hs:plan"}}]}},
            {"timestamp": "2026-06-06T10:05:00Z", "message": {"content": [{"type": "tool_use", "name": "Write", "input": {"file_path": "/a.md"}}]}},
        ]
        transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        mod.main(json.dumps({"session_id": "s", "transcript_path": str(transcript)}))
        rec = _sink_lines(tmp_path, "sessions.jsonl")[0]
        assert rec["duration_s"] == 300  # 10:00 → 10:05, not 0
        assert rec["skills"] == ["hs:plan"]

    def test_scan_head_returns_first_ts_and_early_skills(self, tmp_path, monkeypatch):
        mod = _reload_hook("emit_session_summary", tmp_path, monkeypatch)
        transcript = tmp_path / "head.jsonl"
        rows = [
            {"type": "summary", "summary": "no timestamp here"},
            {"timestamp": "2026-06-06T09:00:00Z", "message": {"content": [{"type": "tool_use", "name": "Skill", "input": {"skill": "hs:plan"}}]}},
        ]
        transcript.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        ts, skills = mod.scan_head(str(transcript))
        assert ts == "2026-06-06T09:00:00Z"
        assert skills == ["hs:plan"]

    def test_no_transcript_still_emits_continue_true(self, tmp_path, monkeypatch):
        mod = _reload_hook("emit_session_summary", tmp_path, monkeypatch)
        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))

        mod.main(json.dumps({"session_id": "ghost", "transcript_path": "/nonexistent/path.jsonl"}))

        assert '"continue"' in "".join(out_parts)
        # No sessions line written because transcript missing — that's fine.


# ---------------------------------------------------------------------------
# fail-open: disabled telemetry still returns continue:true
# ---------------------------------------------------------------------------

class TestFailOpen:
    def test_telemetry_disabled_hook_still_continues_no_writes(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
        monkeypatch.setenv("HARNESS_TELEMETRY_DISABLED", "1")
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        for mod_name in ("track_skill_invocation",):  # telemetry_paths reloaded in place
            sys.modules.pop(mod_name, None)
        import telemetry_paths, track_skill_invocation
        importlib.reload(telemetry_paths)
        importlib.reload(track_skill_invocation)

        out_parts = []
        monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))

        track_skill_invocation.main(json.dumps({
            "tool_name": "Skill",
            "tool_input": {"skill": "x"},
            "session_id": "s",
        }))

        assert '"continue"' in "".join(out_parts)
        assert _sink_lines(tmp_path, "invocations.jsonl") == []

    def test_malformed_stdin_does_not_raise(self, tmp_path, monkeypatch):
        for hook in ("track_skill_invocation", "track_script_execution", "emit_session_summary"):
            mod = _reload_hook(hook, tmp_path, monkeypatch)
            out_parts = []
            monkeypatch.setattr(sys.stdout, "write", lambda s: out_parts.append(s))
            mod.main("not json at all }{")
            assert '"continue"' in "".join(out_parts), "%s must emit continue:true on bad stdin" % hook
