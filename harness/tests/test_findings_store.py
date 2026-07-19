"""test_findings_store.py — durable belief store (append-only + replay-on-read).

findings_store is hs:compound's belief memory. It departs from the source instinct_store:
instead of rewriting the file to update a confidence, it NEVER rewrites — the file only
grows, and confidence/decay/evidence are recomputed at READ time by replaying the records
chronologically.

The load-bearing properties tested here:
  - append-only: every emit only appends; a prior byte-prefix is never disturbed.
  - interleaved decay: a reinforce does not erase decay already accrued.
  - confidence ceiling: no belief reaches 1.0 before a human formalizes it.
  - full-set dedup: a recurring idea reinforces (and revives a faded belief)
    instead of creating a duplicate — even when the belief is archived from the view.
  - archive is a read-time filter only: the record stays in the file and stays a dedup
    candidate.
"""
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import findings_store as fs  # noqa: E402

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _day(n):
    return _BASE + timedelta(days=n)


def _store(tmp_path):
    return tmp_path / "findings.jsonl"


# --- append-only --------------------------------------------------------------

def test_append_only_never_rewrites(tmp_path):
    p = _store(tmp_path)
    fs.emit_observation_record("first idea about routing", "skills", path=p, ts=_day(0).isoformat())
    after_one = p.read_bytes()
    fs.emit_observation_record("second idea about telemetry", "telemetry", path=p, ts=_day(1).isoformat())
    fs.emit_observation_record("third idea about gates", "gates", path=p, ts=_day(2).isoformat())

    lines = p.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 3
    # the first record's bytes are an unchanged PREFIX of the grown file (pure append)
    assert p.read_bytes().startswith(after_one)


# --- interleaved decay --------------------------------------------------------

def test_interleaved_decay_not_erased_by_reinforce(tmp_path):
    p = _store(tmp_path)
    obs = fs.emit_observation_record("decay test belief", "skills",
                                     source="telemetry", path=p, ts=_day(0).isoformat())
    assert obs["conf_seed"] == 0.7
    fs.emit_reinforce(obs["id"], delta_conf=0.15, path=p, ts=_day(200).isoformat())

    belief = _find(fs.read_beliefs(now=_day(200), path=p, include_archived=True), obs["id"])
    # decay applies to the seed BEFORE the reinforce boost: ~0.15, NOT ~0.745
    # (which is what "reinforce erases decay" would yield).
    decayed_seed = 0.7 * math.exp(-fs.DECAY_LAMBDA * 200)
    expected = decayed_seed + (1 - decayed_seed) * 0.15
    assert abs(belief["conf"] - expected) < 1e-3
    assert belief["conf"] < 0.2  # clearly not the decay-erased 0.745

    # second case: seed 0.5 + two same-day reinforces matches the interleaved formula
    p2 = _store(tmp_path / "two")
    (tmp_path / "two").mkdir()
    o2 = fs.emit_observation_record("same day belief", "skills",
                                    source="backlog", path=p2, ts=_day(0).isoformat())
    assert o2["conf_seed"] == 0.5
    fs.emit_reinforce(o2["id"], delta_conf=0.15, path=p2, ts=_day(0).isoformat())
    fs.emit_reinforce(o2["id"], delta_conf=0.15, path=p2, ts=_day(0).isoformat())
    b2 = _find(fs.read_beliefs(now=_day(0), path=p2, include_archived=True), o2["id"])
    exp2 = 0.5
    exp2 = exp2 + (1 - exp2) * 0.15
    exp2 = exp2 + (1 - exp2) * 0.15
    assert abs(b2["conf"] - exp2) < 1e-3
    assert b2["evidence"] == 3


def test_conf_ceiling(tmp_path):
    p = _store(tmp_path)
    obs = fs.emit_observation_record("ceiling belief", "skills",
                                     source="backlog", path=p, ts=_day(0).isoformat())
    for _ in range(10):
        fs.emit_reinforce(obs["id"], delta_conf=0.15, path=p, ts=_day(0).isoformat())
    belief = _find(fs.read_beliefs(now=_day(0), path=p, include_archived=True), obs["id"])
    assert belief["conf"] <= fs.CONF_CEIL
    assert belief["conf"] < 1.0

    # a cap-binding case: enough strong reinforces that the uncapped value would
    # exceed the ceiling => the ceiling must clamp it.
    p2 = _store(tmp_path / "cap")
    (tmp_path / "cap").mkdir()
    o2 = fs.emit_observation_record("hard ceiling belief", "skills",
                                    source="backlog", path=p2, ts=_day(0).isoformat())
    for _ in range(30):
        fs.emit_reinforce(o2["id"], delta_conf=0.3, path=p2, ts=_day(0).isoformat())
    b2 = _find(fs.read_beliefs(now=_day(0), path=p2, include_archived=True), o2["id"])
    assert b2["conf"] <= fs.CONF_CEIL
    assert b2["conf"] >= fs.CONF_CEIL - 0.01  # the clamp engaged (sits at the ceiling)


# --- full-set dedup -----------------------------------------------------------

def test_jaccard_reinforce_beats_recreate(tmp_path):
    p = _store(tmp_path)
    fs.emit_observation_record("the router over triggers on vague prompts", "skills",
                               path=p, ts=_day(0).isoformat())
    # a near-duplicate phrasing (Jaccard >= 0.85 on canon) finds the existing belief
    hit = fs.find_similar("the router over triggers on the vague prompts", path=p)
    assert hit is not None

    # the record helper chooses reinforce over a new observation
    rec = fs.record_observation_or_reinforce(
        "the router over triggers on vague prompts again", "skills",
        path=p, ts=_day(1).isoformat())
    assert rec["type"] == "reinforce"
    # still exactly one distinct belief
    assert len(fs.read_beliefs(now=_day(1), path=p, include_archived=True)) == 1


def test_dedup_scans_full_set_not_filtered(tmp_path):
    p = _store(tmp_path)
    # seed a belief that will be archived (low conf + old) by read time
    obs = fs.emit_observation_record("faded belief about stale gates", "gates",
                                     source="critic", path=p, ts=_day(0).isoformat())
    now = _day(400)
    # archived from the view (conf decays below 0.4 over 400 days, age >= 30d)
    assert _find(fs.read_beliefs(now=now, path=p), obs["id"]) is None
    distinct_before = len(fs.read_beliefs(now=now, path=p, include_archived=True))

    # re-emitting the same idea must REINFORCE (revive) the archived belief, not
    # create a duplicate — dedup scans the full seeded set, archived included.
    rec = fs.record_observation_or_reinforce(
        "faded belief about stale gates", "gates", path=p, ts=now.isoformat())
    assert rec["type"] == "reinforce"
    distinct_after = len(fs.read_beliefs(now=now, path=p, include_archived=True))
    assert distinct_after == distinct_before  # no new distinct belief


def test_archive_stale_filtered_on_read(tmp_path):
    p = _store(tmp_path)
    obs = fs.emit_observation_record("stale low-confidence belief", "misc",
                                     source="critic", path=p, ts=_day(0).isoformat())
    now = _day(400)
    # hidden from the default view...
    assert _find(fs.read_beliefs(now=now, path=p), obs["id"]) is None
    # ...but the record is still in the file (nothing deleted)
    assert len(p.read_text(encoding="utf-8").splitlines()) == 1
    # ...and still a dedup candidate (include_archived view sees it)
    assert _find(fs.read_beliefs(now=now, path=p, include_archived=True), obs["id"]) is not None


# --- promotion ----------------------------------------------------------------

def test_promotion_candidates_threshold(tmp_path):
    p = _store(tmp_path)
    # a belief that clears conf>=0.80 AND evidence>=3
    strong = fs.emit_observation_record("strong promotable belief", "skills",
                                        source="telemetry", path=p, ts=_day(0).isoformat())
    for _ in range(4):
        fs.emit_reinforce(strong["id"], delta_conf=0.4, path=p, ts=_day(0).isoformat())
    # a belief with high conf but too little evidence (only 1 evidence)
    fs.emit_observation_record("lonely high-conf belief", "skills",
                               source="telemetry", path=p, ts=_day(0).isoformat())

    cands = fs.promotion_candidates(now=_day(0), path=p)
    ids = {c["id"] for c in cands}
    assert strong["id"] in ids
    # the lonely one (evidence == 1) is below the evidence floor
    strong_belief = _find(fs.read_beliefs(now=_day(0), path=p), strong["id"])
    assert strong_belief["conf"] >= 0.80 and strong_belief["evidence"] >= 3
    assert all(c["conf"] >= 0.80 and c["evidence"] >= 3 for c in cands)


# --- fail-soft ----------------------------------------------------------------

def test_failsoft_bad_line(tmp_path):
    p = _store(tmp_path)
    fs.emit_observation_record("good belief one", "skills", path=p, ts=_day(0).isoformat())
    with open(p, "a", encoding="utf-8") as fh:
        fh.write("{ this is not valid json\n")
    fs.emit_observation_record("good belief two", "skills", path=p, ts=_day(1).isoformat())

    beliefs = fs.read_beliefs(now=_day(2), path=p, include_archived=True)
    assert len(beliefs) == 2  # the bad line is skipped, not fatal


# --- CLI glue (the compound wiring uses these) --------------------------------

def test_cli_emit_dedups_then_list(tmp_path, capsys):
    import json as _json
    p = str(_store(tmp_path))
    assert fs.main(["--emit", "--text", "router over triggers on vague prompts",
                    "--category", "skills", "--source", "telemetry", "--path", p]) == 0
    assert '"recorded": "observation"' in capsys.readouterr().out

    # an equivalent re-emit reinforces rather than duplicating
    assert fs.main(["--emit", "--text", "router over triggers on the vague prompts",
                    "--category", "skills", "--path", p]) == 0
    assert '"recorded": "reinforce"' in capsys.readouterr().out

    assert fs.main(["--list", "--path", p]) == 0
    listing = _json.loads(capsys.readouterr().out)
    assert len(listing["beliefs"]) == 1  # one belief, reinforced — not two


# --- empty-canon must not dedup-merge -----------------------------------------

def test_empty_canon_beliefs_do_not_merge(tmp_path):
    # A belief whose text canonicalizes to "" (all stopwords / pure punctuation) carries
    # no idea to dedup on. _jaccard(empty, empty) == 1.0, so without a guard two DISTINCT
    # contentless texts would collapse into one belief — violating one-belief-per-idea by
    # MERGING different ideas, not avoiding a dup.
    p = _store(tmp_path)
    assert fs.canon("the of in on") == "" and fs.canon("!!! ??? ...") == ""
    fs.record_observation_or_reinforce("the of in on", "misc", path=p)
    rec = fs.record_observation_or_reinforce("!!! ??? ...", "misc", path=p)
    assert rec["type"] == "observation"  # a fresh seed, NOT a reinforce of the first
    assert len(fs.read_beliefs(path=p, include_archived=True)) == 2


# --- replay orders by real instant, not the ts string -------------------------

def test_replay_orders_by_real_instant_not_string(tmp_path):
    # Reinforces must replay in true chronological order. A non-UTC offset can make the ts
    # STRING sort disagree with the real instant: "...+00:00" sorts before "...+07:00"
    # lexically, but here the +07:00 wall-time is the EARLIER instant. The replayed
    # confidence must follow real time, not string order.
    seed_ts = datetime(2026, 1, 1, tzinfo=timezone.utc)
    early_real = datetime(2026, 1, 9, 17, 0, tzinfo=timezone.utc)   # written below as +07:00
    late_real = datetime(2026, 1, 10, 0, 0, tzinfo=timezone.utc)
    now = datetime(2026, 2, 1, tzinfo=timezone.utc)
    delta = 0.5

    def decay(c, d):
        return c * math.exp(-fs.DECAY_LAMBDA * d)

    def reinforce(c):
        return min(fs.CONF_CEIL, c + (1.0 - c) * delta)

    def days(a, b):
        return max(0.0, (b - a).total_seconds() / 86400.0)

    c = 0.7  # telemetry seed
    c = reinforce(decay(c, days(seed_ts, early_real)))
    c = reinforce(decay(c, days(early_real, late_real)))
    expected = decay(c, days(late_real, now))

    p = _store(tmp_path)
    o = fs.emit_observation_record("real instant ordering belief", "skills",
                                   source="telemetry", path=p, ts=seed_ts.isoformat())
    fs.emit_reinforce(o["id"], delta_conf=delta, path=p, ts="2026-01-10T00:00:00+07:00")
    fs.emit_reinforce(o["id"], delta_conf=delta, path=p, ts="2026-01-10T00:00:00+00:00")
    got = _find(fs.read_beliefs(now=now, path=p, include_archived=True), o["id"])["conf"]
    assert abs(got - expected) < 1e-9


# --- CLI --source choices are unique ------------------------------------------

def test_source_choices_listed_once(capsys):
    import re
    import pytest
    with pytest.raises(SystemExit):
        fs.main(["--help"])
    out = capsys.readouterr().out
    m = re.search(r"--source \{([^}]*)\}", out)
    assert m, out
    choices = m.group(1).split(",")
    assert len(choices) == len(set(choices)), choices


# --- helper -------------------------------------------------------------------

def _find(beliefs, belief_id):
    return next((b for b in beliefs if b["id"] == belief_id), None)
