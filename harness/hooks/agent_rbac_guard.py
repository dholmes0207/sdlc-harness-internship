#!/usr/bin/env python3
"""agent_rbac_guard.py — PreToolUse(Write|Edit|MultiEdit) compliance gate keyed on agent_type.

Reads the role straight off the PreToolUse payload (`agent_type` / `subagent_type`
for a subagent tool call; absent for the top-level agent → `_parent`) — NOT via
resolve_actor, which collapses parent/sub because session_id is shared. Two clauses:

  1. ISOLATION floor — a subagent (non-_parent role) may only write UNDER its
     cwd/worktree root (resolve-then-contain; a worktree-isolated subagent is thus
     physically confined). The top-level agent is exempt.
  2. DENY-LIST floor — within the root, a subagent may write anywhere EXCEPT the
     hard floor (core-immune + the harness binary minus the tests carve); a soft-tier
     match is observed, not blocked. The parent is unrestricted here. See
     agent_permissions.decide + write_deny_policy.

A violation is BLOCKED fail-closed via run_compliance_hook (exit 2 + reason),
softened to a warn under the lenient guard policy (registered _ENFORCE).

ROLE RESOLUTION: a fully ABSENT attribution is the real top-level agent (`_parent`).
A PRESENT-but-empty/blank attribution is instead a DRIFTED subagent (its agent_type
vanished) — it is mapped to a confined `_drifted` sentinel, NOT `_parent`, so an
attribution glitch fails toward containment (default_deny blocks it), never toward
write-everything. A namespaced role (`hs:developer`) is de-namespaced to its bare
table key inside agent_permissions.

ISOLATION ROOT (global-install aware): the confinement root is the payload `cwd`
(the subagent's worktree) > `$CLAUDE_PROJECT_DIR` > the per-project root derived
from data_root(). Under a global bin with NO worktree cwd and NO project dir the
root is UNRESOLVED — a confined role then fails CLOSED (blocked) rather than
decaying to the hook process CWD (the old `"."` fallback), which would silently
confine a subagent to wherever the hook happened to run.

HONESTY (mirrors write_guard/fs_guard): `agent_type` is a platform-set ATTRIBUTION
label, not a credential — this disciplines a cooperative fleet on a trusted host, it
is NOT insider-proof and does NOT judge intent (a hijacked-but-in-role write is
byte-identical at the gate). It sees tool Writes, not Bash-spelled writes
(`echo>`, `sed -i`) — those need the worktree boundary + the git-diff floor.
Hostile multi-tenant authz stays on the separate server-issued-token path.

Additive-skip: an absent / roleless table → inert (pass, no isolation either), so a
fresh install never bricks the fleet. A present-but-malformed table fails closed.
"""
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(_HERE)
sys.path.append(os.path.join(os.path.dirname(_HERE), "scripts"))

import hook_runtime  # noqa: E402
import trace_log     # noqa: E402
# H4: reuse write_guard's MCP write-verb/path-key heuristics (one guarded
# classification, two observers — mirrors gate_stage importing bash_write_guard
# for shell_write_targets). A `mcp__<server>__<method>` tool is invisible to
# _WRITE_TOOLS below (INV-3 F-8c, DECISIONS.md H4); the isolation + identity-lane
# clauses in core() below must see it too, not just the path-containment gate.
import write_guard   # noqa: E402
import write_deny_policy as wdp  # noqa: E402 — the deny-list floor (replaces the whitelist table)

HOOK_CLASS = "compliance"
_HOOK = "agent_rbac_guard"
_DENY_POLICY_REL = "harness/data/write-deny-policy.yaml"
_WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# A present-but-empty/blank attribution resolves to this sentinel — a confined,
# undeclared role (the floor holds it off the protected zone), never the parent.
_DRIFTED_ROLE = "_drifted"


def _resolve_role(data: dict, parent_role: str) -> str:
    """The write's role. A fully ABSENT attribution → the top-level `parent_role`.
    A PRESENT-but-empty/blank/non-string attribution → the confined `_drifted`
    sentinel (an attribution glitch must not read as the unrestricted parent)."""
    for key in ("agent_type", "subagent_type"):
        if key in data:
            val = data.get(key)
            if isinstance(val, str) and val.strip():
                resolved = val.strip()
                # A PRESENT attribution that spells the reserved parent sentinel
                # (verbatim or plugin-qualified `hs:_parent`) must NOT inherit the
                # unrestricted top-level lane — only a fully ABSENT attribution may
                # be the parent. Treat the collision as drift (confined, default_deny),
                # the symmetric guard to the present-but-empty case below.
                bare = resolved.split(":", 1)[1] if ":" in resolved else resolved
                if parent_role in (resolved, bare):
                    return _DRIFTED_ROLE
                return resolved
            return _DRIFTED_ROLE  # present-but-empty/blank/non-string → drift
    return parent_role


def _project_root():
    """The per-project confinement root derived from the phase-1 data-home
    resolver: data_root().parent (the project dir). None when it cannot resolve
    (a global bin with no project dir) so the caller fails closed rather than
    decaying to the hook process CWD."""
    try:
        import harness_paths
        dr = harness_paths.data_root()
        if harness_paths.data_root_unresolved(dr):
            return None
        return str(dr.parent)
    except Exception:  # noqa: BLE001 — never brick the gate on a resolver hiccup
        return None


def _isolation_root(data: dict):
    """The root a subagent must stay under: payload cwd (its worktree) >
    CLAUDE_PROJECT_DIR > data_root()-derived project root. None → unresolved."""
    return (data.get("cwd") or os.environ.get("CLAUDE_PROJECT_DIR")
            or _project_root())


def _contain(target: str, root: str):
    """Repo-relative POSIX path via resolve-then-contain (symlink-safe; NOT a
    string-prefix). A RELATIVE target is resolved against `root` (the payload cwd),
    NOT the hook process cwd — so a worktree subagent's in-tree write is not
    false-flagged. Returns None when `target` escapes `root` — that None is the
    isolation-floor signal.

    Assumes the working filesystem is responsive: Path.resolve() is unbounded, so
    a symlink into a hung mount could in theory stall the hook (acceptable — the
    repo working tree is local)."""
    try:
        t = Path(target)
        if not t.is_absolute():
            t = Path(root) / t
        t = t.resolve()
        r = Path(root).resolve()
        return str(t.relative_to(r)).replace("\\", "/")
    except Exception:  # noqa: BLE001
        return None


def _block(data: dict, role: str, reason: str, target: str) -> str:
    session = data.get("session_id")
    trace_log.append_event(
        hook=_HOOK, event="agent_rbac_block", session=session,
        tool=data.get("tool_name"),
        actor=hook_runtime.resolve_actor(session_id=session),
        status="BLOCKED", note="role=%s %s" % (role, reason),
        target=str(target).replace("\\", "/"))
    return reason


def _load_deny_policy():
    """Assemble the deny-list policy: code-constant floor + the bin-global soft
    config. IO lives here (the hook), so agent_permissions.decide stays pure."""
    bin_root = write_guard._bin_root()
    try:
        soft = wdp.load_soft_rules(Path(bin_root) / _DENY_POLICY_REL)
    except wdp.DenyPolicyError:
        soft = []  # detective config broken: keep the HARD floor, never brick the gate
    return wdp.assemble_policy(soft)


def _deny_block(data: dict, role: str, reason: str, target: str) -> str:
    session = data.get("session_id")
    trace_log.append_event(
        hook=_HOOK, event=wdp.EVENT_HARD_BLOCK, session=session,
        tool=data.get("tool_name"),
        actor=hook_runtime.resolve_actor(session_id=session),
        status="BLOCKED", note="role=%s deny-list" % role,
        target=str(target).replace("\\", "/"))
    return reason


def _emit_soft(data: dict, role: str, target: str, soft_rule: str) -> None:
    """Detective soft-tier observation (telemetry, fail-open — never blocks)."""
    try:
        session = data.get("session_id")
        trace_log.append_event(
            hook=_HOOK, event=wdp.EVENT_SOFT_HIT, session=session,
            tool=data.get("tool_name"),
            actor=hook_runtime.resolve_actor(session_id=session),
            status="OBSERVED", note="role=%s soft_rule=%s" % (role, soft_rule),
            target=str(target).replace("\\", "/"))
    except Exception:  # noqa: BLE001 — soft emission is telemetry, never fatal
        pass


def core(data: dict):
    """None ⇒ pass; string ⇒ block reason (run_compliance_hook contract)."""
    try:
        import agent_permissions as ap
    except ImportError:
        return None  # additive-skip: permissions module unavailable (gate inert)
    except Exception as e:
        return "agent-permissions import failed: %s" % e  # loud failure

    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    tool_name = data.get("tool_name")
    mcp_unresolvable = False
    if tool_name in _WRITE_TOOLS:
        target = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not isinstance(target, str) or not target:
            return None
    elif write_guard.is_mcp_tool(tool_name):
        # H4: gate a write-shaped MCP call under the same isolation + identity
        # rules as a native Write; a read-shaped method is out of scope here.
        if not write_guard.mcp_write_shaped(tool_name):
            return None
        target = write_guard.mcp_candidate_path(tool_input)
        mcp_unresolvable = target is None
        if mcp_unresolvable:
            target = tool_name  # placeholder target for the block message below
    else:
        return None

    role = _resolve_role(data, ap.ROLE_PARENT)

    if mcp_unresolvable:
        # The write target is unknowable — an unconfined role passing this gate
        # would have no RBAC lane check at all, so this fails CLOSED for every
        # role including the parent (unlike the native-tool isolation-floor
        # arm below, which exempts the parent: there the parent's write STILL
        # names a real path other layers can audit; here there is no path to
        # audit at all).
        return _block(
            data, role,
            "%s looks write-shaped (matched a write verb in its method name) "
            "but no recognizable path field was found in its tool_input — the "
            "write target is unknowable, so this is blocked fail-closed."
            % tool_name, tool_name)

    root = _isolation_root(data)
    if not root:
        # No worktree cwd, no project dir, no derivable project root (a global bin
        # with CLAUDE_PROJECT_DIR unset). The isolation floor cannot decay to the
        # hook process CWD — fail CLOSED for a confined role; the parent stays exempt.
        if role == ap.ROLE_PARENT:
            return None
        return _block(data, role,
                      "isolation: role %r has no resolvable project root "
                      "(no worktree cwd and CLAUDE_PROJECT_DIR unset under a global "
                      "bin) — cannot confine the write; set CLAUDE_PROJECT_DIR."
                      % role, target)
    rel = _contain(target, root)

    # clause 1 — isolation floor: a subagent must stay under its cwd/worktree root
    if rel is None:
        if role == ap.ROLE_PARENT:
            return None  # top-level agent is not containment-confined
        return _block(data, role,
                      "isolation: role %r may not write outside its worktree/cwd "
                      "root; '%s' escapes." % (role, str(target).replace("\\", "/")),
                      target)

    # clause 2 — deny-list floor (matched within the isolation root): a subagent is
    # held off the protected core + harness binary; the parent is unrestricted here.
    policy = _load_deny_policy()
    verdict = ap.decide(role, target, policy, [root])
    if verdict.block_reason:
        return _deny_block(data, role, verdict.block_reason, rel)
    if verdict.soft_rule:
        _emit_soft(data, role, rel, verdict.soft_rule)  # detective — telemetry, no block
    return None


def main() -> None:
    hook_runtime.compliance_skip_or_run(_HOOK, core, skip_event="agent_rbac_skip")


if __name__ == "__main__":
    main()
