"""test_path_glob.py — the canonical path-glob matcher: fnmatch + leading-**/
strip + basename fallback, consistent across the harness gates.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import path_glob as pg  # noqa: E402


def test_globstar_spans_slashes():
    assert pg.match_path_glob("src/auth/login.py", ["**/auth/**"])


def test_leading_globstar_strip_matches_top_level():
    # the divergence the consolidation fixes: `**/auth/**` must match a
    # top-level `auth/x`, not only a nested one.
    assert pg.match_path_glob("auth/login.py", ["**/auth/**"])


def test_non_matching_path():
    assert not pg.match_path_glob("payments/x.py", ["**/auth/**"])


def test_basename_fallback():
    assert pg.match_path_glob("a/b/config.yaml", ["config.yaml"])


def test_multi_segment_lane_glob():
    assert pg.match_path_glob("plans/260101/plan.md", ["plans/**"])
    assert not pg.match_path_glob("harness/x.py", ["plans/**"])


def test_skips_non_string_and_empty_patterns():
    assert not pg.match_path_glob("x", [None, "", 123])
    assert not pg.match_path_glob(None, ["**"])


def test_match_any_path_inverse_fan():
    paths = ["docs/readme.md", "src/auth/login.py"]
    assert pg.match_any_path(paths, "**/auth/**")
    assert not pg.match_any_path(paths, "**/payment/**")
