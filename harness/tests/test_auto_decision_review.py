"""test_auto_decision_review.py — flipping a decision's reviewed bit.

"Mark reviewed" must NOT edit the recorded decision line — the store is append-only. It
APPENDS a review event {type:review, target:<id|"*">, reviewed:true}; the current reviewed
state is fold_state overlaying that event on the decision. `*` is a snapshot at flip time:
it marks the decisions seen SO FAR, never a decision appended after it (no auto-mark-future).
"""
import json
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import auto_decision_log as adl  # noqa: E402

_LABELS = adl._DEFAULT_LABELS


def _append_decision(store, rec_id, label="ARCH", no_render=True):
    argv = ["--store", str(store), "--labels", str(_LABELS),
            "--skill", "hs:cook", "--mode", "auto", "--label", label,
            "--what", "w", "--why", "y", "--evidence", "f.py:1"]
    if no_render:
        argv.append("--no-render")
    # pin the id so tests can address the decision deterministically
    import auto_decision_log as m
    orig = m.uuid.uuid4
    # pin the id so tests can address the decision deterministically; the sink slices
    # hex to a short prefix, so hand it exactly the id we want back.
    m.uuid.uuid4 = lambda: type("U", (), {"hex": rec_id})()
    try:
        return m.main(argv)
    finally:
        m.uuid.uuid4 = orig


def _mark(store, target=None, all_=False, no_render=True):
    argv = ["--store", str(store)]
    if all_:
        argv.append("--mark-reviewed-all")
    else:
        argv += ["--mark-reviewed", target]
    if no_render:
        argv.append("--no-render")
    return adl.main(argv)


def _folded(store):
    return {d["id"]: d for d in adl.fold_state(adl.load_events(store))}


# --------------------------------------------------------------------------- append-only
def test_mark_reviewed_appends_review_event_not_edit(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    _append_decision(store, "aaaaaaaa")
    original_lines = store.read_text(encoding="utf-8").splitlines()
    assert len(original_lines) == 1

    rc = _mark(store, "aaaaaaaa")
    assert rc == 0
    lines = store.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    # the decision line is byte-identical — never edited in place
    assert lines[0] == original_lines[0]
    review = json.loads(lines[1])
    assert review["type"] == "review"
    assert review["target"] == "aaaaaaaa"
    assert review["reviewed"] is True
    assert review.get("actor")
    assert review.get("ts")


def test_fold_after_mark(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    _append_decision(store, "bbbbbbbb")
    _mark(store, "bbbbbbbb")
    assert _folded(store)["bbbbbbbb"]["reviewed"] is True


def test_mark_reviewed_all(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    _append_decision(store, "cccccccc")
    _append_decision(store, "dddddddd")
    _mark(store, all_=True)
    folded = _folded(store)
    assert folded["cccccccc"]["reviewed"] is True
    assert folded["dddddddd"]["reviewed"] is True


def test_mark_unknown_id(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    _append_decision(store, "eeeeeeee")
    rc = _mark(store, "ZZZZZZZZ")   # no such decision
    assert rc == 0                  # advisory: warn + exit 0, never blocks
    assert _folded(store)["eeeeeeee"]["reviewed"] is False


def test_star_does_not_mark_future_decision(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    _append_decision(store, "aa111111")
    _mark(store, all_=True)                 # snapshot: only aa111111 seen so far
    _append_decision(store, "bb222222")     # appended AFTER the star review
    folded = _folded(store)
    assert folded["aa111111"]["reviewed"] is True
    assert folded["bb222222"]["reviewed"] is False


def test_double_mark_idempotent(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    _append_decision(store, "ff333333")
    _mark(store, "ff333333")
    _mark(store, "ff333333")
    assert _folded(store)["ff333333"]["reviewed"] is True


def test_mark_triggers_render_refresh(tmp_path):
    store = tmp_path / "auto-decisions.jsonl"
    md = tmp_path / "auto-decisions.md"
    _append_decision(store, "99999999", no_render=False)   # render -> md unreviewed
    assert "no" in md.read_text(encoding="utf-8")
    _mark(store, "99999999", no_render=False)              # render refresh
    import auto_decision_render as adr
    text = md.read_text(encoding="utf-8")
    assert adr._REVIEWED_HEADING in text
    assert "99999999" in text
