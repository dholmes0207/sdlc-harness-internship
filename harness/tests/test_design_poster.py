"""Poster module in hs:design — BM25 search + model-agnostic prompt generation.

The poster module mirrors the logo/cip pattern: a BM25 engine over curated CSV knowledge
(style / palette / layout / texture) plus a generator that emits a TEXT PROMPT for any
image-gen model (no model call at generation time). analyze.py + cluster.py are the upstream
data-build tools (Gemini vision + clustering) — importable without their optional deps, which
load lazily only when you actually rebuild the CSVs.

DRY guard: there is ONE BM25 implementation in the module (core.BM25), reused by search — no
second divergent copy of the scoring math.
"""
import csv
import re
import subprocess
import sys
from pathlib import Path

_DESIGN = Path(__file__).resolve().parents[1] / "plugins" / "hs" / "skills" / "design"
_PSCRIPTS = _DESIGN / "scripts" / "poster"
_PDATA = _DESIGN / "data" / "poster"
if str(_PSCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PSCRIPTS))

_DOT_CLAUDE = "." + "claude/"
_FORBIDDEN = (_DOT_CLAUDE + "skills/", _DOT_CLAUDE + "hooks/")

_CSV_EXPECT = {
    "poster-styles.csv": "Style Name",
    "poster-palettes.csv": "Palette Name",
    "poster-layouts.csv": "Layout Name",
    "poster-textures.csv": "Texture Name",
}


def test_csv_data_loads_with_expected_columns():
    for fname, col in _CSV_EXPECT.items():
        p = _PDATA / fname
        assert p.is_file(), "missing poster CSV %s" % fname
        rows = list(csv.DictReader(p.open(encoding="utf-8")))
        assert rows, "%s has no rows" % fname
        assert col in rows[0], "%s missing column %r" % (fname, col)


def test_bm25_search_returns_ranked_hits():
    import core
    import search as poster_search
    rows = poster_search.load_csv("style")
    assert rows, "no style rows loaded"
    ranked = poster_search.bm25_rank(rows, "geometric typographic minimalist",
                                     poster_search.SEARCH_COLS["style"], top_k=5)
    assert ranked and len(ranked) <= 5
    # the top hit must actually mention a query term somewhere in its searched columns
    top_blob = " ".join(str(ranked[0].get(c, "")) for c in poster_search.SEARCH_COLS["style"]).lower()
    assert any(t in top_blob for t in ("geometric", "typographic", "minimalist"))


def test_generate_emits_model_agnostic_prompt_without_api():
    out = subprocess.run(
        [sys.executable, str(_PSCRIPTS / "generate.py"), "--topic", "AI Conference", "--seed", "42"],
        capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, "generate.py failed: %s" % out.stderr
    text = out.stdout
    assert "AI Conference" in text
    assert "STYLE" in text and "PALETTE" in text, "prompt missing locked-axis sections"


def test_single_bm25_engine_in_module():
    """DRY: exactly one BM25 class in the poster module (core.BM25); search reuses it."""
    class_defs = []
    for py in _PSCRIPTS.glob("*.py"):
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            if re.match(r"\s*class\s+BM25\b", line):
                class_defs.append("%s:%d" % (py.name, i))
    assert len(class_defs) == 1, "expected exactly one BM25 class, found %s" % class_defs
    assert class_defs[0].startswith("core.py"), "the BM25 engine must live in core.py"
    assert "core.BM25" in (_PSCRIPTS / "search.py").read_text(encoding="utf-8"), (
        "search.py must reuse core.BM25, not reimplement scoring")


def test_skill_md_routes_poster_in_three_places():
    text = (_DESIGN / "SKILL.md").read_text(encoding="utf-8")
    lower = text.lower()
    assert lower.count("poster") >= 3, "expected poster referenced in routing + built-in + scripts"


def test_no_forbidden_claude_literals_in_poster_files():
    files = list(_PSCRIPTS.glob("*.py")) + [
        _DESIGN / "references" / "poster-design.md",
        _DESIGN / "references" / "poster-prompt-engineering.md",
    ]
    for p in files:
        text = p.read_text(encoding="utf-8")
        for lit in _FORBIDDEN:
            assert lit not in text, "forbidden literal %r in %s" % (lit, p.name)
