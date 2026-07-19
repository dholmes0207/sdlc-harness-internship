"""Tests for scope_match.py — the canonical path-glob matcher.

scope_match unifies the two hand-rolled `_glob_to_re` copies (review_rules +
risk_rubric) into one regex translation, and ADDS the relation predicates
`globs_overlap` / `glob_subsumes` that conflict-detect and coverage-derive need
(fnmatch/path_glob cannot answer "do these two globs intersect").

Glob semantics (gitignore-style, same as the old loaders):
  `**/` zero-or-more leading segments, `**` anything, `*` within a segment
  (no `/`), `?` one non-slash char.
"""

import os
import sys

sys.path.append(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

from scope_match import (  # noqa: E402
    glob_to_regex,
    scope_matches,
    globs_overlap,
    glob_subsumes,
)


# --- Tests Before: lock the existing match semantics (parity) -----------------

# (globs, changed_files) -> expected, carried from the review-rules loader's
# behaviour so the canonical matcher reproduces it byte-for-byte.
_PARITY = [
    (["**/*.py"], ["src/auth/login.py"], True),
    (["src/**"], ["src/a.py"], True),
    (["src/**"], ["lib/a.py"], False),
    (["*.py"], ["a.py"], True),
    (["*.py"], ["src/a.py"], False),          # `*` does not cross `/`
    (["**/auth/**"], ["auth/x"], True),        # `**/` optional leading segs
    (["**/auth/**"], ["src/auth/x"], True),
    (["docs/*.md"], ["docs/readme.md"], True),
    (["docs/*.md"], ["docs/sub/x.md"], False),
    (["a.py", "b.py"], ["c.py"], False),       # OR over globs, none match
    (["a.py", "b.py"], ["b.py"], True),
]


def test_scope_parity_review_rules():
    for globs, files, expected in _PARITY:
        assert scope_matches(globs, files) is expected, (globs, files)


# --- Tests After: new relation predicates -------------------------------------

_OVERLAP_TRUE = [
    ("src/**", "src/auth/*.py"),
    ("**/*.py", "src/*.py"),
    ("src/*.py", "src/*.py"),
    ("a/**", "a/b/c.py"),
    ("**/*.tsx", "components/Button.tsx"),
    ("src/**/*.py", "src/auth/login.py"),
    ("*.py", "*.py"),
]

_OVERLAP_FALSE = [
    ("src/*.py", "docs/*.md"),
    ("*.py", "*.md"),
    ("src/**", "lib/**"),
    ("a/*.py", "b/*.py"),
    ("docs/*.md", "src/*.py"),
    ("*.go", "*.ts"),
    ("python/**", "go/**"),
]


def test_globs_overlap_known_pairs():
    for a, b in _OVERLAP_TRUE:
        assert globs_overlap(a, b) is True, ("expected overlap", a, b)
        assert globs_overlap(b, a) is True, ("overlap symmetric", b, a)
    for a, b in _OVERLAP_FALSE:
        assert globs_overlap(a, b) is False, ("expected disjoint", a, b)
        assert globs_overlap(b, a) is False, ("disjoint symmetric", b, a)


def test_glob_subsumes():
    assert glob_subsumes("src/**", "src/auth/x.py") is True
    assert glob_subsumes("src/**", "lib/x.py") is False
    assert glob_subsumes("**/*.py", "src/a.py") is True
    assert glob_subsumes("*.py", "*.py") is True
    # a narrower glob does NOT subsume a broader one
    assert glob_subsumes("src/*.py", "src/**") is False
    # disjoint globs never subsume
    assert glob_subsumes("docs/**", "src/x.py") is False


def test_case_knob():
    # case-insensitive catches a capitalised filename (risk-gate posture)
    assert glob_to_regex("**/auth*", case_insensitive=True).match("AuthService.java")
    # default case-sensitive does NOT (review-rules posture)
    assert glob_to_regex("**/auth*", case_insensitive=False).match("AuthService.java") is None

    # the divergence is preserved at the caller seams:
    import mechanical_runner
    import risk_rubric
    # the mechanical runner stays case-sensitive (gitignore semantics)
    assert mechanical_runner._scope_matches(["**/auth*"], ["AuthService.java"]) is False
    # risk-rubric stays case-insensitive (would under-rate security otherwise)
    assert risk_rubric._any_match(["**/auth*"], ["AuthService.java"]) is True


def test_no_dup_glob_to_re():
    """The duplicated `_glob_to_re` defs are gone — one canonical source."""
    scripts = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts")
    for fn in ("risk_rubric.py", "mechanical_runner.py"):
        text = open(os.path.join(scripts, fn), encoding="utf-8").read()
        assert "def _glob_to_re(" not in text, f"{fn} still defines its own _glob_to_re"
