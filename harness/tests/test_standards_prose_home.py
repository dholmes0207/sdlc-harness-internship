"""The two prose standards docs have ONE home: docs/. The harness/standards/ symlink
aliases were dropped (KISS — consumers already read docs/ directly), and the planned
`standards_prose_dir` knob was never filed and is not coming.

These guard the README so it cannot drift back to describing the removed alias layer.
"""
from pathlib import Path

_README = (Path(__file__).resolve().parent.parent / "standards" / "README.md")


def test_readme_drops_alias_and_knob():
    text = _README.read_text(encoding="utf-8").lower()
    assert "symlink alias" not in text, "alias layer removed — README must not describe it"
    assert "standards_prose_dir" not in text, "the knob was never filed — drop the promise"


def test_readme_states_docs_single_home():
    text = _README.read_text(encoding="utf-8")
    assert "docs/system-architecture.md" in text and "docs/code-standards.md" in text
    # prose single-sourced in docs/ (the phrase, not the removed alias plumbing)
    assert "single-sourced in `docs/`" in text or "live in `docs/`" in text


def test_readme_points_missing_standards_at_hs_docs():
    # if the two files do not exist yet, the README must tell the reader to run hs:docs
    text = _README.read_text(encoding="utf-8")
    assert "/hs:docs" in text or "hs:docs" in text
