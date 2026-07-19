#!/usr/bin/env python3
"""write_guard.py — tool-mediated config-edit gate (compliance, fail-closed).

PreToolUse(Write|Edit|MultiEdit|NotebookEdit): block when the target is one of
the files the gate posture depends on. Under a GLOBAL install one shared binary
serves many projects, so the gate resolves TWO roots and guards TWO zones:

  bin zone (read-only, shared)   — the ENTIRE bin_root() tree. When bin≠project a
      tool-Write to ANY path under the bin is blocked for every actor: the bin
      holds .claude/settings.json (the guard on/off switch), CLAUDE.md,
      scripts/, orchestrator/ — a harness/-only fence would let a foreign project
      overwrite the hook registration and disable ALL guards (red-team F1).
  project zone (writeable, per-project) — the project-lane GUARD_LIST patterns
      (docs/decisions.*, plans/*/artifacts/plan-approval.*) matched relative to
      the PROJECT root so they stay guarded even when the binary lives elsewhere
      (C3).

Self-host / dogfood (bin==project) collapses to today's single-root behavior:
detected by HARNESS_BIN_ROOT being UNSET (a global bin is itself a git checkout,
so a `.git` walk-up would wrongly collapse it — red-team F2). Under a global
layout with an unresolved project root the gate FAILS CLOSED (blocks a guarded
project-tail write, never a silent allow — C5/F2). Containment is case-insensitive
(F6) so a case-variant bin path cannot dodge it on macOS/Windows.

GUARD_LIST is a CODE CONSTANT: config can only ADD paths (write-guard.yaml
`extra_guarded`), never remove one — a config knob that can shrink the guard is a
guard that does not exist.

HONESTY — the name is the scope: this is a tool-mediated CONFIG-EDIT gate. It sees
Write/Edit/MultiEdit/NotebookEdit tool calls and nothing else. It does NOT see a
Bash redirect (`echo > file`), a `cp`/`sed -i` into the bin, or an editor outside
the session — those paths are the documented tamper-EVIDENT floor (git diff,
manifest verify, pre-push scrub). For a SHARED bin the git-diff floor evaporates
(the diff lands in the bin's repo, which the foreign operator never reviews), so
true shared-binary integrity requires the bin be OS-level read-only to the runtime
user — that is the real fence, documented in the phase-7 install doc, NOT a claim
this gate makes.

Enable-path autonomy: the guard resolves its enabled flag ONLY from
write-guard.yaml in the bin tree's harness/data/ (tracked in git) — never
HARNESS_HOOK_CONFIG, so one env var cannot switch it off. Fail-closed is local:
this hook reimplements the run_compliance_hook exception→exit-2 discipline rather
than reusing it (that wrapper carries an env-driven enable path).
"""

import fnmatch
import json
import os
import re
import sys
from pathlib import Path

_HOOKS_DIR = os.path.dirname(os.path.abspath(__file__))
if _HOOKS_DIR not in sys.path:
    sys.path.append(_HOOKS_DIR)
# harness_paths lives in the sibling scripts/ dir; add it so bin/data root
# resolution rides the single shared resolver. Imported lazily in the resolvers
# so a missing sibling can never brick the module at import time (a bricked
# compliance hook would fail-closed and reject writes indiscriminately).
_SCRIPTS_DIR = os.path.join(_HOOKS_DIR, "..", "scripts")
if _SCRIPTS_DIR not in sys.path:
    sys.path.append(_SCRIPTS_DIR)

HOOK_CLASS = "compliance"

_GUARDED_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# --- H4: MCP blind spot -------------------------------------------------------
# A `mcp__<server>__<method>` tool call is invisible to the native
# Write|Edit|MultiEdit|NotebookEdit matcher above — an MCP server exposing a
# write-capable method (e.g. a devtool's `write`/`insert`/`update` query) could
# edit a guarded path with NO gate in the way (INV-3 F-8c, DECISIONS.md H4).
# Threat-modeled, not a blanket new gate: this hook polices WRITE TARGETS, so a
# read-shaped method (query/get/list/search/describe/read/fetch/select — e.g. a
# devtool `query` in its read-only Safe Mode) is a documented non-issue and
# passes untouched; only a write-shaped method is gated on its extractable
# target path, or fails CLOSED when no target can be found at all (an MCP write
# this gate cannot see the destination of is never silently allowed).
_MCP_WRITE_VERB_RE = re.compile(
    r"(write|insert|update|delete|remove|create|exec|execute|mutate|patch|"
    r"put|upsert|apply|migrate|drop|truncate|alter|modify)", re.IGNORECASE)
_MCP_PATH_KEYS = ("file_path", "notebook_path", "path", "filepath", "file",
                  "target", "target_path", "output_path", "dest",
                  "destination", "filename")


def is_mcp_tool(tool_name) -> bool:
    return isinstance(tool_name, str) and tool_name.startswith("mcp__")


def mcp_write_shaped(tool_name: str) -> bool:
    """True when the MCP tool's method segment (the part after the LAST `__` in
    `mcp__<server>__<method>`) looks like a write verb. A method with no
    recognizable verb is treated conservatively as NOT write-shaped (read-only-
    safe) — this deliberately favors the documented-non-issue path over an
    unbounded new gate; a server that names its write method something this
    regex misses is a documented gap (mirrors the rest of this file's honesty
    notes), not a silent guarantee."""
    method = tool_name.rsplit("__", 1)[-1] if "__" in tool_name else tool_name
    return bool(_MCP_WRITE_VERB_RE.search(method))


def mcp_candidate_path(tool_input):
    """The first plausible write-target path in an MCP tool_input dict, else
    None. MCP server schemas vary server-to-server; this is a best-effort scan
    over common key names, checked in a fixed, predictable order."""
    if not isinstance(tool_input, dict):
        return None
    for key in _MCP_PATH_KEYS:
        val = tool_input.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return None

# Repo-root-relative globs (fnmatch). Constant by design. The full guarded set
# (both zones) — kept as one tuple so bash_write_guard reuses the identical set
# (one guarded set, two observers). The two lanes below partition it by root.
GUARD_LIST = (
    # decisions SSOT: the register CLI (atomic_write via Bash) is the ONLY
    # sanctioned write path; a direct agent tool-edit would bypass the
    # cross-scope confirm gate, so the SSOT + its rendered view are guarded.
    "docs/decisions.yaml",
    "docs/decisions.md",
    "harness/hooks/*.py",
    "harness/data/harness-hooks.yaml",
    "harness/data/write-guard.yaml",
    "harness/data/stage-policy.yaml",
    "harness/data/simplify-policy.yaml",
    "harness/data/ownership.yaml",
    "harness/data/agent-permissions.yaml",
    "harness/scripts/agent_permissions.py",
    # the write deny-list cage: its policy modules, the soft config, and the schema —
    # an agent must not weaken the floor it is held to (the cage self-protection set).
    "harness/scripts/write_deny_policy.py",
    "harness/scripts/deny_matcher.py",
    "harness/scripts/deny_audit.py",
    "harness/data/write-deny-policy.yaml",
    "harness/schemas/write-deny-policy.json",
    "harness/data/task-store.yaml",
    "harness/scripts/artifact_check.py",
    "harness/scripts/stage_detector.py",
    "harness/scripts/fs_guard.py",
    "harness/scripts/claims.py",
    "harness/scripts/component_config.py",
    "harness/data/components.yaml",
    "harness/data/component-policy.yaml",
    "harness/scripts/plan_approval.py",
    "harness/scripts/task_store.py",
    "harness/scripts/task_store_http.py",
    "harness/scripts/task_store_github.py",
    "harness/scripts/task_store_gitlab.py",
    "harness/scripts/output_config.py",
    "harness/scripts/register_block.py",
    "harness/scripts/voice_prefs.py",
    "harness/install/git-pre-push-hook.sh",
    "harness/install/hooks-registration.yaml",
    "plans/*/artifacts/plan-approval.json",
    "plans/*/artifacts/plan-approval.yaml",
)

# Project-lane: matched relative to the PROJECT root (docs/plans are per-project).
_PROJECT_LANE = (
    "docs/decisions.yaml",
    "docs/decisions.md",
    "plans/*/artifacts/plan-approval.json",
    "plans/*/artifacts/plan-approval.yaml",
)
# Bin-lane: everything else — the shared binary's own gate files, matched
# relative to the bin root. Derived from GUARD_LIST so the two lanes can never
# drift from the constant.
_BIN_LANE = tuple(p for p in GUARD_LIST if p not in _PROJECT_LANE)

_SWITCH_NAME = "write-guard.yaml"


def _bin_root() -> Path:
    """The shared-binary root: HARNESS_BIN_ROOT > HARNESS_ROOT > __file__ >
    walk-up. Delegates to the shared resolver; falls back to a local self-resolve
    if the sibling import is unavailable so the gate never bricks."""
    try:
        import harness_paths
        return harness_paths.bin_root()
    except Exception:
        raw = os.environ.get("HARNESS_BIN_ROOT") or os.environ.get("HARNESS_ROOT")
        if raw:
            return Path(raw).resolve()
        return Path(__file__).resolve().parent.parent.parent


def _root() -> Path:
    """Back-compat alias for _bin_root() — bash_write_guard + the switch loader
    resolve the shared binary through here."""
    return _bin_root()


def _self_host() -> bool:
    """Self-host / dogfood is detected by HARNESS_BIN_ROOT being UNSET. A global
    bin is itself a git checkout, so this must NOT key off a `.git` walk-up (F2):
    that would collapse a global layout with an unset project dir and unlock the
    shared bin."""
    return not os.environ.get("HARNESS_BIN_ROOT")


def _project_root(bin_root: Path):
    """The project root the project-lane patterns anchor to. Under self-host the
    bin IS the project (ignore a stray CLAUDE_PROJECT_DIR — in a self-hosted
    checkout the two are the same tree). Under a global layout it is the external
    project; None (fail-closed) when it cannot be resolved."""
    if _self_host():
        return bin_root
    proj = os.environ.get("CLAUDE_PROJECT_DIR")
    if proj:
        return Path(proj).resolve()
    try:
        import harness_paths
        dr = harness_paths.data_root()
        if harness_paths.data_root_unresolved(dr):
            return None
        return dr.parent
    except Exception:
        return None


def _switch_config() -> dict:
    """Parse the write-guard switch. Default source: write-guard.yaml in the bin
    tree's harness/data/ (the tracked break-glass file). A repo owner MAY point
    the DEDICATED HARNESS_WRITE_GUARD_CONFIG env at an override file (e.g. under
    .harness-dev/) to disarm the tool-cage in a single-owner dev repo; the
    override wins when set + readable.

    Two deliberate scope choices: (1) the BROAD HARNESS_HOOK_CONFIG still does
    NOT reach this gate — only this single-purpose var does, so a general config
    can never silently disarm the cage. (2) SECURITY TRADEOFF: the override lives
    in a writeable zone, re-exposing the F3 self-disarm surface the tracked-file
    design closes — intended ONLY for a single-owner dev sandbox, and scrubbed at
    push (HARNESS_* env never ships), so ship/downstream always fall back to the
    tracked write-guard.yaml (enabled: true). Malformed / missing override →
    fall through to the tracked file; a broken switch never opens the gate."""
    override = os.environ.get("HARNESS_WRITE_GUARD_CONFIG")
    if override:
        op = Path(override)
        if op.is_file():
            try:
                import yaml
                raw = yaml.safe_load(op.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    return raw
            except Exception:
                pass  # unparsable override → fall through to the tracked switch
    p = _bin_root() / "harness" / "data" / _SWITCH_NAME
    if not p.is_file():
        return {}
    try:
        import yaml
        raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}  # unparsable switch = guard stays on


def _extra_guarded(cfg) -> tuple:
    extra = cfg.get("extra_guarded")
    if isinstance(extra, list):
        return tuple(str(x) for x in extra if isinstance(x, str) and x.strip())
    return ()


def _rel_target(file_path, root: Path):
    """Target as a root-relative POSIX path, resolved (`..` collapsed, symlinks
    followed) so traversal cannot dodge the match. None when the target lies
    outside `root` entirely."""
    target = Path(file_path)
    if not target.is_absolute():
        target = root / target
    resolved = target.resolve(strict=False)
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


def _match(rel, patterns):
    """First guarded pattern matching `rel`, case-insensitively (on a
    case-insensitive FS `harness/HOOKS/gate_stage.py` opens the REAL guarded
    file), else None."""
    if rel is None:
        return None
    low = rel.lower()
    return next((pat for pat in patterns
                 if fnmatch.fnmatch(low, pat.lower())), None)


def _match_tail(file_path, patterns):
    """First pattern matching the TAIL of an absolute path — the fail-closed
    fallback when the project root is unresolved under a global bin: we cannot
    anchor the project-lane patterns, so a path whose tail looks like a guarded
    SSOT is blocked rather than silently allowed."""
    p = str(Path(file_path)).replace(os.sep, "/").lower()
    for pat in patterns:
        low = pat.lower()
        if fnmatch.fnmatch(p, "*/" + low) or fnmatch.fnmatch(p, low):
            return pat
    return None


def _under(file_path, root: Path) -> bool:
    """True when the resolved target lies inside `root` (the whole-bin catch-all).
    Case-insensitive realpath containment (F6) so a case-variant path cannot dodge
    it on a case-insensitive FS. On a resolution error this catch-all BLOCKS
    (returns True): it exists precisely for bin paths the lane checks did not
    match, so failing open here would let an unresolvable bin-zone write slip."""
    try:
        target = Path(file_path)
        if not target.is_absolute():
            target = root / target
        rt = str(target.resolve(strict=False)).lower()
        rr = str(root.resolve()).lower()
        return rt == rr or rt.startswith(rr.rstrip(os.sep) + os.sep)
    except Exception:
        return True


def check(data) -> "str | None":
    """None = allow; string = block reason (the compliance core contract)."""
    tool = data.get("tool_name")
    tool_input = data.get("tool_input") or {}
    mcp_unresolvable = False
    if tool in _GUARDED_TOOLS:
        # NotebookEdit carries notebook_path, not file_path (F4) — read both so a
        # .ipynb write to the bin cannot slip past the catch-all.
        file_path = tool_input.get("file_path") or tool_input.get("notebook_path")
        if not file_path:
            return None
    elif is_mcp_tool(tool):
        # H4: gate a write-shaped MCP call the same way; a read-shaped one
        # (query/get/list/...) is out of scope for this path-containment gate.
        if not mcp_write_shaped(tool):
            return None
        file_path = mcp_candidate_path(tool_input)
        mcp_unresolvable = file_path is None
    else:
        return None

    cfg = _switch_config()
    disabled = cfg.get("enabled") is False

    if mcp_unresolvable:
        return _decide_mcp_unresolvable(disabled, tool, data)
    extra = _extra_guarded(cfg)
    bin_root = _bin_root()
    proj_root = _project_root(bin_root)
    collapse = _self_host()  # global layout never collapses, even if realpath-equal (F7)

    # 1. Bin-lane: the shared binary's own gate files, resolved rel to bin_root.
    #    Fires in BOTH modes (they name the bin's files either way).
    rel_bin = _rel_target(file_path, bin_root)
    hit = _match(rel_bin, _BIN_LANE + extra)
    if hit:
        return _decide(disabled, rel_bin, hit, data)

    # 2. Project-lane: docs/plan-approval, resolved rel to the project root.
    if proj_root is not None:
        rel_proj = _rel_target(file_path, proj_root)
        hit = _match(rel_proj, _PROJECT_LANE + extra)
        if hit:
            return _decide(disabled, rel_proj, hit, data)
    elif os.environ.get("HARNESS_BIN_ROOT"):
        # C5/F2 fail-closed: global layout, project unresolved → cannot anchor the
        # project lane. Block a guarded-SSOT-tail write rather than allow it.
        hit = _match_tail(file_path, _PROJECT_LANE + extra)
        if hit:
            return _decide(disabled, str(file_path), hit, data,
                           note="project root unresolved under global bin")

    # 3. Bin-zone whole-bin catch-all (F1): when bin≠project, any tool-Write under
    #    the shared bin is blocked for every actor. Skipped on self-host collapse
    #    so the dev can edit its own tree.
    if not collapse and _under(file_path, bin_root):
        return _decide(disabled, str(file_path),
                       "${bin}/** (shared-binary read-only zone)", data)

    return None


def _decide(disabled, target, hit, data, note=None):
    """Emit the trace + reason for a matched guard, honoring the tracked
    break-glass (write-guard.yaml enabled:false)."""
    if disabled:
        _trace("gate_skip", target, data,
               note="write_guard disabled via %s (tracked break-glass; the flip "
                    "is a git diff)" % _SWITCH_NAME)
        return None
    _trace("gate_block", target, data,
           note=("matched %s" % hit) + (" — %s" % note if note else ""))
    return (
        "%s is gate config (matched %r) — agent tools may not edit it. "
        "If the change is intended, make it with a normal editor outside "
        "the agent session: the file is tracked, the diff stays visible. "
        "Artifacts like plan-approval.json are written via their CLI "
        "(plan_approval.py) only." % (target, hit)
    )


def _decide_mcp_unresolvable(disabled, tool_name, data) -> "str | None":
    """H4 fail-closed arm: an MCP tool_name matched a write verb but no
    candidate path field was found in tool_input — the write target is
    unknowable, so this blocks rather than silently letting an MCP write past
    every path-containment check (honoring the same tracked break-glass as
    every other guard_list hit)."""
    if disabled:
        _trace("gate_skip", tool_name, data,
               note="write_guard disabled via %s (tracked break-glass; the flip "
                    "is a git diff)" % _SWITCH_NAME)
        return None
    _trace("gate_block", tool_name, data,
           note="MCP tool matched a write verb but no path field was found "
                "among %s — target unknowable" % ", ".join(_MCP_PATH_KEYS))
    return (
        "%s looks write-shaped (matched a write verb in its method name) but "
        "no recognizable path field was found in its tool_input (checked: %s) "
        "— the write target is unknowable, so this is blocked fail-closed. If "
        "this MCP tool is read-only-safe, name its verb outside the guard's "
        "write-verb pattern, or add its path field name to _MCP_PATH_KEYS."
        % (tool_name, ", ".join(_MCP_PATH_KEYS))
    )


def _trace(event, target, data, note=None) -> None:
    try:
        import trace_log
        trace_log.append_event("write_guard", event,
                               session=data.get("session_id"),
                               tool=data.get("tool_name"), target=target,
                               note=note)
    except Exception:
        pass  # tracing must not break the gate decision


def main() -> None:
    """Fail-closed shell (the run_compliance_hook discipline, minus its
    env-driven enable path): every internal error blocks with exit 2."""
    try:
        try:
            data = json.loads(sys.stdin.read() or "{}")
        except ValueError:
            data = {}
        if not isinstance(data, dict):
            data = {}

        reason = check(data)
        if reason:
            sys.stderr.write("[write_guard] BLOCKED: %s\n" % reason)
            sys.exit(2)
        try:
            sys.stdout.write(json.dumps({"continue": True}))
            sys.stdout.flush()
        except Exception:
            pass
        sys.exit(0)
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — a broken gate must gate
        try:
            import hook_runtime
            hook_runtime.log_hook_error("write_guard", e)
        except Exception:
            pass
        sys.stderr.write(
            "[write_guard] BLOCKED: gate crashed (%s: %s). Fail-closed by "
            "policy. Emergency off-switch: set `enabled: false` in "
            "harness/data/write-guard.yaml with an editor OUTSIDE the "
            "agent session (tracked file, diff visible).\n"
            % (type(e).__name__, e))
        sys.exit(2)


if __name__ == "__main__":
    main()
