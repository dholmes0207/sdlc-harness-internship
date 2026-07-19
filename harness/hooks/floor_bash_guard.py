#!/usr/bin/env python3
"""floor_bash_guard.py — PreToolUse(Bash) compliance floor for Bash-spelled writes.

Closes the "guard blind to Bash" hole: agent_rbac_guard/write_guard see tool Writes,
not `echo >`, `sed -i`, `tee`, `cp`. This gate blocks a Bash write into the deny-list
floor (core-immune, OR harness/** minus the tests carve), fail-closed, for EVERY actor
— the Bash path never reads agent_type (probed: gate_stage / bash_write_guard /
bash_safety_guard do not), so there is NO parent exemption here (mirrors write_guard's
whole-bin catch-all "for every actor").

Detection is two arms plus two narrow extras:
  ARM 1 (precise): shell_write_targets() -> each resolved write target -> deny_matcher.
    A hard-tier target blocks. This is a WRITE floor, not a read gate: `cat core > /tmp/y`
    writes /tmp/y (allowed); reading guard source does not disarm the cage.
  ARM 2 (obfuscation, triple-coincidence): a wrapper token (eval / sh -c / python -c /
    $() / backtick / xargs) AND a protected-zone literal AND a write-verb, all in the RAW
    command -> block. shell_write_targets returns 0 candidates for a quoted/wrapped write,
    so without this the wrapper is a clean bypass. Over-block is bounded to the triple-
    coincidence (undecidable read/write inside a wrapper).
  ln-into-carve: `ln -s* <src> harness/tests/**` + a redirect in the same command ->
    block. The symlink does not exist yet at PreToolUse, so realpath membership cannot
    catch it; block on the command shape instead.
  dynamic tail: a write token with an unresolved $VAR whose directory-qualified tail
    (e.g. `hooks/<name>.py`, derived from CORE_IMMUNE, never a bare *.py) matches a core
    tail -> block.

RESIDUAL — NOT airtight (honesty, mirrors write_guard's own scope note): a fully dynamic
no-literal target (`D=harness/hooks; echo x > $D/x.py`) or a base64/encoded construction
slips arm 2 (no protected literal in the raw text). Those are caught by the detective tier
+ git-diff + (shared bin) OS-level read-only, NOT here. This floor is a speed bump +
tamper-EVIDENT record, not an airtight wall.
"""
import fnmatch
import os
import re
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.append(_HERE)
sys.path.append(os.path.join(os.path.dirname(_HERE), "scripts"))

import hook_runtime     # noqa: E402
import trace_log        # noqa: E402
import write_guard      # noqa: E402
import bash_write_guard  # noqa: E402 — reuse shell_write_targets (one parser, N observers)
import deny_matcher     # noqa: E402
import write_deny_policy as wdp  # noqa: E402

HOOK_CLASS = "compliance"
_HOOK = "floor_bash_guard"
_POLICY_REL = "harness/data/write-deny-policy.yaml"

# A shell construct that hides a write from the token parser (wrapper gate).
_WRAPPER_RE = re.compile(
    r"(?:\beval\b|\bxargs\b|\bsh\s+-c\b|\bbash\s+-c\b|\bpython3?\s+-c\b|\$\(|`)")
# A write verb/redirect anywhere in the raw command (write-verb gate). `open(` counts
# ONLY with a write mode ('w'/'a'/'x' in a 2nd arg) — a bare open()/open(...).read() is
# a read and must not trip the WRITE floor.
_WRITE_VERB_RE = re.compile(
    r"(?:>>?|\btee\b|\bsed\s+-i|\bdd\b[^\n]*\bof=|\.write_text|\.write_bytes|"
    r"\.truncate\b|\binstall\b|\bln\s+-s|\bopen\s*\([^,)]*,[^)]*['\"][^'\")]*[waxWAX]|"
    r"\.write\b)")
# A real redirect (NOT counting the ln itself) — the "write after the ln".
_REDIRECT_RE = re.compile(r"(?:>>?|\btee\b|\bsed\s+-i|\bdd\b[^\n]*\bof=)")
# ln -s / ln -sf whose link name lands inside the tests carve.
_LN_INTO_CARVE_RE = re.compile(r"\bln\s+-s\w*\b[^\n;&|]*\bharness/tests/")


def _resolve_roots():
    """(bin_root, [roots]) from agent-uncontrolled sources; ([], []) when the bin
    root cannot be resolved so the caller fails closed."""
    try:
        bin_root = write_guard._bin_root()
    except Exception:  # noqa: BLE001
        return None, []
    if not bin_root or not Path(bin_root).is_dir():
        return None, []
    roots = [Path(bin_root)]
    try:
        proj = write_guard._project_root(bin_root)
    except Exception:  # noqa: BLE001
        proj = None
    if proj and Path(proj).is_dir() and Path(proj).resolve() != Path(bin_root).resolve():
        roots.append(Path(proj))
    return bin_root, roots


def _policy(bin_root):
    try:
        soft = wdp.load_soft_rules(Path(bin_root) / _POLICY_REL)
    except wdp.DenyPolicyError:
        soft = []  # detective config broken: keep the HARD floor, drop soft (never brick)
    return wdp.assemble_policy(soft)


def _dir_tails(core_immune):
    """Directory-qualified tails (last-dir/filename) derived from CORE_IMMUNE — for
    the dynamic-prefix check. Never a bare `*.py` (that would over-block every .py)."""
    tails = set()
    for glob in core_immune:
        segs = glob.split("/")
        if len(segs) >= 2 and not any(c in segs[-2] for c in "*?["):
            tails.add(segs[-2] + "/" + segs[-1])
    return tails


def _wild_index(s):
    for i, ch in enumerate(s):
        if ch in "*?[":
            return i
    return len(s)


def _protected_literals(policy):
    """Specific raw-text needles for the obfuscation-wrapper gate. A needle is a
    wildcard-free literal path, a whole-subtree prefix (`<dir>/**` -> `<dir>/`), a
    multi-segment prefix (`harness/hooks/`), or — for a glob whose only literal prefix
    is a single top-level dir that is otherwise an ALLOW zone (e.g.
    `plans/*/artifacts/plan-approval.json`) — the distinctive tail (`plan-approval.json`).
    NEVER a bare single allow-zone segment like `plans/` (that over-blocks every wrapped
    write under plans/); arm-1's precise resolve still catches those exact targets."""
    lits = set()
    for glob in tuple(policy.core_immune) + tuple(policy.hard_binary_deny):
        if not any(c in glob for c in "*?["):
            if len(glob) >= 4:
                lits.add(glob)  # literal path: .env, docs/decisions.yaml
            continue
        pre = glob[:_wild_index(glob)].rstrip("/")
        if glob in (pre + "/**", pre + "/*"):
            if len(pre) >= 3:
                lits.add(pre + "/")  # whole subtree: harness/, .git/, the cage hooks slot
        elif "/" in pre and len(pre) >= 6:
            lits.add(pre + "/")  # multi-segment prefix: harness/hooks/
        else:
            base = glob.rsplit("/", 1)[-1]
            base = base[:_wild_index(base)]
            if len(base) >= 5:
                lits.add(base)  # distinctive tail: plan-approval.json
    return lits


def _has_protected_literal(command, policy):
    """True when a protected-zone needle appears in the raw command. A directory needle
    (ending `/`) matches as a substring; a file/dotfile needle anchors on a path boundary
    so `.env` matches neither `.environment` nor a `*.env.sample` template."""
    for lit in _protected_literals(policy):
        if lit.endswith("/"):
            if lit in command:
                return True
        elif re.search(r"(?<![\w.])" + re.escape(lit) + r"(?![\w.])", command):
            return True
    return False


def _dynamic_tail_hit(rel, tails):
    if "$" not in rel:
        return False
    return any(fnmatch.fnmatch(rel, "*/" + t) or fnmatch.fnmatch(rel, t) for t in tails)


def _block(data, target, arm, decision=None):
    session = data.get("session_id")
    tier = decision.tier if decision is not None else "hard"
    rule = decision.matched_rule if decision is not None else None
    try:
        trace_log.append_event(
            hook=_HOOK, event=wdp.EVENT_HARD_BLOCK, session=session,
            tool=data.get("tool_name"),
            actor=hook_runtime.resolve_actor(session_id=session),
            status="BLOCKED", note="arm=%s tier=%s rule=%s" % (arm, tier, rule),
            target=str(target).replace("\\", "/"))
    except Exception:  # noqa: BLE001 — the audit write is telemetry; never block on it
        pass
    return ("floor: this Bash command writes into the protected harness floor "
            "('%s', %s) — blocked fail-closed for every actor. Edit the file with an "
            "editor OUTSIDE the agent session; it is git-tracked, so the change stays "
            "reviewable." % (str(target), arm))


def core(data: dict):
    """None ⇒ pass; string ⇒ block reason (run_compliance_hook contract)."""
    tool_input = data.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    command = tool_input.get("command")
    if not isinstance(command, str) or not command.strip():
        return None

    bin_root, roots = _resolve_roots()
    if not roots:
        return ("floor: cannot resolve the harness binary root, so a Bash write into "
                "the protected floor cannot be checked — blocked fail-closed. Set "
                "HARNESS_BIN_ROOT to the harness tree.")

    policy = _policy(bin_root)
    tails = _dir_tails(policy.core_immune)

    # Arm 1 — precise write targets (+ dynamic tail on the same tokens).
    for rel in bash_write_guard.shell_write_targets(command, include_copy_move=True):
        decision = deny_matcher.evaluate(Path(bin_root) / rel, policy, roots)
        if decision.is_hard:
            return _block(data, rel, "precise write-target", decision)
        if _dynamic_tail_hit(rel, tails):
            return _block(data, rel, "dynamic core-tail")

    # ln into the tests carve + a redirect in the same command (TOCTOU).
    if _LN_INTO_CARVE_RE.search(command) and _REDIRECT_RE.search(command):
        return _block(data, "harness/tests/<symlink>", "ln-into-carve TOCTOU")

    # Arm 2 — obfuscation-wrapper triple-coincidence.
    if (_WRAPPER_RE.search(command)
            and _WRITE_VERB_RE.search(command)
            and _has_protected_literal(command, policy)):
        return _block(data, "<wrapped>", "obfuscation-wrapper")

    return None


def main() -> None:
    hook_runtime.compliance_skip_or_run(_HOOK, core, skip_event="floor_bash_skip")


if __name__ == "__main__":
    main()
