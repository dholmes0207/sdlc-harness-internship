#!/usr/bin/env python3
"""agent_permissions — the pure identity-lane decision for agent write-RBAC.

Given a role (the subagent `agent_type` from the PreToolUse payload, or `_parent`
for the top-level agent), a write target, an assembled deny-list `policy`, and the
protected roots, decide the write. Pure + deterministic; the `agent_rbac_guard` hook
owns the payload/IO + policy load, this owns the rule. One home per fact.

Model: a DENY-LIST, not a whitelist. A subagent may write anywhere EXCEPT the code
floor (core-immune + the harness binary minus the tests carve); a soft-tier match is
observed, not blocked. The top-level session is unrestricted on the tool-Write path.
The floor lives in write_deny_policy + deny_matcher; this maps a matcher tier to a
role-aware Verdict.

Trust note: `agent_type` is a platform-set ATTRIBUTION label (same tier as
resolve_actor), not a credential — this gate disciplines a cooperative fleet on a
trusted host, it is NOT insider-proof. Hostile multi-tenant authz stays on the
separate server-issued-token path.
"""
from collections import namedtuple

# The identity-lane decision. block_reason set => hard-block; soft_rule set =>
# detective soft-observe (the write is ALLOWED, the hook emits deny_soft_hit);
# both None => allow.
Verdict = namedtuple("Verdict", ["block_reason", "soft_rule"])
_ALLOW = Verdict(None, None)

# The role label for the top-level agent (its PreToolUse payload carries no
# agent_type) — unrestricted on the tool-Write path.
ROLE_PARENT = "_parent"


def decide(role, target, policy, protected_roots) -> Verdict:
    """Classify a write for `role` against the deny-list `policy` (see Verdict).

    PURE — the hook resolves `protected_roots` and loads `policy`; this reads no
    file/env. Deterministic: same (role, target, policy, roots) → same Verdict.

    The parent (the trusted top-level session) is unrestricted on the tool-Write
    path — write_guard and the Bash floor cover the global cases, and the cage
    blocks the SUBAGENT, not the main session. A subagent is held to the floor:
    core-immune and the harness binary (minus the tests carve) are hard-blocked,
    a soft-tier match is observed-not-blocked, everything else is allowed."""
    import deny_matcher
    from write_deny_policy import TIER_CORE, TIER_HARD_BINARY, TIER_SOFT

    role = role or ROLE_PARENT
    target = str(target).replace("\\", "/")
    decision = deny_matcher.evaluate(target, policy, protected_roots)
    tier = decision.tier

    if tier == TIER_SOFT:
        return Verdict(None, decision.matched_rule)  # detective for every role
    if tier in (TIER_CORE, TIER_HARD_BINARY):
        if role == ROLE_PARENT:
            return _ALLOW
        zone = ("the protected core (guard code, .git, secrets, cage settings)"
                if tier == TIER_CORE
                else "the installed harness binary (harness/**)")
        return Verdict(
            "role %r may not write into %s; '%s' is blocked. Make this change from the "
            "trusted main session or an editor outside the agent." % (role, zone, target),
            None)
    if tier == deny_matcher.TIER_UNRESOLVED:
        if role == ROLE_PARENT:
            return _ALLOW
        return Verdict(
            "write target '%s' does not resolve into any protected root; blocked "
            "fail-closed." % target, None)
    return _ALLOW  # TIER_ALLOW
