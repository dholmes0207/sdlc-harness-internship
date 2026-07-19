"""Tests for the tolerant frontmatter parser.

The contract the memory-gap pass leans on: a malformed artifact yields a
structured ``error`` string and ``ok=False`` — it NEVER raises. That is what
lets the graph build stay fail-soft and surface a ``parse_error`` signal
instead of crashing the advisory pass.
"""

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from frontmatter_parser import parse_text, parse_file, extract_sections  # noqa: E402


def test_parse_text_happy_path():
    text = "---\nid: X\nstatus: draft\n---\n\n# Title\n\nBody line.\n"
    res = parse_text(text)
    assert res["ok"] is True
    assert res["error"] is None
    assert res["frontmatter"] == {"id": "X", "status": "draft"}
    assert "Body line." in res["body"]
    assert "Title" in res["sections"]


def test_parse_text_strips_leading_bom():
    """A UTF-8 BOM before the '---' sentinel must not break frontmatter detection."""
    text = "﻿---\nid: X\n---\n\n# H\n"
    res = parse_text(text)
    assert res["ok"] is True, res["error"]
    assert res["frontmatter"] == {"id": "X"}


def test_parse_text_no_frontmatter_is_soft_error():
    res = parse_text("# Just a heading\n\nNo frontmatter here.\n")
    assert res["ok"] is False
    assert "no YAML frontmatter" in res["error"]


def test_parse_text_empty_block_reported_accurately():
    res = parse_text("---\n---\n\nbody\n")
    assert res["ok"] is False
    assert "empty" in res["error"]


def test_parse_text_malformed_missing_close():
    res = parse_text("---\nid: X\nbody without close\n")
    assert res["ok"] is False
    assert res["error"]  # a message, not a crash


def test_parse_text_non_mapping_frontmatter():
    res = parse_text("---\n- just\n- a\n- list\n---\n\nbody\n")
    assert res["ok"] is False
    assert "not a YAML mapping" in res["error"]


def test_parse_text_yaml_value_error_is_caught():
    """An out-of-range date triggers PyYAML's bare ValueError; the parser must
    fold it into a parse_error, not propagate."""
    res = parse_text("---\ntarget_date: 2026-13-99\n---\n\nbody\n")
    assert res["ok"] is False
    assert "parse error" in res["error"].lower()


def test_extract_sections_disambiguates_duplicate_headings():
    body = "# Notes\nfirst\n\n# Notes\nsecond\n"
    sections = extract_sections(body)
    assert "Notes" in sections
    assert any(k.startswith("Notes (") for k in sections), sections
    assert "first" in sections["Notes"]


def test_parse_file_missing_is_soft_error(tmp_path):
    res = parse_file(tmp_path / "nope.md")
    assert res["ok"] is False
    assert "not found" in res["error"]
