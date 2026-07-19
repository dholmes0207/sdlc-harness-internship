"""test_humanize_leading_dash.py — line-leading dashes survive the humanizer.

humanize_dashes collapses a SPACED em/en dash plus its surrounding whitespace
into a separator (`A — B` -> `A, B`). That rule must NOT fire on a dash that
sits at the START of a line: a leading em-dash (or one preceded only by
indentation) is a list bullet / leading separator, not a mid-sentence aside, and
collapsing it eats the line's opening character and corrupts the markdown.

These tests pin the boundary: a line-leading spaced dash is left verbatim, an
ASCII `- ` list item is left verbatim, while a genuine mid-line ` — ` still
converts.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import humanize_dashes as hd  # noqa: E402


def test_line_leading_em_dash_is_left_intact():
    # A line that opens with an em-dash is a leading separator/bullet, not an
    # aside; collapsing it would delete the line's first character.
    s = "— aside text"
    out, changes = hd.humanize_text(s)
    assert out == s
    assert changes == []


def test_indented_leading_em_dash_is_left_intact():
    # Only whitespace precedes the dash, so it is still line-leading.
    s = " — aside"
    out, changes = hd.humanize_text(s)
    assert out == s
    assert changes == []


def test_leading_en_dash_is_left_intact():
    s = "– note"
    out, changes = hd.humanize_text(s)
    assert out == s
    assert changes == []


def test_ascii_hyphen_list_item_is_left_intact():
    # `- ` is a plain markdown bullet (ASCII hyphen, already untouched), and a
    # mid-line em-dash on the same line still converts as prose.
    s = "- a plain bullet"
    out, changes = hd.humanize_text(s)
    assert out == s
    assert changes == []


def test_mid_line_spaced_dash_still_converts():
    out, changes = hd.humanize_text("A — B")
    assert out == "A, B"
    assert changes == [1]


def test_leading_dash_line_with_mid_line_dash_converts_only_mid():
    # The opening em-dash survives; the later in-line em-dash still collapses.
    out, _ = hd.humanize_text("— intro then A — B")
    assert out == "— intro then A, B"


def test_per_line_leading_rule_across_multiple_lines():
    text = "— first line\nA — B\n— third line"
    out, changes = hd.humanize_text(text)
    assert out == "— first line\nA, B\n— third line"
    assert changes == [2]
