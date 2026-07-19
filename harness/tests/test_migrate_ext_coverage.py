"""test_migrate_ext_coverage.py — the decomposition rewrite covers JS/TS source files.

The decomposition migrate engine rewrites `/hs:<skill>` route references to their new
group-prefixed form across text files. Ported skills ship JavaScript/TypeScript scripts
(`.js`, `.cjs`, `.ts`, …) that can carry such references; if the engine's text-suffix
filter omits those extensions it silently leaves dangling refs in them. This guard pins
the filter to include the JS/TS family.
"""
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import migrate_decomposition as md  # noqa: E402


def test_text_suffixes_cover_js_ts_family():
    for ext in (".ts", ".tsx", ".js", ".jsx", ".cjs", ".mjs"):
        assert ext in md._TEXT_SUFFIXES, "migrate text filter omits %s" % ext


def test_existing_text_suffixes_retained():
    # widening must not drop the originals
    for ext in (".md", ".py", ".yaml", ".json", ".sh"):
        assert ext in md._TEXT_SUFFIXES
