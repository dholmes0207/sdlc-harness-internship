"""test_humanize_fence.py — fence open/close semantics for the dash remover.

A fenced code block CLOSES only on a bare marker line: the fence marker (``` or
~~~) optionally followed by trailing whitespace, with no info-string. An OPENING
fence may carry an info-string (e.g. ```python). When a code block's body itself
contains a marker-plus-info line (a fence shown inside another fence), the loose
"starts with the marker" close test misreads that body line as a close, desyncs
the in/out-of-code state, and lets em-dashes inside the code get rewritten. These
tests pin the correct semantics: dashes inside a ```python block survive while a
prose dash outside the block is still converted.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import humanize_dashes as hd  # noqa: E402


def test_info_string_open_does_not_close_a_block():
    # A bare ``` opens; the body shows a ```python fence (marker + info-string)
    # which must NOT be read as the close. The close is the later bare ```.
    text = (
        "prose — a\n"
        "```\n"
        "See example:\n"
        "```python\n"
        "code — here\n"
        "```\n"
        "tail — b\n"
    )
    out, changes = hd.humanize_text(text)
    # in-code dash preserved (the body line was never treated as prose)
    assert "code — here" in out
    assert "code, here" not in out
    # prose outside the (single) block is still converted
    assert "prose, a" in out
    assert "tail, b" in out
    assert 5 not in changes  # the in-code line did not change


def test_python_block_em_dash_preserved_prose_converted():
    # The canonical case: a ```python block whose body carries an em-dash, plus
    # an inner marker-plus-info line so the close test is exercised.
    text = (
        "before — text\n"
        "```python\n"
        "x = 1  # note — inside\n"
        "snippet = '''\n"
        "```bash\n"
        "echo — still in code\n"
        "'''\n"
        "```\n"
        "after — text\n"
    )
    out, changes = hd.humanize_text(text)
    # every dash inside the python block is preserved verbatim
    assert "# note — inside" in out
    assert "echo — still in code" in out
    # prose on both sides of the block is converted
    assert "before, text" in out
    assert "after, text" in out
    # only the two prose lines changed
    assert changes == [1, 9]


def test_close_requires_bare_marker():
    text = "```\nbody — x\n```\nafter — y\n"
    out, changes = hd.humanize_text(text)
    assert "body — x" in out      # in-code preserved
    assert "after, y" in out      # prose after close converted
    assert changes == [4]
