"""decision_reconcile — reconcile counter (marker + status/mark) used by the
nudge and the release preflight.

The marker snapshots (max-dec, superseded-count) at the last reconcile; status
reports the drift since. flip-count via superseded-diff is APPROXIMATE
(advisory-only, not audit-grade — R10). A registry-agnostic helper writes a tiny
decisions.yaml fixture so the counter logic is tested without the register CLI.
"""
import json
import os
import sys
from pathlib import Path

import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))

import decision_reconcile as dr  # noqa: E402


def _write_register(root, records):
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "decisions.yaml").write_text(yaml.safe_dump(records, sort_keys=False),
                                         encoding="utf-8")


def _rec(n, status="active"):
    return {"id": "DEC-%d" % n, "status": status, "date": "2026-06-29",
            "actor": "u", "ts": "t", "title": "t%d" % n, "rationale": "why"}


def _governance(root, new_thr=15, flip_thr=8):
    data = root / "harness" / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "decision-governance.yaml").write_text(
        "reconcile_threshold_new_decs: %d\nreconcile_threshold_flips: %d\n"
        % (new_thr, flip_thr), encoding="utf-8")


def test_status_counts_new_and_flips(tmp_path):
    _governance(tmp_path)
    _write_register(tmp_path, [_rec(1), _rec(2), _rec(3)])
    dr.mark(str(tmp_path))                       # baseline at max=3, superseded=0
    _write_register(tmp_path, [_rec(1), _rec(2, "superseded"), _rec(3),
                               _rec(4), _rec(5)])
    st = dr.status(str(tmp_path))
    assert st["new_decs"] == 2                   # DEC-4, DEC-5
    assert st["flips"] == 1                      # DEC-2 retired


def test_marker_absent_baselines_no_nudge(tmp_path):
    _governance(tmp_path)
    _write_register(tmp_path, [_rec(1), _rec(2)])
    st = dr.status(str(tmp_path))                # no marker yet
    assert st["new_decs"] == 0 and st["flips"] == 0
    assert st["over"] is False


def test_mark_writes_snapshot(tmp_path):
    _write_register(tmp_path, [_rec(1), _rec(2), _rec(3, "superseded")])
    dr.mark(str(tmp_path))
    marker = json.loads((tmp_path / "harness" / "state"
                         / "decision-reconcile.json").read_text())
    assert marker["last_max_dec"] == 3
    assert marker["last_superseded"] == 1


def test_over_threshold_true(tmp_path):
    _governance(tmp_path, new_thr=2, flip_thr=8)
    _write_register(tmp_path, [_rec(1)])
    dr.mark(str(tmp_path))
    _write_register(tmp_path, [_rec(1), _rec(2), _rec(3)])  # +2 new >= 2
    assert dr.status(str(tmp_path))["over"] is True


# ---------- P5: decision-reconciler agent (presence + invariant strings) ----------

_AGENT = (Path(__file__).resolve().parent.parent / "plugins" / "hs" / "agents"
          / "decision-reconciler.md")


def test_decision_reconciler_registered():
    assert _AGENT.is_file()
    head = _AGENT.read_text(encoding="utf-8")
    assert "name: decision-reconciler" in head


def test_decision_reconciler_keeps_ssot_invariant():
    text = _AGENT.read_text(encoding="utf-8")
    # the two load-bearing constraints must survive any future edit (DC-4)
    assert "MUST NOT edit the contents of decisions.yaml" in text
    assert "Ask the user when you cannot reconcile" in text


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
