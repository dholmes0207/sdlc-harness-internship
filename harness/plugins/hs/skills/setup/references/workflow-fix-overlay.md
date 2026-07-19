---
name: workflow-fix-overlay
description: Retired — the write-RBAC overlay is gone; a Workflow --fix subagent writes project source out of the box under the deny-list.
---

# Workflow `--fix` code-lane — RETIRED (no overlay needed)

The per-agent_type whitelist table + the add-only overlay this recipe used to configure
have been **retired** in favour of the two-tier deny-list write-RBAC.

There is nothing to set up any more. Under the deny-list a `workflow-subagent` (like every
subagent) writes **anywhere except the hard floor** — so a Workflow-orchestrated
`code-review --fix` / `security-scan --fix` edits project source (`src/**`, `lib/**`, …)
**out of the box, no overlay, no env**. The only writes it is refused are the hard floor:
the protected core (guard code, `.git`, secrets, the cage-disarm surface) and the harness
binary `harness/**` (carve for `harness/tests/**` only) — a downstream install still cannot
let an agent overwrite the installed harness (restated as an explicit deny).

To see the current scope: `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/check_permission.py --name workflow-subagent`.
A soft-tier widen (never a hard-floor appeal) is a human decision:
`python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/deny_audit.py --request-widen <path>` then edit the
guarded `harness/data/write-deny-policy.yaml`. Background: `harness/rules/orchestration-protocol.md`.
