"""Tests for review_issue_router.py — classify code-review findings into route buckets.

A finding category is classified into one of two routes:
  - "needs-user": the finding touches a user decision (contract, threshold, scope,
    schema, pricing, compliance, trade-off) — do NOT auto-fix; escalate to human.
  - "auto-fix": the finding is a clear correctness/quality issue — drive hs:fix TDD.

Unknown categories default to "needs-user" (safe default: when in doubt, escalate).
"""
import sys
from pathlib import Path


_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import review_issue_router as rir  # noqa: E402


# --- category classification --------------------------------------------------

def test_needs_user_categories():
    """Every category that touches a user decision routes to needs-user."""
    for cat in ("contract", "threshold", "scope", "schema",
                "pricing", "compliance", "trade-off"):
        assert rir.classify_finding(cat) == "needs-user", (
            "expected needs-user for category %r" % cat
        )


def test_auto_fix_categories():
    """Clear correctness/quality categories route to auto-fix."""
    for cat in ("correctness", "dry", "cleanup", "consistency", "security"):
        assert rir.classify_finding(cat) == "auto-fix", (
            "expected auto-fix for category %r" % cat
        )


def test_unknown_category_defaults_needs_user():
    """An unrecognised category defaults to needs-user (never auto-fix the unknown)."""
    assert rir.classify_finding("weird") == "needs-user"
    assert rir.classify_finding("") == "needs-user"
    assert rir.classify_finding("undefined-thing") == "needs-user"


def test_classify_case_insensitive():
    """Input is normalised (lower + strip) before lookup."""
    assert rir.classify_finding("Schema ") == "needs-user"
    assert rir.classify_finding("SECURITY") == "auto-fix"
    assert rir.classify_finding("  COMPLIANCE  ") == "needs-user"
    assert rir.classify_finding("DRY") == "auto-fix"


def test_sets_disjoint():
    """No category appears in both NEEDS_USER and AUTO_FIX."""
    assert rir.NEEDS_USER & rir.AUTO_FIX == frozenset()
