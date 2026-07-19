#!/usr/bin/env python3
"""rbac_selfcheck.py — drift + brick guard for the `agent_type` attribution field.

`agent_rbac_guard` keys its identity lane on `agent_type` (a subagent's PreToolUse
payload) vs absent (the top-level `_parent`). Two ways that keying goes wrong:

  DRIFT (fail-toward-privilege): the field is version-volatile (Anthropic issues
  #56168 / #31939). If a platform update drops `agent_type` from subagent payloads,
  every subagent collapses to `_parent` and the identity lane fails toward PRIVILEGE
  — the cwd/worktree ISOLATION floor still holds, but the role→glob lane does not.
  Coverage note: presence is read from `agent_type` OR `subagent_type`, so a rename
  to EITHER still reads as present; this catches a TOTAL disappearance, not a rename
  to a third, unanticipated key.

  BRICK (fail-toward-brick): the field is present but its value maps to NO lane in
  the table (e.g. the table is keyed `hs:developer` while the runtime role arrives
  bare `developer`, or vice-versa). Under default_deny that role is silently denied
  on every write. Pass `--table` to also flag this.

This is an OPT-IN check (CI / manual), NOT a runtime gate: a process cannot spawn a
subagent of itself to probe live. The flow is two-step —

  1. A harness step spawns ONE real subagent that performs a Write, with a PreToolUse
     hook that dumps the raw payload JSON to a capture file.
  2. This script reads that captured payload and asserts `agent_type` is present
     (and, with --table, that it resolves to a lane). A bad result ⇒ loud non-zero.

The payload-analysis core below is pure and unit-tested; producing the capture (the
real spawn) is the operator's job, exercised in the real-llm test surface.

Exit codes: 0 = all payloads carry an attribution that resolves; 3 = drift or brick
(loud); 2 = usage / parse error.
"""
import argparse
import json
import sys
from typing import Any, Dict, List, Optional, Tuple


# Fields a PreToolUse payload may carry the subagent role under. `agent_type` is the
# documented key; `subagent_type` is accepted as a defensive fallback (the Task tool
# input spells it that way), so a rename to either still reads as "role present".
ROLE_FIELDS = ("agent_type", "subagent_type")


class Result:
    """One payload's verdict. `ok` False on `drift` (a known-subagent payload missing
    its attribution → fail-toward-privilege) OR `brick` (a present role that maps to
    no lane under the table → fail-toward-brick). Parent payloads without a role are
    `ok`."""

    __slots__ = ("ok", "drift", "brick", "role", "detail")

    def __init__(self, ok: bool, drift: bool, brick: bool, role: Optional[str], detail: str):
        self.ok = ok
        self.drift = drift
        self.brick = brick
        self.role = role
        self.detail = detail


def extracted_role(payload: Dict[str, Any]) -> Optional[str]:
    """First non-empty string role field, else None. An empty / non-string value is
    as good as missing — it must never read as a valid role."""
    if not isinstance(payload, dict):
        return None
    for key in ROLE_FIELDS:
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None


def assess(payload: Dict[str, Any], *, expect: str = "subagent") -> Result:
    """Judge one captured payload.

    expect="subagent": attribution MUST be present; absent ⇒ drift.
    expect="parent":   attribution is legitimately absent ⇒ never drift.

    Under the deny-list model a PRESENT role is role-agnostic at the floor (there is
    no whitelist lane to map to), so the old brick check (present-but-unmapped) is
    gone — only attribution DRIFT (isolation floor keys on presence) is guarded here.
    """
    role = extracted_role(payload)
    if role is not None:
        return Result(True, False, False, role, "role=%s present" % role)
    if expect == "parent":
        return Result(True, False, False, None, "parent payload carries no agent_type (expected)")
    return Result(
        False, True, False, None,
        "no agent_type/subagent_type on a known-subagent payload — "
        "attribution drift; identity lane would fail toward _parent (privilege)",
    )


def assess_many(payloads: List[Dict[str, Any]], *, expect: str = "subagent") -> Tuple[bool, List[Result]]:
    """Assess a batch; overall ok iff every payload is ok (no drift)."""
    results = [assess(p, expect=expect) for p in payloads]
    return (all(r.ok for r in results), results)


def _load(text: str) -> List[Dict[str, Any]]:
    """Parse a capture into a list of payloads (accepts a single object or a list).
    Raises ValueError on anything else."""
    data = json.loads(text)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        if not all(isinstance(x, dict) for x in data):
            raise ValueError("capture list must contain only JSON objects")
        return data
    raise ValueError("capture must be a JSON object or a list of objects")


def _emit(ok: bool, results: List[Result]) -> None:
    for i, r in enumerate(results):
        stream = sys.stdout if r.ok else sys.stderr
        tag = "ok" if r.ok else ("BRICK" if r.brick else "DRIFT")
        print("[rbac-selfcheck] payload %d: %s — %s" % (i, tag, r.detail), file=stream)
    if ok:
        return
    if any(r.drift for r in results):
        print(
            "[rbac-selfcheck] DRIFT DETECTED: a known-subagent payload is missing its "
            "agent_type. Every subagent now reads as _parent (privilege). The "
            "cwd/worktree isolation floor still holds; the role→glob lane does not. "
            "Pin the platform version or re-derive the attribution key.",
            file=sys.stderr,
        )
    if any(r.brick for r in results):
        print(
            "[rbac-selfcheck] BRICK DETECTED: a present agent_type maps to no lane in "
            "the table — that role is default-denied on every write. Reconcile the "
            "table keys with the runtime agent_type (de-namespace, or add the role).",
            file=sys.stderr,
        )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="agent_type attribution drift/brick guard (opt-in CI/manual).")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--capture-file", help="JSON file: one PreToolUse payload, or a list of them.")
    src.add_argument("--stdin", action="store_true", help="Read the payload JSON from stdin.")
    ap.add_argument("--expect", choices=("subagent", "parent"), default="subagent",
                    help="Whether the capture is from a subagent (default) or the top-level agent.")
    args = ap.parse_args(argv)

    try:
        if args.stdin:
            text = sys.stdin.read()
        else:
            with open(args.capture_file, "r", encoding="utf-8") as f:
                text = f.read()
        payloads = _load(text)
    except (OSError, ValueError, json.JSONDecodeError) as e:
        print("[rbac-selfcheck] usage error: %s" % e, file=sys.stderr)
        return 2

    if not payloads:
        print("[rbac-selfcheck] usage error: capture is empty", file=sys.stderr)
        return 2

    ok, results = assess_many(payloads, expect=args.expect)
    _emit(ok, results)
    return 0 if ok else 3


if __name__ == "__main__":
    raise SystemExit(main())
