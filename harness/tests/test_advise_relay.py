"""hs:advise relay mechanism — the state-file + NEEDS_USER_INPUT marker seam.

A Claude Code subagent cannot call AskUserQuestion, so the advisor agent persists its working
state, emits a NEEDS_USER_INPUT marker with ONE question in the AskUserQuestion schema, and
ends its turn; the main orchestrator asks the user and re-spawns the advisor with the answer.
These tests pin the machine-checkable half: atomic state round-trip, marker emit/parse, the
one-question schema, and that a fresh advisor can resume from the state.
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills" / "advise" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import advise_relay  # noqa: E402


def _state():
    return {
        "phase": "interview",
        "input": "Should I build my own job queue or use an off-the-shelf one?",
        "flags": "--agent",
        "scout_findings": ["adapter layer already wraps SQS"],
        "qa_log": [{"q": "what breaks if never done?", "a": "manual retries pile up"}],
        "reframing_draft": {"problem": "reliable async work", "requirements": [], "goals": []},
        "next": "ask about throughput + ops budget",
    }


def test_state_write_read_round_trip(tmp_path):
    p = tmp_path / "advise-state.json"
    advise_relay.write_state(str(p), _state())
    assert advise_relay.read_state(str(p)) == _state()


def test_write_is_atomic_no_leftover_tmp(tmp_path):
    p = tmp_path / "advise-state.json"
    advise_relay.write_state(str(p), _state())
    assert p.is_file()
    assert not (tmp_path / "advise-state.json.tmp").exists()


def test_read_missing_returns_empty(tmp_path):
    assert advise_relay.read_state(str(tmp_path / "nope.json")) == {}


def test_emit_needs_input_has_marker_and_valid_question():
    q = {
        "question": "How much throughput must the queue sustain?",
        "header": "Throughput",
        "multiSelect": False,
        "options": [
            {"label": "< 100/s (Recommended)", "description": "off-the-shelf easily covers it"},
            {"label": "100-10k/s", "description": "off-the-shelf still fine, tune batching"},
            {"label": "> 10k/s", "description": "custom may pay off — measure first"},
        ],
    }
    out = advise_relay.emit_needs_input(q)
    assert out.splitlines()[0].strip() == "NEEDS_USER_INPUT"
    assert "```json" in out
    parsed = advise_relay.parse_needs_input(out)
    assert parsed["question"] == q["question"]
    assert 2 <= len(parsed["options"]) <= 4


def test_parse_anchors_on_marker_not_first_json_block():
    # An advisor reply that shows an auxiliary ```json block BEFORE the marker must
    # still parse the marker's question block, not the earlier one.
    q = {
        "question": "Which scope?",
        "header": "Scope",
        "multiSelect": False,
        "options": [
            {"label": "A", "description": "a"},
            {"label": "B", "description": "b"},
        ],
    }
    reply = 'context:\n```json\n{"evidence": "prior finding"}\n```\n\n' + advise_relay.emit_needs_input(q)
    parsed = advise_relay.parse_needs_input(reply)
    assert parsed.get("question") == "Which scope?"
    assert parsed.get("header") == "Scope"


def test_validate_question_rejects_bad_shapes():
    ok, _ = advise_relay.validate_question({
        "question": "q", "header": "H", "multiSelect": False,
        "options": [{"label": "a", "description": "x"}, {"label": "b", "description": "y"}]})
    assert ok
    # too few options
    bad, _ = advise_relay.validate_question({
        "question": "q", "header": "H", "multiSelect": False,
        "options": [{"label": "a", "description": "x"}]})
    assert not bad
    # header too long (> 12 chars)
    bad2, _ = advise_relay.validate_question({
        "question": "q", "header": "WAY-TOO-LONG-HEADER", "multiSelect": False,
        "options": [{"label": "a", "description": "x"}, {"label": "b", "description": "y"}]})
    assert not bad2


def test_emit_rejects_more_than_four_options():
    q = {"question": "q", "header": "H", "multiSelect": False,
         "options": [{"label": str(i), "description": "d"} for i in range(5)]}
    try:
        advise_relay.emit_needs_input(q)
        raised = False
    except ValueError:
        raised = True
    assert raised, "emit must reject an out-of-schema question (5 options)"


def test_respawn_continuation_resumes_from_state(tmp_path):
    p = tmp_path / "advise-state.json"
    st = _state()
    advise_relay.write_state(str(p), st)
    # a fresh advisor records the answer and advances the phase
    resumed = advise_relay.read_state(str(p))
    resumed["qa_log"].append({"q": "throughput?", "a": "< 100/s"})
    resumed["phase"] = "confirm"
    resumed["next"] = "confirm reframing"
    advise_relay.write_state(str(p), resumed)
    final = advise_relay.read_state(str(p))
    assert final["phase"] == "confirm"
    assert len(final["qa_log"]) == 2
    assert final["next"] == "confirm reframing"
