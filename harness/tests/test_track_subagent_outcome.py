"""test_track_subagent_outcome.py — SubagentStop telemetry hook.

Covers: explicit-outcome passthrough, transcript-tail classification
(success / api_error / timeout / blocked / unknown), agent_type resolution
(payload field → filename fallback), fail-open on malformed input, and
pytest/HARNESS_TELEMETRY_DISABLED silence. Mirrors the reload-with-fresh-env
pattern of test_telemetry_hooks.py so writes actually happen inside the test body.

Harness re-home: env knobs are HARNESS_*, sinks under HARNESS_STATE_DIR/telemetry.
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


def _reload(tmp_path, monkeypatch, extra=None):
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    monkeypatch.setenv("HARNESS_USER", "alice")  # hermetic actor
    for k, v in (extra or {}).items():
        monkeypatch.setenv(k, v)
    # telemetry_paths is reloaded in place (not popped) so its module identity
    # survives for other test files that hold a top-level reference and reload it.
    for m in ("hook_runtime", "track_subagent_outcome"):
        sys.modules.pop(m, None)
    import telemetry_paths, track_subagent_outcome  # noqa
    importlib.reload(telemetry_paths)
    importlib.reload(track_subagent_outcome)
    monkeypatch.setattr(track_subagent_outcome.sys.stdout, "write", lambda _s: None)
    return track_subagent_outcome


def _lines(tmp_path, name="subagent-outcomes.jsonl"):
    # Union legacy sink + per-session shards (P2 shards session records).
    d = tmp_path / "state" / "telemetry"
    if not d.is_dir():
        return []
    stem, dot, ext = name.rpartition(".")
    pats = [name] if not dot else [name, "%s-*.%s" % (stem, ext)]
    out = []
    for pat in pats:
        for p in sorted(d.glob(pat)):
            if p.name.endswith(".bak"):
                continue
            out.extend(json.loads(l) for l in p.read_text().splitlines() if l.strip())
    return out


def _transcript(tmp_path, name, rows):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
    return str(p)


class TestExplicitOutcome:
    def test_explicit_timeout_recorded_with_agent_type(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        mod.main(json.dumps({"agent_type": "researcher", "outcome": "timeout", "session_id": "s1"}))
        recs = _lines(tmp_path)
        assert len(recs) == 1
        assert recs[0]["outcome"] == "timeout"
        assert recs[0]["agent_type"] == "researcher"
        assert recs[0]["session"] == "s1"

    def test_unknown_enum_value_falls_through_to_unknown(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        mod.main(json.dumps({"agent_type": "x", "outcome": "weird-value"}))
        assert _lines(tmp_path)[0]["outcome"] == "unknown"


class TestTranscriptClassification:
    def test_non_object_last_record_degrades_to_unknown(self, tmp_path, monkeypatch):
        # an untrusted transcript whose last non-empty line is valid JSON but not
        # an object ([...], "str", 42) must NOT crash the hook — it degrades to
        # the honest "unknown", never drops the outcome.
        mod = _reload(tmp_path, monkeypatch)
        p = tmp_path / "agent-x-1.jsonl"
        p.write_text('{"message":{"role":"assistant"}}\n[1,2,3]\n', encoding="utf-8")
        assert mod.classify_from_transcript(str(p)) == "unknown"  # no raise

    def test_clean_stop_is_success(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        tp = _transcript(tmp_path, "agent-coder-1.jsonl", [
            {"message": {"role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "done"}]}},
        ])
        mod.main(json.dumps({"transcript_path": tp, "session_id": "s1"}))
        assert _lines(tmp_path)[0]["outcome"] == "success"

    def test_api_error_text_is_api_error(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        tp = _transcript(tmp_path, "agent-researcher-9.jsonl", [
            {"message": {"role": "assistant",
                         "content": [{"type": "text", "text": "Overloaded: status 529"}]}},
        ])
        mod.main(json.dumps({"transcript_path": tp}))
        assert _lines(tmp_path)[0]["outcome"] == "api_error"

    def test_no_clean_stop_no_error_is_unknown_not_success(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        tp = _transcript(tmp_path, "agent-x-2.jsonl", [
            {"message": {"role": "assistant",
                         "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}},
        ])
        mod.main(json.dumps({"transcript_path": tp}))
        assert _lines(tmp_path)[0]["outcome"] == "unknown"

    def test_agent_type_from_filename_when_payload_lacks_it(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        tp = _transcript(tmp_path, "agent-code-reviewer-abc123.jsonl", [
            {"message": {"role": "assistant", "stop_reason": "end_turn", "content": []}},
        ])
        mod.main(json.dumps({"transcript_path": tp}))
        assert _lines(tmp_path)[0]["agent_type"] == "code-reviewer"

    def test_no_transcript_no_explicit_is_unknown(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        mod.main(json.dumps({"session_id": "s1"}))
        assert _lines(tmp_path)[0]["outcome"] == "unknown"


class TestSubagentTranscriptDiscovery:
    """Under a real SubagentStop, the payload carries `agent_id` while
    `transcript_path` points at the MAIN session transcript; the subagent's own
    transcript lives at <session>/subagents/agent-<agent_id>.jsonl. The hook must
    reconstruct that path from agent_id and classify it — and must NOT classify
    the main transcript as the subagent's outcome.
    """

    def _layout(self, tmp_path, session, agent_id, rows):
        # main transcript = <tmp>/<session>.jsonl (what transcript_path points at)
        main = tmp_path / ("%s.jsonl" % session)
        main.write_text(json.dumps({"sessionId": session}) + "\n")
        # subagent transcript = <tmp>/<session>/subagents/agent-<id>.jsonl
        sub_dir = tmp_path / session / "subagents"
        sub_dir.mkdir(parents=True)
        (sub_dir / ("agent-%s.jsonl" % agent_id)).write_text(
            "\n".join(json.dumps(r) for r in rows) + "\n")
        return str(main)

    def test_locates_subagent_transcript_via_agent_id(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        main_tp = self._layout(tmp_path, "sess1", "abc123def", [
            {"message": {"role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "done"}]}},
        ])
        mod.main(json.dumps({"transcript_path": main_tp, "agent_id": "abc123def",
                             "agent_type": "tester", "session_id": "sess1"}))
        rec = _lines(tmp_path)[0]
        assert rec["outcome"] == "success"      # classified from the SUBAGENT transcript
        assert rec["agent_type"] == "tester"

    def test_missing_subagent_file_is_unknown_no_guess(self, tmp_path, monkeypatch):
        # agent_id present but no such transcript on disk → honest unknown,
        # never a guess / pick-latest, never a crash.
        mod = _reload(tmp_path, monkeypatch)
        main = tmp_path / "sess2.jsonl"
        main.write_text("{}\n")
        mod.main(json.dumps({"transcript_path": str(main), "agent_id": "missing9",
                             "agent_type": "tester", "session_id": "sess2"}))
        assert _lines(tmp_path)[0]["outcome"] == "unknown"

    def test_agent_id_present_does_not_classify_main_transcript(self, tmp_path, monkeypatch):
        # When agent_id is present the hook must NOT fall back to classifying the
        # MAIN transcript (transcript_path) — that would report the parent turn as
        # the subagent's outcome. Clean-stop MAIN + missing subagent file = unknown.
        mod = _reload(tmp_path, monkeypatch)
        main = tmp_path / "sess3.jsonl"
        main.write_text(json.dumps({"message": {"role": "assistant",
                        "stop_reason": "end_turn", "content": []}}) + "\n")
        mod.main(json.dumps({"transcript_path": str(main), "agent_id": "nope",
                             "session_id": "sess3"}))
        assert _lines(tmp_path)[0]["outcome"] == "unknown"


class TestPayloadSignals:
    """The real SubagentStop payload carries `agent_transcript_path` (authoritative
    subagent transcript) and `last_assistant_message` (the subagent's final text,
    race-free). The subagent transcript's terminal record is typically NOT flushed
    when SubagentStop fires, so the hook (a) catches error endings from the message
    race-free, and (b) records the transcript path so the lens can reclassify the
    `unknown` later, once the file is flushed.
    """

    def test_last_assistant_message_error_is_race_free(self, tmp_path, monkeypatch):
        # error text in the in-payload final message → definite error WITHOUT
        # any transcript file (the file may not be flushed yet).
        mod = _reload(tmp_path, monkeypatch)
        mod.main(json.dumps({"agent_type": "researcher", "session_id": "s1",
                             "last_assistant_message": "Request failed: Overloaded (status 529)"}))
        assert _lines(tmp_path)[0]["outcome"] == "api_error"

    def test_clean_message_alone_is_unknown_not_success(self, tmp_path, monkeypatch):
        # a clean final message with no flushed transcript must NOT be fabricated
        # into success — stays honest unknown.
        mod = _reload(tmp_path, monkeypatch)
        mod.main(json.dumps({"agent_type": "Explore", "session_id": "s1",
                             "last_assistant_message": "# SDLC Harness"}))
        assert _lines(tmp_path)[0]["outcome"] == "unknown"

    def test_records_resolved_transcript_path_for_deferred_classify(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        sub = tmp_path / "agent-deadbeef01.jsonl"
        sub.write_text("{}\n")
        mod.main(json.dumps({"agent_type": "Explore", "session_id": "s1",
                             "agent_id": "deadbeef01", "agent_transcript_path": str(sub),
                             "transcript_path": str(tmp_path / "main.jsonl")}))
        assert _lines(tmp_path)[0]["transcript"] == str(sub)

    def test_agent_transcript_path_classified_when_flushed(self, tmp_path, monkeypatch):
        # when the authoritative subagent transcript IS already flushed, classify it.
        mod = _reload(tmp_path, monkeypatch)
        sub = _transcript(tmp_path, "agent-feedface02.jsonl", [
            {"message": {"role": "assistant", "stop_reason": "end_turn",
                         "content": [{"type": "text", "text": "done"}]}},
        ])
        mod.main(json.dumps({"agent_type": "Explore", "session_id": "s1",
                             "agent_id": "feedface02", "agent_transcript_path": sub,
                             "transcript_path": str(tmp_path / "main.jsonl")}))
        assert _lines(tmp_path)[0]["outcome"] == "success"


class TestFailOpen:
    def test_malformed_stdin_does_not_raise_and_continues(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch)
        out = []
        monkeypatch.setattr(mod.sys.stdout, "write", lambda s: out.append(s))
        mod.main("not json }{")
        assert '"continue"' in "".join(out)

    def test_disabled_no_write(self, tmp_path, monkeypatch):
        mod = _reload(tmp_path, monkeypatch, extra={"HARNESS_TELEMETRY_DISABLED": "1"})
        mod.main(json.dumps({"agent_type": "x", "outcome": "success"}))
        assert _lines(tmp_path) == []
