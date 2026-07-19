"""test_transcript_questions.py — last_unresolved_question(transcript_path).

Extractor reads a Claude Code session transcript (jsonl), reverse-scans the tail
for the most-recent AskUserQuestion (AUQ) tool_use, finds its answer, and returns
a dict ONLY when that question is still open (unanswered, or answered with free
text that doesn't match any offered option label). A clean option pick, no AUQ in
the tail, or any error -> None (fail-safe silent).

Transcript shape pinned from REAL transcripts on CC v2.1.195 (session
1ae597cc-...; the AUQ answer is itself the documented redirect signature: user
picked "option 1" by typing free text instead of clicking a label):
  - AUQ tool_use lives in an assistant record:
      {"type":"assistant","message":{"role":"assistant","content":[
         {"type":"tool_use","name":"AskUserQuestion","id":"toolu_..",
          "input":{"questions":[{"question":..,"header":..,"multiSelect":bool,
                                 "options":[{"label":..,"description":..}]}]}}]}}
  - the answer lives in a user record carrying BOTH a tool_result block (same
    tool_use_id) AND a top-level toolUseResult.answers dict keyed by question text:
      {"type":"user","message":{"role":"user","content":[
         {"type":"tool_result","tool_use_id":"toolu_..","content":"Your questions
          have been answered: ..."}]},
       "toolUseResult":{"questions":[..],"answers":{"<question text>":"<answer>"}}}
This format is CC-internal and version-fragile; the extractor fails open on any
shape drift, so a future CC release degrades to today's behavior (silent), never
to a crash.
"""
import json
import sys
from pathlib import Path


_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import transcript_questions as tq  # noqa: E402


# --------------------------------------------------------------- fixture builders ---

def _auq_record(tool_use_id, question, labels, multi=False):
    """An assistant record carrying one AskUserQuestion tool_use (real CC shape)."""
    return {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [
                {"type": "text", "text": "let me ask"},
                {
                    "type": "tool_use",
                    "name": "AskUserQuestion",
                    "id": tool_use_id,
                    "input": {
                        "questions": [{
                            "question": question,
                            "header": "H",
                            "multiSelect": multi,
                            "options": [
                                {"label": lbl, "description": "d-%s" % lbl}
                                for lbl in labels
                            ],
                        }],
                    },
                },
            ],
        },
    }


def _answer_record(tool_use_id, question, answer):
    """A user record answering an AUQ — both tool_result and toolUseResult.answers."""
    return {
        "type": "user",
        "message": {
            "role": "user",
            "content": [{
                "type": "tool_result",
                "tool_use_id": tool_use_id,
                "content": 'Your questions have been answered: "%s"="%s". You can '
                           "now continue with these answers in mind." % (question, answer),
            }],
        },
        "toolUseResult": {"answers": {question: answer}},
    }


def _noise_record(text="nudge ctx"):
    """A post-answer hook/assistant record — what really sits between an AUQ answer
    and the compaction event, forcing a reverse scan (Mechanism #5)."""
    return {"type": "assistant",
            "message": {"role": "assistant", "content": [{"type": "text", "text": text}]}}


def _write(tmp_path, records, name="transcript.jsonl"):
    p = tmp_path / name
    p.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------- 1. unanswered ---

def test_last_auq_unanswered_returns_unanswered(tmp_path):
    path = _write(tmp_path, [
        _noise_record("earlier turn"),
        _auq_record("toolu_1", "Pick a branch?", ["A", "B"]),
    ])
    res = tq.last_unresolved_question(path)
    assert res is not None
    assert res["reason"] == "unanswered"
    assert res["answer"] is None
    assert res["question"] == "Pick a branch?"
    assert res["options"] == ["A", "B"]


# ----------------------------------------------------------------- 2. clean pick -> None ---

def test_last_auq_clean_option_returns_none(tmp_path):
    path = _write(tmp_path, [
        _auq_record("toolu_1", "Pick a branch?", ["A — go", "B — stop"]),
        _answer_record("toolu_1", "Pick a branch?", "A — go"),
    ])
    assert tq.last_unresolved_question(path) is None


# --------------------------------------------------------------- 3. free-text redirect ---

def test_last_auq_free_text_returns_free_text(tmp_path):
    typed = "actually let's do option 1 but also preserve voice"
    path = _write(tmp_path, [
        _auq_record("toolu_1", "Pick a branch?", ["A — go", "B — stop"]),
        _answer_record("toolu_1", "Pick a branch?", typed),
    ])
    res = tq.last_unresolved_question(path)
    assert res is not None
    assert res["reason"] == "free_text"
    assert res["answer"] == typed
    assert res["question"] == "Pick a branch?"


# ------------------------------------------------- 4. reverse scan past trailing noise ---

def test_reverse_scan_finds_auq_not_last_line(tmp_path):
    # AUQ unanswered, then several non-AUQ records AFTER it (hooks/assistant fire
    # before compaction). Reading the last line would miss the AUQ entirely.
    path = _write(tmp_path, [
        _auq_record("toolu_9", "Still open?", ["yes", "no"]),
        _noise_record("hook a"),
        _noise_record("hook b"),
        _noise_record("assistant turn"),
    ])
    res = tq.last_unresolved_question(path)
    assert res is not None
    assert res["question"] == "Still open?"
    assert res["reason"] == "unanswered"


# --------------------------------------------------- 5. only the LAST AUQ is considered ---

def test_only_last_auq_considered_clean_skips(tmp_path):
    # An earlier AUQ is unanswered, but the LATER AUQ was cleanly answered ->
    # None (we trust prior questions; only the most-recent one matters).
    path = _write(tmp_path, [
        _auq_record("toolu_old", "Old question?", ["X", "Y"]),  # never answered
        _auq_record("toolu_new", "New question?", ["P", "Q"]),
        _answer_record("toolu_new", "New question?", "P"),
    ])
    assert tq.last_unresolved_question(path) is None


# ----------------------------------------------------------------- 6. no AUQ in tail ---

def test_no_auq_returns_none(tmp_path):
    path = _write(tmp_path, [_noise_record("a"), _noise_record("b")])
    assert tq.last_unresolved_question(path) is None


# ------------------------------------------------------------- 7. corrupt line tolerated ---

def test_corrupt_jsonl_line_does_not_raise(tmp_path):
    p = tmp_path / "t.jsonl"
    good = _auq_record("toolu_1", "Open?", ["A", "B"])
    p.write_text(
        json.dumps(_noise_record("x")) + "\n"
        + "{ this is not valid json ]]]\n"
        + json.dumps(good) + "\n",
        encoding="utf-8",
    )
    res = tq.last_unresolved_question(str(p))
    assert res is not None and res["reason"] == "unanswered"


# ----------------------------------------------------- 8. missing / None path -> None ---

def test_missing_path_returns_none():
    assert tq.last_unresolved_question(None) is None
    assert tq.last_unresolved_question("/no/such/file/at/all.jsonl") is None


# ---------------------------------------------------------------- 9. _classify (pure) ---

def _q(labels):
    return {"questions": [{"question": "Q?", "header": "H", "multiSelect": False,
                           "options": [{"label": l, "description": "d"} for l in labels]}]}


def test_classify_multiselect_clean_returns_none():
    # multiSelect answer is a list; every element matching a label is a clean pick.
    q = {"questions": [{"question": "Q?", "header": "H", "multiSelect": True,
                        "options": [{"label": l, "description": "d"} for l in ["A", "B", "C"]]}]}
    assert tq._classify(q, ["A", "C"]) is None


def test_classify_multiselect_with_extra_is_free_text():
    q = {"questions": [{"question": "Q?", "header": "H", "multiSelect": True,
                        "options": [{"label": l, "description": "d"} for l in ["A", "B"]]}]}
    res = tq._classify(q, ["A", "typed extra"])
    assert res is not None and res["reason"] == "free_text"
