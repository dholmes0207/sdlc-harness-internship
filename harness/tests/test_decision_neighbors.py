"""decision_neighbors — pure blast-radius detector + scope classifier + digest.

Fixtures are plain record dicts (the shape parse_decisions emits): no file I/O,
no register on disk. The detector never loads the SSOT itself — the caller
passes parsed records in, so the write path never feeds 40k tokens to a model.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import decision_neighbors as dn  # noqa: E402


def _rec(dec_id, title="", rationale="", affects=""):
    return {"id": dec_id, "title": title, "rationale": rationale,
            "affects": affects, "status": "active"}


def test_keyword_overlap_detects_neighbor():
    records = [
        _rec("DEC-1", "Token rotation cadence",
             "rotate the refresh credential on a fixed cadence interval"),
        _rec("DEC-2", "Refresh rotation window",
             "the rotation cadence interval governs refresh lifetime"),
        _rec("DEC-3", "Unrelated thing", "the and for with from that this"),
    ]
    neigh = dn.neighbors(records, "DEC-1")
    ids = [n["id"] for n in neigh]
    assert "DEC-2" in ids
    assert "DEC-3" not in ids


def test_name_mention_detects_neighbor():
    records = [
        _rec("DEC-50", "New ruling", "this supersedes the DEC-12 rule on caching"),
        _rec("DEC-12", "Cache rule", "original caching policy"),
    ]
    fwd = [n["id"] for n in dn.neighbors(records, "DEC-50")]
    assert "DEC-12" in fwd
    # the relation is symmetric: looking from DEC-12, DEC-50 names it
    rev = [n["id"] for n in dn.neighbors(records, "DEC-12")]
    assert "DEC-50" in rev


def test_affects_relation():
    records = [
        _rec("DEC-7", "A", "alpha", affects="PRD-checkout payment-flow"),
        _rec("DEC-8", "B", "beta", affects="payment-flow ledger"),
        _rec("DEC-9", "C", "gamma", affects="unrelated-surface"),
    ]
    ids = [n["id"] for n in dn.neighbors(records, "DEC-7")]
    assert "DEC-8" in ids
    assert "DEC-9" not in ids


def test_target_excluded_and_topk():
    records = [_rec("DEC-1", "rotation cadence", "rotation cadence interval token")]
    for i in range(2, 15):
        records.append(_rec("DEC-%d" % i, "rotation cadence",
                            "rotation cadence interval token"))
    neigh = dn.neighbors(records, "DEC-1", top_k=5)
    ids = [n["id"] for n in neigh]
    assert "DEC-1" not in ids          # target never neighbors itself
    assert len(ids) == 5               # cut to top_k
    # deterministic order: stable across calls
    assert ids == [n["id"] for n in dn.neighbors(records, "DEC-1", top_k=5)]


def test_classify_in_vs_cross():
    neigh = [{"id": "DEC-12", "reasons": ["keyword"], "score": 3},
             {"id": "DEC-99", "reasons": ["keyword"], "score": 3}]
    scope = dn.classify_scope(neigh, "the active plan references DEC-12 explicitly")
    assert scope["in_scope"] == ["DEC-12"]
    assert scope["cross_scope"] == ["DEC-99"]


def test_classify_no_active_plan():
    neigh = [{"id": "DEC-12", "reasons": ["keyword"], "score": 3},
             {"id": "DEC-99", "reasons": ["keyword"], "score": 3}]
    scope = dn.classify_scope(neigh, None)
    assert set(scope["cross_scope"]) == {"DEC-12", "DEC-99"}
    assert scope["in_scope"] == []
    # empty string is the same fail-safe as None
    assert set(dn.classify_scope(neigh, "")["cross_scope"]) == {"DEC-12", "DEC-99"}


def test_classify_in_scope_from_validation_log():
    # R6: an id mentioned only in the Validation Log / artifact text (folded into
    # active_plan_text at the call site) still counts in_scope — a DEC created
    # mid-execution is named there before it reaches plan.md.
    neigh = [{"id": "DEC-200", "reasons": ["name-mention"], "score": 100}]
    vl_text = "## Validation Log\n- VL-9 | chốt DEC-200 ngưỡng N=15 ..."
    assert dn.classify_scope(neigh, vl_text)["in_scope"] == ["DEC-200"]


def test_word_boundary_not_substring():
    # DEC-1 must not match "DEC-12" in the plan text (substring false-positive)
    neigh = [{"id": "DEC-1", "reasons": ["keyword"], "score": 2}]
    assert dn.classify_scope(neigh, "plan mentions DEC-12 only")["cross_scope"] == ["DEC-1"]


def test_neighbors_digest_stable_and_order_insensitive():
    a = dn.neighbors_digest(["DEC-3", "DEC-1", "DEC-2"])
    b = dn.neighbors_digest(["DEC-1", "DEC-2", "DEC-3"])
    assert a == b                       # order-insensitive (sorted internally)
    assert len(a) == 12 and all(c in "0123456789abcdef" for c in a)
    assert dn.neighbors_digest(["DEC-1", "DEC-2"]) != a   # different set → different digest
    assert dn.neighbors_digest([]) == dn.neighbors_digest([])  # stable on empty


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
