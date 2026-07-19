#!/usr/bin/env python3
"""decision_reconcile — drift counter for the Decision Register.

A marker snapshots (max-dec, superseded-count) at the last reconcile; status()
reports how far the register has drifted since. Two consumers: the Stop nudge
(advisory when over threshold) and the release preflight (hard-gate: refuse a cut
while drift is unreconciled).

flip-count is derived from the superseded-count DIFF and is APPROXIMATE — it
undercounts a flip+re-flip and skews while the repo dogfoods its own DECs. It is
advisory-only, NOT audit-grade (R10). That is sufficient for a fail-open nudge and
a "have you reconciled lately" preflight; it is not a forensic flip ledger.
"""
import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path
from typing import Dict, Optional

import yaml

sys.path.append(os.path.dirname(os.path.abspath(__file__)))
import decision_register  # noqa: E402 — sibling; SSOT parse + id scan (DRY)

_DEFAULTS = {
    "reconcile_threshold_new_decs": 15,
    "reconcile_threshold_flips": 8,
    "confirm_ttl_s": 1800,
}


def _marker_path(root) -> Path:
    return Path(root) / "harness" / "state" / "decision-reconcile.json"


def _governance(root) -> Dict:
    """Knobs from <root>/harness/data/decision-governance.yaml, falling back to
    the shipped defaults for any missing/invalid key (a broken knob file never
    silently disables the counter — it just reads as defaults)."""
    env = os.environ.get("HARNESS_DECISION_GOVERNANCE")
    p = Path(env) if env else Path(root) / "harness" / "data" / "decision-governance.yaml"
    out = dict(_DEFAULTS)
    try:
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for k in _DEFAULTS:
                if isinstance(raw.get(k), int):
                    out[k] = raw[k]
    except (FileNotFoundError, OSError, yaml.YAMLError, ValueError):
        pass
    return out


def confirm_ttl_s(root) -> int:
    """Public read of the confirm-token TTL knob (P3 gate reads this)."""
    return _governance(root)["confirm_ttl_s"]


def _current(root) -> Dict[str, int]:
    """(max-dec-number, superseded-count) of the register right now. Empty/missing
    register → both 0 (decision_register.alloc_id returns DEC-1 on empty)."""
    alloc = decision_register.alloc_id(root)  # max+1
    try:
        cur_max = int(alloc.split("-")[1]) - 1
    except (IndexError, ValueError):
        cur_max = 0
    superseded = sum(1 for r in decision_register.parse_decisions(root)
                     if r.get("status") == "superseded")
    return {"max": cur_max, "superseded": superseded}


def _read_marker(root) -> Optional[Dict]:
    try:
        data = json.loads(_marker_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (FileNotFoundError, OSError, ValueError):
        return None


def status(root) -> Dict:
    """Drift since the last marker: {new_decs, flips, over, cur_max, cur_superseded}.
    No marker → baseline = now (new_decs=flips=0, over=False): a fresh install never
    nudges until a reconcile has actually been marked once."""
    cur = _current(root)
    marker = _read_marker(root)
    if marker is None:
        last_max, last_sup = cur["max"], cur["superseded"]
    else:
        last_max = int(marker.get("last_max_dec", cur["max"]))
        last_sup = int(marker.get("last_superseded", cur["superseded"]))
    new_decs = max(0, cur["max"] - last_max)
    flips = max(0, cur["superseded"] - last_sup)
    gov = _governance(root)
    over = (new_decs >= gov["reconcile_threshold_new_decs"]
            or flips >= gov["reconcile_threshold_flips"])
    return {"new_decs": new_decs, "flips": flips, "over": over,
            "cur_max": cur["max"], "cur_superseded": cur["superseded"]}


def mark(root) -> Dict:
    """Snapshot the current register state as the new reconcile baseline. Called by
    the reconcile agent when it finishes, or by hand."""
    cur = _current(root)
    payload = {
        "last_max_dec": cur["max"],
        "last_superseded": cur["superseded"],
        "ts": dt.datetime.now(dt.timezone.utc).isoformat(),
    }
    p = _marker_path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return payload


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=".")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true", help="print drift counts JSON")
    g.add_argument("--mark", action="store_true", help="snapshot current as baseline")
    args = ap.parse_args(argv)
    root = str(Path(args.root).resolve())
    if args.status:
        print(json.dumps(status(root), ensure_ascii=False))
    else:
        print(json.dumps(mark(root), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
