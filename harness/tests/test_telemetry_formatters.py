"""test_telemetry_formatters — display-width-correct table rendering shared by
every telemetry lens (lens_reliability/skill_usage/risk_flags/subagent_outcomes/
workflow_chains + analyze_telemetry).

The module was exercised only INDIRECTLY before, through lens tests that feed it
ASCII data — so its load-bearing logic was untested where it actually matters:
the harness emits reports in Vietnamese (output.yaml language=vi), so a table
column's width must be computed by DISPLAY width, not code-point count. A
regression of `_disp_width` to `len(s)` (CJK glyphs are 2 cells, Vietnamese NFD
diacritics are base+combining code points that render as 1 cell) would silently
misalign every generated report table and no ASCII-only lens test would catch it.

These are characterization/regression locks: the shipped code is already correct
(verified against its documented contract), so they pass green and PIN that
behavior against a future "simplification".
"""
import json
import sys
import unicodedata
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import telemetry_formatters as tf  # noqa: E402


# --- _disp_width: the reason the module exists ---

def test_disp_width_ascii_one_cell_each():
    assert tf._disp_width("abc") == 3


def test_disp_width_cjk_wide_two_cells_each():
    # East-Asian Wide/Fullwidth glyphs occupy two terminal cells
    assert tf._disp_width("中文") == 4


def test_disp_width_vietnamese_nfd_counts_as_one_cell():
    # 'ệ' authored as NFD = base 'e' + 2 combining marks (3 code points) must
    # render as ONE cell — the vi-output case that len() gets wrong.
    nfd = unicodedata.normalize("NFD", "ệ")
    assert len(nfd) > 1                 # genuinely multi-codepoint input
    assert tf._disp_width(nfd) == 1     # but one display cell


def test_disp_width_empty_is_zero():
    assert tf._disp_width("") == 0


# --- _pad: left-justify by display width, not code points ---

def test_pad_uses_display_width_for_cjk():
    # a 1-glyph CJK string is 2 cells wide → padding to 4 adds 2 spaces
    out = tf._pad("中", 4)
    assert tf._disp_width(out) == 4
    assert out == "中  "


def test_pad_never_truncates_when_wider_than_target():
    out = tf._pad("abcd", 2)
    assert out == "abcd"               # max(0, ...) guard: no negative padding


# --- markdown_table ---

def test_markdown_table_empty_rows_emits_placeholder():
    out = tf.markdown_table(["A", "B"], [])
    assert "_(empty)_" in out
    assert out.splitlines()[0] == "| A | B |"


def test_markdown_table_alignment_separators():
    out = tf.markdown_table(["H1", "H2", "H3"], [["x", "y", "z"]],
                            align=["l", "r", "c"])
    sep = out.splitlines()[1]
    left, right, center = [c.strip() for c in sep.strip("|").split("|")]
    assert set(left) == {"-"}                         # left: dashes only
    assert right.endswith(":") and not right.startswith(":")  # right: trailing colon
    assert center.startswith(":") and center.endswith(":")    # center: both colons


def test_markdown_table_pads_columns_to_widest_cell():
    out = tf.markdown_table(["H"], [["short"], ["a-longer-value"]])
    rows = out.splitlines()
    # header + separator + 2 data rows
    assert len(rows) == 4
    # every rendered line has equal display width (aligned grid)
    widths = {tf._disp_width(line) for line in rows}
    assert len(widths) == 1


def test_markdown_table_escapes_pipe_and_newline():
    # A pipe in a cell must be escaped (else it injects an extra column); a newline is
    # folded to a space (else it injects an extra physical row). The formatter's job is
    # VALID markdown from ANY cell content (a future free-form-text lens depends on it).
    out = tf.markdown_table(["Skill", "Note"], [["hs:plan", "a|b"]])
    assert "a\\|b" in out  # the inner pipe is escaped, not left as a raw delimiter
    data = [l for l in out.splitlines() if "hs:plan" in l][0]
    # after removing escaped pipes, a 2-cell row has exactly 3 real delimiters
    assert data.replace("\\|", "").count("|") == 3
    out2 = tf.markdown_table(["A"], [["line1\nline2"]])
    assert len(out2.splitlines()) == 3  # header + sep + 1 data row; no injected line


def test_markdown_table_handles_ragged_rows():
    # A row shorter than the headers must NOT crash (pad it); a wider row must NOT drop
    # its extra cell silently.
    short = tf.markdown_table(["A", "B", "C"], [["x", "y", "z"], ["only"]])
    assert "only" in short  # no IndexError; short row padded and rendered
    wide = tf.markdown_table(["A", "B"], [["x", "y", "EXTRA"]])
    assert "EXTRA" in wide  # wide row's extra cell is not silently dropped
