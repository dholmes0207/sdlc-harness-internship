"""test_auto_decision_render.py — the human-readable view over the ledger JSONL.

The JSONL is the source of truth (machine-written, append-only); auto-decisions.md is a
DERIVED view, re-rendered from scratch each time — the glossary.yaml->GLOSSARY.md pattern.
Full-regen of a derived view does NOT violate the no-read-modify-write rule (that rule
guards the append-only SOURCE, not a view). The write is atomic (mkstemp + os.replace) so a
concurrent render or a mid-write failure never leaves a torn view. The header
disambiguates from the human-approved DEC register (docs/decisions.md) — naming honesty.
"""
import os
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import auto_decision_log as adl  # noqa: E402
import auto_decision_render as adr  # noqa: E402

_LABELS = adl._DEFAULT_LABELS


def _append(store, **over):
    kw = dict(skill="hs:cook", mode="auto", label="ARCH", what="split boundary",
              why="cohesion", evidence="foo.py:1", in_plan=False,
              vocab=set(adl.load_labels(_LABELS).keys()), store=store)
    kw.update(over)
    return adl.emit(**kw)


# --------------------------------------------------------------------------- basic render
def test_render_empty_ledger(tmp_path):
    jsonl = tmp_path / "auto-decisions.jsonl"
    md = tmp_path / "auto-decisions.md"
    adr.render(jsonl, md)
    assert md.exists()
    text = md.read_text(encoding="utf-8")
    assert text.startswith("#")  # a header even for an empty ledger, no crash


def test_render_one_decision(tmp_path):
    jsonl = tmp_path / "auto-decisions.jsonl"
    md = tmp_path / "auto-decisions.md"
    rec = _append(jsonl, rec_id="abc12345")
    adr.render(jsonl, md)
    text = md.read_text(encoding="utf-8")
    assert "abc12345" in text
    assert "ARCH" in text
    assert "foo.py:1" in text


def test_render_reflects_reviewed_fold(tmp_path):
    jsonl = tmp_path / "auto-decisions.jsonl"
    md = tmp_path / "auto-decisions.md"
    _append(jsonl, rec_id="rev00001")
    # append a review event by hand (will own the CLI for this)
    import json
    with open(jsonl, "a", encoding="utf-8") as f:
        f.write(json.dumps({"type": "review", "target": "rev00001", "reviewed": True}) + "\n")
    adr.render(jsonl, md)
    text = md.read_text(encoding="utf-8")
    # the decision must now render under a reviewed heading, not the unreviewed must-review one
    assert "rev00001" in text
    assert adr._REVIEWED_HEADING in text


def test_must_review_unreviewed_section_first(tmp_path):
    jsonl = tmp_path / "auto-decisions.jsonl"
    md = tmp_path / "auto-decisions.md"
    _append(jsonl, rec_id="mustrev1", label="ARCH")           # must_review, unreviewed
    _append(jsonl, rec_id="traceon1", label="TRIVIAL")        # trace_only
    adr.render(jsonl, md)
    text = md.read_text(encoding="utf-8")
    must_pos = text.index(adr._MUST_REVIEW_HEADING)
    trace_pos = text.index(adr._TRACE_HEADING)
    assert must_pos < trace_pos            # must-review section comes first
    assert text.index("mustrev1") < text.index("traceon1")


def test_header_disambiguates_dec(tmp_path):
    jsonl = tmp_path / "auto-decisions.jsonl"
    md = tmp_path / "auto-decisions.md"
    adr.render(jsonl, md)
    text = md.read_text(encoding="utf-8")
    assert "decisions.md" in text  # names the DEC register it must NOT be confused with


# --------------------------------------------------------------------------- atomic
def test_render_atomic_no_fixed_tmp_leftover(tmp_path):
    jsonl = tmp_path / "auto-decisions.jsonl"
    md = tmp_path / "auto-decisions.md"
    _append(jsonl, rec_id="atom0001")
    adr.render(jsonl, md)
    # no fixed-name temp file leaks; the fixed `.tmp` sibling is exactly what F7 forbids
    leftovers = [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]
    assert leftovers == []
    assert not (tmp_path / "auto-decisions.md.tmp").exists()


def test_render_atomic_preserves_view_on_replace_failure(tmp_path, monkeypatch):
    jsonl = tmp_path / "auto-decisions.jsonl"
    md = tmp_path / "auto-decisions.md"
    _append(jsonl, rec_id="keep0001")
    adr.render(jsonl, md)
    original = md.read_text(encoding="utf-8")

    def _boom(*a, **k):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(adr.os, "replace", _boom)
    _append(jsonl, rec_id="new00002")
    with pytest.raises(OSError):
        adr.render(jsonl, md)
    # the in-place view must be byte-identical to before — never written in place
    assert md.read_text(encoding="utf-8") == original
    # and the aborted render leaves no tmp behind
    assert [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")] == []


def test_render_idempotent(tmp_path):
    jsonl = tmp_path / "auto-decisions.jsonl"
    md = tmp_path / "auto-decisions.md"
    _append(jsonl, rec_id="idem0001", ts="2026-01-01T00:00:00+00:00")
    adr.render(jsonl, md)
    first = md.read_text(encoding="utf-8")
    adr.render(jsonl, md)
    assert md.read_text(encoding="utf-8") == first


def test_md_path_for_plan_vs_reports():
    plan_jsonl = Path("/x/plans/260101-slug/artifacts/auto-decisions.jsonl")
    assert adr.md_path_for(plan_jsonl) == Path("/x/plans/260101-slug/auto-decisions.md")
    rep_jsonl = Path("/x/plans/reports/auto-decisions-sess.jsonl")
    assert adr.md_path_for(rep_jsonl) == Path("/x/plans/reports/auto-decisions-sess.md")


# --------------------------------------------------------------------------- CLI wire
def test_cli_append_triggers_render(tmp_path):
    jsonl = tmp_path / "auto-decisions.jsonl"
    rc = adl.main(["--store", str(jsonl), "--labels", str(_LABELS),
                   "--skill", "hs:cook", "--mode", "auto", "--label", "ARCH",
                   "--what", "x", "--why", "y", "--evidence", "z.py:2"])
    assert rc == 0
    md = tmp_path / "auto-decisions.md"
    assert md.exists()
    assert "ARCH" in md.read_text(encoding="utf-8")


def test_cli_no_render_flag_skips_view(tmp_path):
    jsonl = tmp_path / "auto-decisions.jsonl"
    rc = adl.main(["--store", str(jsonl), "--labels", str(_LABELS), "--no-render",
                   "--skill", "hs:cook", "--mode", "auto", "--label", "ARCH",
                   "--what", "x", "--why", "y", "--evidence", "z.py:2"])
    assert rc == 0
    assert not (tmp_path / "auto-decisions.md").exists()
