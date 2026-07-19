"""Closeout guards for the unify-rule-systems plan.

The four load-bearing decisions are registered in the ledger, and the GLOSSARY /
config-reference carry the new vocabulary + knobs.

Reads the dev-only docs/ ledger + glossary, so it is dev_repo-scoped (auto-skipped
on an installed copy where docs/ is not shipped).
"""

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_SCRIPTS = _REPO / "harness" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

pytestmark = pytest.mark.dev_repo


def test_closeout_decisions_registered():
    import decision_register
    active = {d["id"]: d for d in decision_register.load_active(_REPO)} \
        if hasattr(decision_register, "load_active") else None
    if active is None:
        # fall back to scanning the ledger text
        text = (_REPO / "docs" / "decisions.yaml").read_text(encoding="utf-8")
        for did in ("DEC-120", "DEC-121", "DEC-122", "DEC-123"):
            assert did in text, did
        return
    for did in ("DEC-120", "DEC-121", "DEC-122", "DEC-123"):
        assert did in active, did
        assert active[did].get("status") == "active"


def test_glossary_has_unify_terms():
    # Read the YAML SSOT (docs/glossary.yaml), not the rendered GLOSSARY.md view:
    # post-migration the SSOT is the source of truth for the vocabulary.
    g = (_REPO / "docs" / "glossary.yaml").read_text(encoding="utf-8")
    for term in ("scope_match (canonical)", "operational zone", "TOFU trust",
                 "rule-coverage gate", "standards.user.yaml"):
        assert term in g, term


def test_config_reference_has_new_knobs():
    c = (_REPO / "docs" / "harness" / "config-reference.md").read_text(encoding="utf-8")
    for knob in ("HARNESS_USER_OVERRIDE", "HARNESS_RULE_COVERAGE", "HARNESS_TRUST_STORE"):
        assert knob in c, knob
    # the retired flat-tree knobs are gone from the doc
    assert "HARNESS_REVIEW_RULES_BASE" not in c
