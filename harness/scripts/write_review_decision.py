#!/usr/bin/env python3
"""write_review_decision.py — write the review-decision gate artifact.

The third gate producer (with write_verification + plan_approval). hs:code-review used
to write review-decision.yaml directly with the Write tool, so it was the one gate
artifact that could not carry a run_seq stamp. Routing it through artifact_io here gives
it the SAME treatment as the other two — atomic same-dir write + run_seq stamp (D1) —
so the orchestrator's watchdog can stale-reject all THREE gate artifacts, not just two.

Gate logic is unchanged: this only writes the record hs:code-review already produces
(verdict/reviewer/role/rationale + optional fields), conforming to
harness/schemas/artifact-review-decision.json. The verdict decision stays with the
reviewer; this is the write path, not the judgment.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import artifact_io  # noqa: E402

_VERDICTS = ("PASS", "PASS_WITH_RISK", "BLOCKED")


def _resolve_reviewer(explicit):
    """Explicit --reviewer wins; else resolve_actor() (attribution, not auth)."""
    if explicit:
        return explicit
    try:
        sys.path.insert(0, str(_HERE.parent / "hooks"))
        import hook_runtime
        return hook_runtime.resolve_actor()
    except Exception:  # noqa: BLE001 — never block a write on attribution
        return "agent:code-reviewer"


def _build_record(args) -> dict:
    rec = {
        "verdict": args.verdict,
        "reviewer": _resolve_reviewer(args.reviewer),
        "role": args.role,
        "rationale": args.rationale,
    }
    # optional fields only when provided — keep the artifact minimal + schema-clean
    for key, val in (("plan_hash", args.plan_hash), ("ticket_id", args.ticket_id),
                     ("effort", args.effort), ("strategy", args.strategy),
                     ("reviewer_engine", args.reviewer_engine),
                     ("reviewer_model", args.reviewer_model)):
        if val is not None:
            rec[key] = val
    if args.rounds_run is not None:
        rec["rounds_run"] = args.rounds_run
    return rec


def _canonical_target(plan_dir: Path) -> Path:
    """.yaml preferred (SSOT), .json legacy. Default to .yaml when neither exists."""
    art = plan_dir / "artifacts"
    yaml_p, json_p = art / "review-decision.yaml", art / "review-decision.json"
    return json_p if json_p.exists() and not yaml_p.exists() else yaml_p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plan_dir", help="active plan dir (path)")
    ap.add_argument("--verdict", required=True, choices=_VERDICTS)
    ap.add_argument("--rationale", required=True, help="WHY — the verdict's justification")
    ap.add_argument("--reviewer", default=None, help="reviewer identity (default: resolve_actor)")
    ap.add_argument("--role", default="reviewer")
    ap.add_argument("--plan-hash", dest="plan_hash", default=None)
    ap.add_argument("--ticket-id", dest="ticket_id", default=None)
    ap.add_argument("--effort", default=None, choices=("low", "medium", "high", "xhigh", "max"))
    ap.add_argument("--rounds-run", dest="rounds_run", type=int, default=None)
    ap.add_argument("--strategy", default=None)
    ap.add_argument("--reviewer-engine", dest="reviewer_engine", default=None)
    ap.add_argument("--reviewer-model", dest="reviewer_model", default=None)
    args = ap.parse_args(argv)

    plan_dir = Path(args.plan_dir).resolve()
    art = plan_dir / "artifacts"
    art.mkdir(parents=True, exist_ok=True)
    target = _canonical_target(plan_dir)
    rec = _build_record(args)
    artifact_io.stamp_and_write(target, rec)
    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
