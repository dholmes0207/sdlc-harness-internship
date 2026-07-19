"""Behavioral pins for the caged code-review fixes (applied via the owner-run
/tmp applier). These are RED until the caged edits land, GREEN after.

Covers the two behavioral security fixes in caged hooks:
  - secret_scan_before_ship.scannable_added_lines must NOT drop an added content
    line whose own text starts with '++ ' (it appears as '+++ ...' in the diff).
  - write_guard._under must BLOCK (fail-closed) on a resolution error, not fail
    open to False and let an unresolvable bin-zone write slip.
"""
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))


def test_double_plus_content_line_is_scanned():
    import secret_scan_before_ship as s
    diff = (
        "--- a/config.py\n"
        "+++ b/config.py\n"
        "@@ -0,0 +1 @@\n"
        "+++ password = 'hunter2'\n"  # added line; its text is "++ password..."
    )
    scanned = s.scannable_added_lines(diff)
    assert "password = 'hunter2'" in scanned, (
        "a '++ '-prefixed added line was dropped as a header — secret evades")


def test_real_file_header_still_stripped():
    import secret_scan_before_ship as s
    diff = (
        "--- a/app.py\n"
        "+++ b/app.py\n"
        "@@ -0,0 +1 @@\n"
        "+real_added_line = 1\n"
    )
    scanned = s.scannable_added_lines(diff)
    assert "b/app.py" not in scanned  # the +++ b/ header is not scanned as content
    assert "real_added_line = 1" in scanned


def test_under_blocks_on_resolution_error(monkeypatch):
    import write_guard

    def boom(*a, **k):
        raise OSError("simulated symlink loop")

    monkeypatch.setattr(Path, "resolve", boom)
    # both target.resolve() and root.resolve() raise -> the catch-all must BLOCK
    assert write_guard._under("/whatever/path", Path("/opt/bin")) is True
