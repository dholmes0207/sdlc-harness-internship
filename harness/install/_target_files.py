#!/usr/bin/env python3
"""_target_files.py — writes into the target repo's OWN files (extracted from
install.py): the managed .gitignore block, the CLAUDE.md onboarding block, and
the standards presence check. install.py
re-exports these names, so callers and tests that reach them through the
`install` module see no change.
"""
import json
import os

def _standards_maxloc() -> int:
    """Advisory line budget for a standards doc (tunable). Default mirrors the
    repo's docs footprint limit."""
    raw = os.environ.get("HARNESS_STANDARDS_MAXLOC", "").strip()
    try:
        return int(raw) if raw else 800
    except ValueError:
        return 800


_GITIGNORE_BEGIN = "# >>> harness (generated runtime — never commit) >>>"
_GITIGNORE_END = "# <<< harness <<<"
_GITIGNORE_PATTERNS = (
    "harness/state/",
    "harness/standards/.snapshots/",
    "harness/e2e/RUN-LOG.md",
    # settings.json is install-materialized and mode-specific (global wires
    # $HARNESS_BIN_ROOT, project wires $CLAUDE_PROJECT_DIR). Committing it freezes
    # wrong-mode/stale hook wiring into git history — a later `git checkout` then
    # restores dead per-script paths and bricks every hook. Never commit it.
    ".claude/settings.json",
    ".claude/settings.local.json",
)


def _write_gitignore(target_root, result, dry_run, no_track=False):
    """Ensure the target's .gitignore carries a managed harness block so the
    runtime state the harness writes never lands in the deployer's git. User lines
    OUTSIDE the markers are always preserved, and a no-change rewrite is skipped
    (idempotent). Like the CLAUDE.md block, this REPLACES between the markers so a
    version bump (a new ignore pattern such as `.claude/settings.json`) reaches an
    adopter who installed before it — a skip-if-present block would freeze the
    stale set forever. With ``no_track``, ALSO ignore the whole harness/ tree — the
    harness runs but is never committed into the adopter's product git (re-track by
    dropping --no-track and removing the line)."""
    path = target_root / ".gitignore"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = "\n".join([_GITIGNORE_BEGIN, *_GITIGNORE_PATTERNS, _GITIGNORE_END])
    b = existing.find(_GITIGNORE_BEGIN)
    e = existing.find(_GITIGNORE_END, b) if b != -1 else -1
    if b != -1 and e != -1:
        # well-formed marker pair → replace between them (refresh a stale block)
        new = existing[:b] + block + existing[e + len(_GITIGNORE_END):]
        if new != existing:
            result["actions"].append("refresh harness block in .gitignore")
    else:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        new = existing + sep + block + "\n"
        result["actions"].append("add harness block to .gitignore")
    if no_track and "harness/" not in new.splitlines():
        sep = "" if not new or new.endswith("\n") else "\n"
        new = new + sep + "harness/\n"
        result["actions"].append(
            "gitignore harness/ (--no-track: harness present but not committed)")
    if new != existing and not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")


_CLAUDE_BEGIN = "<!-- >>> harness onboarding (generated; edits between markers are overwritten on reinstall) >>> -->"
_CLAUDE_END = "<!-- <<< harness <<< -->"


def _claude_md_block() -> str:
    """The self-loading onboarding block: what the harness is, how to drive it,
    where the shared rules live. Kept short — it is a pointer, not a manual."""
    return "\n".join([
        _CLAUDE_BEGIN,
        "",
        "## SDLC harness",
        "",
        "This repo runs a file-based **SDLC harness** for Claude Code, vendored "
        "self-contained under `harness/`.",
        "",
        "- **Probe before you build on a guess** (the load-bearing habit) — when a "
        "load-bearing assumption CAN be checked empirically (spike a thin slice, run "
        "the real tool once, read the source/docs, web-search), do that FIRST, before "
        "you design or build on top of it. A real check is cheaper and firmer than "
        "predicting-then-building, or verifying in circles without ever running the "
        "thing. A claim you have not exercised for real is `[ASSUMED]` (training knowledge "
        "you have not re-checked is `[PRIOR]`), never OBSERVED: label it with its honest type "
        "and gate it behind one real-run step — never report \"works\" from reasoning alone.",
        "- **Skills** — drive the workflow with `/hs:<name>` (e.g. `/hs:plan`, "
        "`/hs:cook`, `/hs:test`, `/hs:ship`, `/hs:review-pr`). `/hs:find-skills` "
        "lists the full catalog.",
        "- **Off skills** — a fresh install ships DEFAULT-OFF: only a recommended "
        "subset loads; the rest are stashed under "
        "`harness/plugins/hs/disabled-skills/<name>/` (present in the bundle, not "
        "deleted). `/hs:find-skills --list` shows them tagged `[OFF]`; "
        "`python3 harness/scripts/disabled_skills.py --status <name>` reports "
        "live|disabled|unknown. Run an off skill with `/hs:use <name>` (not the raw "
        "`/hs:<name>`); enable one for every session with "
        "`hs-cli skills --on <name>` (restart to apply). An off reference is a "
        "normal state, not a broken link — do not go hunting for a 'missing' skill.",
        "- **Rules** — shared conventions load on demand from `harness/rules/` "
        "(routing in this file's project section, or ask a skill).",
        "- **Hooks** — gates/telemetry are wired in `.claude/settings.json`; "
        "config knobs live in `harness/data/*.yaml` and `harness/hooks/*.yaml`. "
        "Run `/hs:setup` to configure (voice, guard/stage policy, output language).",
        "- **State** — runtime telemetry/state is written under `harness/state/` "
        "(gitignored; never commit it).",
        "",
        _CLAUDE_END,
    ])


def _strip_stale_claude_blocks(text: str) -> str:
    """Remove every well-formed onboarding block but the LAST, leaving a single
    BEGIN/END pair for the caller to replace. Defends against a prior bug that
    appended a second block instead of replacing the first: each extra leading
    block (BEGIN ... END) is excised so no orphan marker survives the rewrite."""
    while text.count(_CLAUDE_BEGIN) > 1:
        b = text.find(_CLAUDE_BEGIN)
        e = text.find(_CLAUDE_END, b)
        if e == -1:
            break  # an unpaired leading BEGIN — leave it for the caller
        # drop the block and any blank padding that immediately followed it
        rest = text[e + len(_CLAUDE_END):]
        rest = rest[1:] if rest.startswith("\n") else rest
        text = text[:b] + rest
    return text


def _write_claude_md(target_root, result, dry_run):
    """Inject the onboarding block into the target's CLAUDE.md. Unlike the
    .gitignore block (skip-if-present), this REPLACES between markers so a
    version bump refreshes a stale block; prose OUTSIDE the markers is always
    preserved, and a no-change rewrite is skipped (idempotent)."""
    path = target_root / "CLAUDE.md"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    block = _claude_md_block()
    # rfind (last BEGIN) + first END after it: replacing the LAST pair drains a
    # duplicate leading block left by a prior bug — find() would keep the first
    # pair and orphan the rest. Validate there is exactly one BEGIN; if there are
    # several, strip the stale leading blocks first so the rewrite ends with a
    # single clean marked block (no orphan marker survives).
    if existing.count(_CLAUDE_BEGIN) > 1:
        existing = _strip_stale_claude_blocks(existing)
    b = existing.rfind(_CLAUDE_BEGIN)
    e = existing.find(_CLAUDE_END, b) if b != -1 else -1
    if b != -1 and e != -1:
        # well-formed marker pair → replace between them
        new = existing[:b] + block + existing[e + len(_CLAUDE_END):]
    else:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        lead = "\n" if existing else ""
        new = existing + sep + lead + block + "\n"
    if new == existing:
        return  # already current — nothing to do (idempotent)
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    result["actions"].append("write harness onboarding block to CLAUDE.md")


_WTI_BEGIN = "# >>> harness worktree carry (generated — edits between markers are overwritten) >>>"
_WTI_END = "# <<< harness <<<"


def _write_worktreeinclude(target_root, source_root, result, dry_run):
    """Materialize the target's `.worktreeinclude` from the shipped template so
    Claude Code copies the harness runtime subtree + `.claude/settings.json` into
    every worktree it creates (closes the F1 partial-carry hard-block). Managed
    between markers — replaces a stale block so a version bump (a new harness
    subdir) reaches an adopter who installed earlier, while preserving any user
    lines outside the markers. Idempotent (a no-change rewrite is skipped).
    Per-project only — the caller gates on mode != global and source != target."""
    template = source_root / "harness" / "install" / "worktreeinclude.template"
    try:
        lines = [l.rstrip("\n") for l in
                 template.read_text(encoding="utf-8").splitlines() if l.strip()]
    except OSError:
        return  # no template shipped — nothing to materialize (fail-open)
    block = "\n".join([_WTI_BEGIN, *lines, _WTI_END])
    path = target_root / ".worktreeinclude"
    existing = path.read_text(encoding="utf-8") if path.is_file() else ""
    b = existing.find(_WTI_BEGIN)
    e = existing.find(_WTI_END, b) if b != -1 else -1
    if b != -1 and e != -1:
        new = existing[:b] + block + existing[e + len(_WTI_END):]
    else:
        sep = "" if not existing or existing.endswith("\n") else "\n"
        new = existing + sep + block + "\n"
    if new == existing:
        return  # already current — idempotent
    if not dry_run:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(new, encoding="utf-8")
    result["actions"].append("write .worktreeinclude (worktree carry of harness/)")


def _check_standards(target_root, result):
    base = target_root / "docs"
    maxloc = _standards_maxloc()
    for name in ("code-standards.md", "system-architecture.md"):
        p = base / name
        if not p.is_file() or len(p.read_text(encoding="utf-8").strip()) < 40:
            result["warnings"].append(
                "docs/%s missing or thin — run /hs:docs to author it (or "
                "harness/scripts/scaffold_standards.py --type %s for a TBD "
                "skeleton, or copy your org's into docs/) before relying on "
                "standards-aware skills; the installer never fabricates them"
                % (name, name[:-3]))
            continue
        loc = p.read_text(encoding="utf-8").count("\n") + 1
        if loc > maxloc:
            result["warnings"].append(
                "docs/%s is %d lines (> %d) — many skills load it, so a "
                "long file costs tokens and is easy to skim past; consider "
                "trimming or splitting it (advisory, set HARNESS_STANDARDS_MAXLOC "
                "to tune)" % (name, loc, maxloc))
