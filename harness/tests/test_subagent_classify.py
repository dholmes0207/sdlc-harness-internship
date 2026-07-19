"""test_subagent_classify.py — shared subagent outcome classifier.

Pure classification: error-taxonomy on a raw string (race-free, for the
payload's last_assistant_message) and transcript-tail classification
(clean stop → success; terminal error → taxonomy; else unknown). No fabrication.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import subagent_classify as sc  # noqa: E402


class TestClassifyText:
    def test_error_text_maps_to_label(self):
        assert sc.classify_text("Overloaded: status 529") == "api_error"
        assert sc.classify_text("operation timed out") == "timeout"
        assert sc.classify_text("permission denied for path") == "blocked"

    def test_clean_text_is_unknown_not_success(self):
        # a clean message is NOT asserted success here — that needs stop_reason.
        assert sc.classify_text("# SDLC Harness") == "unknown"

    def test_empty_is_unknown(self):
        assert sc.classify_text("") == "unknown"
        assert sc.classify_text(None) == "unknown"


class TestClassifyFromTranscript:
    def _t(self, tmp_path, rows):
        p = tmp_path / "agent-x-1.jsonl"
        p.write_text("\n".join(json.dumps(r) for r in rows) + "\n")
        return str(p)

    def test_clean_stop_is_success(self, tmp_path):
        tp = self._t(tmp_path, [{"message": {"role": "assistant",
                     "stop_reason": "end_turn", "content": [{"type": "text", "text": "ok"}]}}])
        assert sc.classify_from_transcript(tp) == "success"

    def test_pending_tool_use_is_unknown(self, tmp_path):
        tp = self._t(tmp_path, [{"message": {"role": "assistant",
                     "content": [{"type": "tool_use", "name": "Bash", "input": {}}]}}])
        assert sc.classify_from_transcript(tp) == "unknown"

    def test_missing_file_is_unknown_no_raise(self, tmp_path):
        assert sc.classify_from_transcript(str(tmp_path / "nope.jsonl")) == "unknown"
        assert sc.classify_from_transcript(None) == "unknown"

    def test_non_object_last_line_degrades(self, tmp_path):
        p = tmp_path / "agent-x-2.jsonl"
        p.write_text('{"message":{"role":"assistant"}}\n[1,2,3]\n')
        assert sc.classify_from_transcript(str(p)) == "unknown"  # no raise


class TestAgentTypeFromFilename:
    def test_keeps_alpha_tokens(self):
        assert sc.agent_type_from_filename("/x/agent-code-reviewer-abc123.jsonl") == "code-reviewer"

    def test_pure_id_is_unknown(self):
        # agent-<hexid>.jsonl (the real subagent filename) has no type segment.
        assert sc.agent_type_from_filename("/x/agent-a7acf4b675b9dbc36.jsonl") == "unknown"
