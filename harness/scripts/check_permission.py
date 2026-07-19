#!/usr/bin/env python3
"""check_permission.py — self-service write-scope check for an agent role.

An agent runs `python3 harness/scripts/check_permission.py --name <agent-name>` to see
its OWN effective write scope under the DENY-LIST model — instead of hard-coding scope
into its body. It prints what is BLOCKED (the hard floor: the protected core + the
harness binary) and what is OBSERVED (the soft tier: logged, not blocked), plus the
rule: you may write anywhere else; a write into the hard floor is BLOCKED by
agent_rbac_guard / floor_bash_guard, so STOP and return the raw output. The floor is
caged — an agent cannot widen it. A soft-tier widen is a REQUEST
(`deny_audit.py --request-widen`) a human applies by editing the guarded
write-deny-policy.yaml; an agent never self-grants.
"""
import argparse
import json as _json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
import agent_permissions as ap  # noqa: E402
import write_deny_policy as wdp  # noqa: E402

_DATA = _HERE.parent / "data"


def _rbac_enabled() -> "bool | None":
    """Best-effort: is agent_rbac_guard actually running? None if undeterminable.
    A disabled guard means the floor below is NOT enforced (a dev full-quyen repo)."""
    try:
        hooks_dir = _HERE.parent / "hooks"
        if str(hooks_dir) not in sys.path:
            sys.path.insert(0, str(hooks_dir))
        import hook_runtime
        return bool(hook_runtime.hook_enabled("agent_rbac_guard", "compliance"))
    except Exception:  # noqa: BLE001
        return None


def _soft_rules():
    path = os.environ.get("HARNESS_WRITE_DENY_POLICY") or (_DATA / "write-deny-policy.yaml")
    try:
        return wdp.load_soft_rules(path)
    except wdp.DenyPolicyError:
        return []


def resolve(name: str) -> dict:
    """Describe the deny-list write scope for `name`.

    unrestricted: the top-level session (_parent) is not floor-restricted on the
    tool-Write path. hard_floor: globs a SUBAGENT may not write (core-immune + the
    harness binary). soft_rules: the ordered detective rules (observed, not blocked)."""
    is_parent = (name == ap.ROLE_PARENT
                 or (":" in name and name.split(":", 1)[1] == ap.ROLE_PARENT))
    return {
        "role": name,
        "unrestricted": is_parent,
        "hard_floor": sorted(set(wdp.CORE_IMMUNE) | set(wdp.HARD_BINARY_DENY)),
        "hard_binary_carve": list(wdp.HARD_BINARY_CARVE),
        "soft_rules": [{pol: glob} for pol, glob in _soft_rules()],
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Show an agent role's deny-list write scope (live from the floor).")
    parser.add_argument("--name", required=True,
                        help="agent name, bare or plugin-qualified (developer or hs:developer)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args(argv)

    scope = resolve(args.name)
    rbac_on = _rbac_enabled()

    if args.json:
        scope["rbac_enabled"] = rbac_on
        print(_json.dumps(scope))
        return 0

    print("Agent: %s" % args.name)
    if scope["unrestricted"]:
        print("Write scope: top-level session (_parent) — unrestricted on the tool-Write path.")
    else:
        print("Write scope: you may write ANYWHERE except the hard floor below.")
        print("BLOCKED (hard floor — protected core + harness binary):")
        for g in scope["hard_floor"]:
            print("  - %s" % g)
        print("  (carve-out, writable: %s)" % ", ".join(scope["hard_binary_carve"]))
    if scope["soft_rules"]:
        print("OBSERVED (soft tier — logged, NOT blocked):")
        for rule in scope["soft_rules"]:
            for pol, glob in rule.items():
                print("  - %s %s" % (pol, glob))
    if rbac_on is False:
        print("Status: agent_rbac_guard is currently DISABLED — the floor above is NOT "
              "enforced this session (a dev full-quyen repo).")
    print("Rule: a write INTO the hard floor is BLOCKED — STOP and return the raw output "
          "instead. You cannot widen the floor yourself (it is caged); a soft-tier widen "
          "is a human decision via deny_audit.py --request-widen.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
