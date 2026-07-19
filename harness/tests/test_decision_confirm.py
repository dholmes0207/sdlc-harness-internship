"""decision_confirm — hash-bound single-use confirm token for a cross-scope flip.

Posture: tamper-EVIDENT + raise-price (the plan_approval model), NOT
authentication. The token binds (target, neighbors_digest); a stale/generic
token cannot cover a different flip. now is injected as an epoch-second param
(R3) so TTL is testable without sleeping or the date-only HARNESS_NOW seam.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import decision_confirm as dc  # noqa: E402


def test_write_then_verify_consumes(tmp_path):
    dc.write_confirm(str(tmp_path), "DEC-5", ["DEC-1", "DEC-2"], now=1000.0)
    assert dc.verify_and_consume(str(tmp_path), "DEC-5", ["DEC-1", "DEC-2"],
                                 ttl_s=1800, now=1100.0) is True
    # single-use: the token is gone after a successful consume
    assert dc.verify_and_consume(str(tmp_path), "DEC-5", ["DEC-1", "DEC-2"],
                                 ttl_s=1800, now=1100.0) is False


def test_digest_mismatch_rejected(tmp_path):
    dc.write_confirm(str(tmp_path), "DEC-5", ["DEC-1", "DEC-2"], now=1000.0)
    # a token for a DIFFERENT cross-scope set must not cover this flip
    assert dc.verify_and_consume(str(tmp_path), "DEC-5", ["DEC-1", "DEC-3"],
                                 ttl_s=1800, now=1100.0) is False


def test_ttl_expired_rejected(tmp_path):
    dc.write_confirm(str(tmp_path), "DEC-5", ["DEC-1"], now=1000.0)
    assert dc.verify_and_consume(str(tmp_path), "DEC-5", ["DEC-1"],
                                 ttl_s=1800, now=1000.0 + 1801) is False


def test_order_insensitive_match(tmp_path):
    dc.write_confirm(str(tmp_path), "DEC-5", ["DEC-2", "DEC-1"], now=1000.0)
    assert dc.verify_and_consume(str(tmp_path), "DEC-5", ["DEC-1", "DEC-2"],
                                 ttl_s=1800, now=1100.0) is True


def test_token_under_state_fence(tmp_path):
    import decision_neighbors as dn
    digest = dn.neighbors_digest(["DEC-1", "DEC-2"])
    p = dc._token_path(str(tmp_path), "DEC-5", digest)
    rel = p.relative_to(tmp_path).as_posix()
    assert rel.startswith("harness/state/decision-confirm/")
    # a different root never reaches into tmp_path
    other = dc._token_path("/some/other/root", "DEC-5", digest)
    assert tmp_path not in other.parents


def test_trace_events_emitted(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(dc.trace_log, "append_event",
                        lambda *a, **k: events.append(k.get("event") or (a[1] if len(a) > 1 else None)))
    dc.write_confirm(str(tmp_path), "DEC-5", ["DEC-1"], now=1000.0)
    dc.verify_and_consume(str(tmp_path), "DEC-5", ["DEC-1"], ttl_s=1800, now=1100.0)
    assert "decision_flip_confirm_written" in events
    assert "decision_flip_confirmed" in events


def test_missing_token_returns_false(tmp_path):
    assert dc.verify_and_consume(str(tmp_path), "DEC-9", ["DEC-1"],
                                 ttl_s=1800, now=1000.0) is False


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
