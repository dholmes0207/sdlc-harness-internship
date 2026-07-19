"""test_config_io.py — shared helper for the YAML config WRITE CLIs: preserve a
file's leading comment header across a programmatic rewrite. One home so the
guard/team/output --set writers cannot drift.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import config_io  # noqa: E402

_DEFAULT = "# default header.\n"


def test_returns_default_when_file_absent(tmp_path):
    assert config_io.leading_comment_block(tmp_path / "nope.yaml", _DEFAULT) == _DEFAULT


def test_keeps_leading_comment_and_blank_run(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("# line one\n# line two\n\nkey: value\n# trailing comment\n",
                 encoding="utf-8")
    head = config_io.leading_comment_block(p, _DEFAULT)
    assert head == "# line one\n# line two\n\n"     # stops at the first non-comment
    assert "key: value" not in head
    assert "trailing comment" not in head


def test_empty_string_when_no_leading_comments(tmp_path):
    p = tmp_path / "x.yaml"
    p.write_text("key: value\n", encoding="utf-8")
    assert config_io.leading_comment_block(p, _DEFAULT) == ""
