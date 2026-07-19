#!/usr/bin/env python3
"""verify_install.py — compare files on disk against harness/manifest.json.

Fails LOUD per asset (R8 install drift): every drifted/missing file is named
individually — never a bare "mismatch". --strict exits non-zero on any drift;
without it the report still prints but exit is 0 (inspection mode).

Usage:
    python3 harness/scripts/verify_install.py [--root <repo-root>] [--strict]
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

# Same hashing as the builder — single source for the digest algorithm.
sys.path.append(str(Path(__file__).resolve().parent))
from build_manifest import MANIFEST_REL, sha256_file  # noqa: E402

# A hook command line names its script as harness/hooks/<name>.py. Parsed by
# pattern (not YAML) so verify stays dependency-free; hooks-registration.yaml is
# the installer's input — the one place a hook gets wired to a Claude Code event.
_HOOK_CMD_RE = re.compile(r"harness/hooks/([A-Za-z0-9_]+\.py)(?![A-Za-z0-9_])")


# The harness/data/*.yaml files a deploying team is EXPECTED to tune per target —
# the documented gate-input knobs (docs/harness/config-reference.md). An EXPLICIT allowlist, not
# a whole-directory glob: everything else under harness/data/ is harness-BEHAVIOR
# data (skill-deps = the skill->dep graph + 13-skill core-immutable set, the
# components install map, decomposition/route/observation tables) that stays
# integrity-LOCKED by the manifest — a drift there is corruption/tamper, not
# customization, and must fail --strict.
_LOCALIZED_DATA = frozenset((
    "agent-permissions.yaml", "component-policy.yaml", "cook.yaml", "critique.yaml",
    "guard-policy.yaml", "output.yaml", "ownership.yaml", "protected-branches.yaml",
    "skill-chains.yaml", "stage-policy.yaml",
    "terminal-voice.yaml",
))


def is_localized(rel: str) -> bool:
    """True for files a deploying team is EXPECTED to edit per target: the
    documented gate-input knobs under harness/data/ (reviewer roster, stage/guard
    policy, protected branches, voice knobs, output language, RBAC lanes — the
    _LOCALIZED_DATA allowlist) and the hook registration. They ship in the manifest
    as a baseline, but post-install divergence is customization, not integrity
    drift — gate config is tamper-visible via git, not integrity-locked by the
    manifest. Single source of truth shared with the installer's final verify (DRY).
    Code under harness/hooks, scripts, rules, skills AND harness-behavior data
    (skill-deps, components, decomposition-map, …) is NOT localized: it still fails
    --strict on any mismatch.

    harness/data/harness-hooks.yaml is localized too: it is the per-deployment
    hook enable/mode override file (the component projector writes enabled flags
    into it, and a deployer may flip a gate in an emergency). Like the other
    gate configs it is tamper-visible via git + the gate_skip trace, not
    integrity-locked by the manifest."""
    if rel in ("harness/install/hooks-registration.yaml",
               "harness/data/harness-hooks.yaml"):
        return True
    prefix = "harness/data/"
    if rel.startswith(prefix) and "/" not in rel[len(prefix):]:
        return rel[len(prefix):] in _LOCALIZED_DATA
    return False


def split_localized(problems: list) -> tuple:
    """Partition (rel, problem) tuples into (hard_drift, localized) by
    is_localized. The installer and the CLI both classify through this — one
    rule, two callers."""
    hard, localized = [], []
    for rel, prob in problems:
        (localized if is_localized(rel) else hard).append((rel, prob))
    return hard, localized


def _omitted_skill_prefixes(root: Path) -> tuple:
    """Skill-dir path prefixes the install deliberately omitted (the dir-omit
    disable for the collapsed single-hs plugin). Files under these prefixes are
    absent BY DESIGN, so the verify loop excludes them rather than reading their
    absence as drift. Sourced from the install-recorded list under harness/state/
    (machine-written, gitignored, never in the manifest). Missing or unreadable
    -> no omits, i.e. strict as before — a broken record never silently hides a
    real missing file."""
    import omit_record
    return omit_record.skill_dir_prefixes(omit_record.read_omitted(root))


def verify(root: Path) -> list:
    """Return list of (relpath, problem) tuples; empty = clean."""
    manifest_path = root / MANIFEST_REL
    if not manifest_path.is_file():
        return [(MANIFEST_REL, "manifest missing — run build_manifest.py")]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as e:
        # A corrupt/truncated manifest (partial write, interrupted build, bad copy)
        # is named as one hard drift — never an uncaught traceback. Mirrors the
        # sibling loaders (orphan_problems, component_file_problems).
        return [(MANIFEST_REL, "unreadable: %s" % e)]
    omit_prefixes = _omitted_skill_prefixes(root)
    problems = []
    for rel, expected in sorted(manifest.get("files", {}).items()):
        if omit_prefixes and rel.startswith(omit_prefixes):
            continue  # deliberately-omitted skill dir — absence is by design
        p = root / rel
        if not p.is_file():
            problems.append((rel, "missing"))
        elif sha256_file(p) != expected:
            problems.append((rel, "hash mismatch"))
    return problems


def orphan_problems(root: Path) -> list:
    """Files present on disk under harness/ but ABSENT from the manifest — the
    inverse of verify() (which only checks manifest-listed files are present and
    hash-match). A reinstall/upgrade that drops a script never removes the stale
    copy, so the orphan lingers and can shadow the live tree. NAMED per file.

    WARN-class by decision: an orphan is customization or stale-but-harmless, not
    integrity drift — it never fails --strict by itself (the installer's --prune
    removes them on demand). Returns [] when the manifest is absent (not an
    installable tree); harness/state/ is excluded (runtime-written, gitignored,
    never manifest-tracked)."""
    manifest_path = root / MANIFEST_REL
    harness_dir = root / "harness"
    if not manifest_path.is_file() or not harness_dir.is_dir():
        return []
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — verify() reports the unreadable manifest
        return []
    tracked = set(manifest.get("files", {}))
    tracked.add(MANIFEST_REL)  # excluded from its own hash map but IS shipped
    tracked.add("harness/release.json")  # shipped, excluded from the hash map
    problems = []
    for p in sorted(harness_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        # harness/state/ is runtime-written and gitignored — never manifest-
        # tracked, so it is not an orphan.
        if rel.startswith("harness/state/"):
            continue
        # harness/plugins/hs/disabled-skills/ is the tracked off-skill stash, but a
        # skill DISABLED at runtime is moved here and its files are not in the shipped
        # manifest — like state/, a runtime-mutable location. Excluding it keeps the
        # parked skill from reading as an orphan (and being deleted by --prune); a
        # ship-time-disabled skill is manifest-tracked and passes as `rel in tracked`.
        if rel.startswith("harness/plugins/hs/disabled-skills/"):
            continue
        # Compiler-generated / runtime-written paths — gitignored and never
        # manifest-tracked, so noise rather than orphans: bytecode cache, hook
        # crash logs, e2e run log.
        if ("__pycache__/" in rel or rel.endswith((".pyc", ".pyo"))
                or "/.logs/" in rel or rel.endswith("/RUN-LOG.md")):
            continue
        if rel not in tracked:
            problems.append((rel, "present on disk but not in the manifest "
                             "(orphan — a prior install left it; --prune removes)"))
    return problems


def prepush_copy_warnings(root: Path) -> list:
    """The ACTIVE pre-push hook lives at .git/hooks/pre-push, outside the
    manifest's reach — the manifest only hashes harness/. This cheap check
    compares the installed copy against its source and NAMES a difference.
    Warn-only by decision: a repo that never installed the git hook (or has
    no .git dir at all) must not fail verification over it."""
    src = root / "harness" / "install" / "git-pre-push-hook.sh"
    installed = root / ".git" / "hooks" / "pre-push"
    if not src.is_file() or not (root / ".git").is_dir():
        return []
    if not installed.is_file():
        return [(".git/hooks/pre-push",
                 "not installed (transport gate inactive — the installer "
                 "copies harness/install/git-pre-push-hook.sh there)")]
    if sha256_file(installed) != sha256_file(src):
        return [(".git/hooks/pre-push",
                 "installed copy differs from harness/install/"
                 "git-pre-push-hook.sh — reinstall or diff the two")]
    return []


def worktreeinclude_problems(root, mode="project") -> list:
    """Per-project presence of `.worktreeinclude`. The installer materializes it
    so Claude Code carries the harness runtime subtree + `.claude/settings.json`
    into every worktree it creates (closes F1). Under a GLOBAL install there is
    no per-project harness/ tree to carry — one shared bin serves every worktree —
    so its absence is NOT a defect (R8). A per-project install that lacks it would
    silently reintroduce the partial-carry hard-block."""
    from pathlib import Path as _Path
    if mode == "global":
        return []
    if not (_Path(root) / ".worktreeinclude").is_file():
        return [(".worktreeinclude",
                 "missing — the installer materializes it so Claude Code carries "
                 "harness/ + .claude/settings.json into every worktree (F1); "
                 "reinstall per-project")]
    return []


_DISPATCH_MODULE_RE = re.compile(r"module:\s*([A-Za-z0-9_]+)")


def _dispatch_covered_hooks(root: Path, registered: set) -> set:
    """Hook filenames a REGISTERED dispatcher runs in-process, read from
    harness/data/hook-dispatch.yaml (regex-scanned to keep verify dependency-free).

    A hook migrated into the dispatcher is no longer wired by its own command, but it
    still fires — as a core of hook_dispatch.py. It counts as registered ONLY when the
    dispatcher itself is registered; otherwise the registry is inert data and its
    cores really are unwired. Returns filenames like `gate_stage.py`."""
    if "hook_dispatch.py" not in registered:
        return set()
    disp = root / "harness" / "data" / "hook-dispatch.yaml"
    if not disp.is_file():
        return set()
    try:
        text = disp.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {m + ".py" for m in _DISPATCH_MODULE_RE.findall(text)}


def hook_registration_problems(root: Path) -> list:
    """Co-presence between shipped entrypoint hooks and hooks-registration.yaml.
    Two failure modes, each NAMED per file (R8 install drift):
      - a registered command names a hook file that is absent on disk — the
        installer would wire a dangling command;
      - a shipped entrypoint hook (carries `__main__`) is missing from the
        registration — it ships but never fires (a silent no-op).
    A hook library WITHOUT `__main__` (hook_runtime, trace_log) is not an
    entrypoint and is not required to be registered. A hook migrated into
    hook_dispatch.py fires as a dispatcher CORE, not its own command, so it is
    counted registered when it appears in hook-dispatch.yaml under a registered
    dispatcher. Returns [] for a layout that has no registration file or hooks dir
    (not an installable tree)."""
    reg = root / "harness" / "install" / "hooks-registration.yaml"
    hooks_dir = root / "harness" / "hooks"
    if not reg.is_file() or not hooks_dir.is_dir():
        return []
    registered = set(_HOOK_CMD_RE.findall(reg.read_text(encoding="utf-8")))
    registered |= _dispatch_covered_hooks(root, registered)
    problems = []
    for fname in sorted(registered):
        if not (hooks_dir / fname).is_file():
            problems.append(
                ("harness/install/hooks-registration.yaml",
                 "registers %s but harness/hooks/%s is absent" % (fname, fname)))
    for p in sorted(hooks_dir.glob("*.py")):
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            continue
        if "__main__" not in text:
            continue  # library module, not an entrypoint hook
        if p.name not in registered:
            problems.append(
                ("harness/hooks/%s" % p.name,
                 "entrypoint hook not registered in hooks-registration.yaml "
                 "(ships but never fires)"))
    return problems


def _live_wired_hook_names(root: Path) -> set:
    """Basenames (no path, no CLI flags) of every hook .py wired into
    .claude/settings.json's `hooks` tree. Regex-scanned over the raw text (not
    JSON-walked) so this reuses the SAME `_HOOK_CMD_RE` pattern as
    hook_registration_problems — one hook-name-from-command-string parser, not
    two. Returns an empty set (not an error) when settings.json is absent/
    unreadable — this stays advisory (H1), never crashes verify_install."""
    settings = root / ".claude" / "settings.json"
    if not settings.is_file():
        return set()
    try:
        text = settings.read_text(encoding="utf-8")
    except OSError:
        return set()
    return {m[:-3] for m in _HOOK_CMD_RE.findall(text)}  # strip the .py suffix


def _ssot_enabled_hooks(root: Path) -> dict:
    """name -> enabled(bool) from harness/data/harness-hooks.yaml's explicit
    `hooks:` map. Only EXPLICIT booleans count (a hook resting on its
    class-default is not this SSOT's concern — see the file's own header).
    Returns {} on any missing-file/parse error (advisory; never crashes
    verify_install on a machine that skipped preflight / has no PyYAML)."""
    cfg = root / "harness" / "data" / "harness-hooks.yaml"
    if not cfg.is_file():
        return {}
    try:
        import yaml
        raw = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a malformed/unreadable SSOT is not fatal here
        return {}
    hooks = raw.get("hooks") if isinstance(raw, dict) else None
    if not isinstance(hooks, dict):
        return {}
    out = {}
    for name, entry in hooks.items():
        if isinstance(entry, dict) and isinstance(entry.get("enabled"), bool):
            out[name] = entry["enabled"]
    return out


def ssot_live_hook_drift_problems(root: Path) -> list:
    """H1: diff the hook SSOT (harness-hooks.yaml `enabled:`) against LIVE
    wiring (.claude/settings.json) so a flipped SSOT toggle can never silently
    drift from what is actually wired — the exact "config claims ON, live is
    dead" gap INV-3 found for glossary_pointer_inject and decision_reconcile_
    nudge. Advisory/fail-soft by design (unlike hook_registration_problems,
    which stays hard — see its own docstring): a repo checkout before install
    has no .claude/settings.json yet, and that absence must never read as
    drift, so this returns [] whenever settings.json or the SSOT can't be
    read, rather than raising or forcing a --strict failure.

    The two directions are NOT symmetric bugs, so they get different wording:
      - enabled=true + unwired -> a REAL defect (the hook can never fire no
        matter what the operator believes — the case INV-3 flagged HIGH).
      - enabled=false + still wired -> NOT a defect under this repo's
        wire-everything/toggle-via-SSOT pattern (every entrypoint self-checks
        hook_enabled() and no-ops when off — that's the whole point of the
        SSOT). It is real, worth-knowing STATE (a spawned-but-inert process
        per matching event, the per-invocation cost INV-3's F-9 counts), not a
        "live still fires" bypass — the wording says so explicitly."""
    settings = root / ".claude" / "settings.json"
    if not settings.is_file():
        return []
    ssot = _ssot_enabled_hooks(root)
    if not ssot:
        return []
    live = _live_wired_hook_names(root)
    # A hook migrated into hook_dispatch.py fires as a dispatcher CORE, not by its
    # own command, so it is live whenever the dispatcher itself is wired — union
    # those in (mirrors hook_registration_problems) or every grouped hook reads as
    # "config claims ON, live is dead".
    covered = _dispatch_covered_hooks(root, {n + ".py" for n in live})
    live |= {c[:-3] for c in covered}
    problems = []
    for name, enabled in sorted(ssot.items()):
        if enabled and name not in live:
            problems.append(
                ("harness/data/harness-hooks.yaml",
                 "%s: enabled=true in the SSOT but NOT wired in "
                 ".claude/settings.json — the hook can never fire "
                 "(config claims ON, live is dead)" % name))
        elif not enabled and name in live:
            problems.append(
                ("harness/data/harness-hooks.yaml",
                 "%s: enabled=false in the SSOT but its entrypoint is still "
                 "wired in .claude/settings.json — not a bug (the process "
                 "spawns and self-no-ops each matching event under the "
                 "wire-then-toggle pattern), but it costs a spawn; unwire it "
                 "there too if you want zero per-event cost" % name))
    return problems


def component_file_problems(root: Path) -> list:
    """Each file a component DECLARES (its hooks/scripts/data) must exist on
    disk — a component that ships a dangling member would wire/enable a file
    that isn't there. NAMED per missing file (R8 install drift). SKILLS are
    informational (a skill may be a not-yet-ported placeholder) and are not
    checked. Returns [] when components.yaml is absent (not a component-aware
    tree); a malformed manifest is itself reported as drift."""
    comp_file = root / "harness" / "data" / "components.yaml"
    if not comp_file.is_file():
        return []
    sys.path.append(str(root / "harness" / "scripts"))
    try:
        import component_config
        components = component_config.load_components(comp_file)
    except Exception as e:  # noqa: BLE001 — an unreadable manifest is drift
        return [("harness/data/components.yaml", "unreadable: %s" % e)]
    member_dirs = {
        "hooks": ("harness/hooks", ".py"),
        "scripts": ("harness/scripts", ".py"),
        "data": ("harness/data", ""),
    }
    problems = []
    for name in sorted(components):
        spec = components[name]
        for kind, (subdir, suffix) in member_dirs.items():
            for member in spec.get(kind, []):
                rel = "%s/%s%s" % (subdir, member, suffix)
                if not (root / rel).is_file():
                    problems.append(
                        (rel, "declared by component %r but missing on disk"
                         % name))
    return problems


def _source_escapes(plugins_dir: Path, candidate: Path) -> bool:
    """True if a marketplace `source`, joined under harness/plugins, resolves
    outside that dir — an absolute source or a `..` climb. The marketplace is
    attacker-influenceable (a crafted bundle), so a source like `../../etc/x`
    must be refused before the is_file probes read an arbitrary location. Uses
    resolve() + is_relative_to(), the same containment shape the installer's
    path guard enforces."""
    base = plugins_dir.resolve()
    try:
        resolved = candidate.resolve()
    except (OSError, RuntimeError, ValueError):
        # an unresolvable source (e.g. a circular symlink raising ELOOP) cannot
        # be proven contained — refuse it rather than crash verify (never-crash
        # contract, mirrors the symlink guard in standards_graph.py).
        return True
    return resolved != base and not resolved.is_relative_to(base)


def plugin_presence_problems(root: Path) -> list:
    """Every plugin the local marketplace DECLARES must exist on disk: a
    `.claude-plugin/plugin.json` plus a `skills/` or `agents/` dir (a plugin
    with neither loads nothing). NAMED per offending plugin (R8 install drift) —
    a marketplace that points at a missing plugin loads silently nothing.
    Returns [] when there is no marketplace.json (not a plugin-aware tree); an
    unreadable marketplace is itself reported as drift."""
    mp = root / "harness" / "plugins" / ".claude-plugin" / "marketplace.json"
    if not mp.is_file():
        return []
    try:
        data = json.loads(mp.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001 — an unreadable marketplace is drift
        return [("harness/plugins/.claude-plugin/marketplace.json",
                 "unreadable: %s" % e)]
    plugins_dir = root / "harness" / "plugins"
    problems = []
    for entry in data.get("plugins", []):
        name = entry.get("name")
        if not name:
            continue
        # `source` is a path relative to the marketplace dir; strip the leading
        # "./" as a PREFIX (not lstrip, which is a char-set strip that would eat a
        # leading dot of a real segment like "./.internal").
        src = entry.get("source") or ("./%s" % name)
        if src.startswith("./"):
            src = src[2:]
        base = plugins_dir / src
        rel = "harness/plugins/%s" % src
        # Containment: `source` is marketplace-supplied, so a crafted entry could
        # be absolute or climb out via `..` ("../../etc/x"), pointing the is_file
        # probes below at an arbitrary filesystem location. Refuse anything that
        # resolves outside harness/plugins (mirrors the installer's path guard).
        if _source_escapes(plugins_dir, base):
            problems.append(
                (rel, "marketplace declares plugin %r with source %r that "
                 "escapes harness/plugins (absolute or `..` climb) — refused"
                 % (name, src)))
            continue
        if not (base / ".claude-plugin" / "plugin.json").is_file():
            problems.append(
                (rel, "marketplace declares plugin %r but %s/.claude-plugin/"
                 "plugin.json is absent" % (name, rel)))
            continue
        # A plugin is loadable if it ships ANY content CC loads: skills, agents,
        # commands, or hooks. A hook-only plugin is legitimate (the harness has
        # hook-only feature bundles), so do not flag it as "nothing to load".
        if not any((base / d).is_dir()
                   for d in ("skills", "agents", "commands", "hooks")):
            problems.append(
                (rel, "plugin %r has no skills/agents/commands/hooks dir "
                 "(nothing to load)" % name))
    return problems


def settings_wiring_problems(project_root: Path) -> list:
    """Global-mode only: the PROJECT's settings.json/.local wired hook commands
    must reference $HARNESS_BIN_ROOT (the shared binary), not a stale project-local
    harness/hooks path. A project-local reference means the wiring never took the
    global switch — the hooks would look for a harness/ tree the global install
    never copied. NAMED per offending command; only harness-hook invocations are
    checked (a user command that merely mentions the dir is ignored)."""
    problems = []
    for name in ("settings.json", "settings.local.json"):
        p = project_root / ".claude" / name
        if not p.is_file():
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue  # verify() already reports an unreadable settings elsewhere
        for _event, groups in (data.get("hooks") or {}).items():
            for g in (groups or []):
                for h in (g.get("hooks") or []):
                    cmd = str(h.get("command", ""))
                    if _HOOK_CMD_RE.search(cmd) and "$HARNESS_BIN_ROOT" not in cmd:
                        problems.append(
                            (".claude/%s" % name,
                             "harness hook command does not reference "
                             "$HARNESS_BIN_ROOT under a global install (stale "
                             "project-local wiring): %s" % cmd[:100]))
    return problems


def tracked_settings_problems(project_root: Path) -> list:
    """Warn-only, mode-agnostic: `.claude/settings.json` (and .local) must NOT be
    git-tracked. It is install-materialized and mode-specific (global wires
    $HARNESS_BIN_ROOT, project wires $CLAUDE_PROJECT_DIR), so a committed copy
    freezes stale/wrong-mode hook wiring into git history — a later `git checkout`
    restores dead per-script paths and bricks every hook. Reported ACTIONABLE
    (name the fix). A non-git dir / missing git yields no problem (graceful)."""
    import subprocess
    problems = []
    for name in ("settings.json", "settings.local.json"):
        rel = ".claude/%s" % name
        try:
            r = subprocess.run(
                ["git", "-C", str(project_root), "ls-files", "--error-unmatch", rel],
                capture_output=True, text=True)
        except (OSError, ValueError):
            return []  # no git available → cannot check, stay silent
        if r.returncode == 0:
            problems.append(
                (rel, "is git-tracked — settings.json is install-materialized and "
                 "mode-specific; a committed copy restores stale/wrong-mode hook "
                 "wiring on `git checkout` and bricks every hook. Untrack it: "
                 "`git rm --cached %s` (the managed .gitignore block now ignores "
                 "it)." % rel))
    return problems


def recipient_skeleton_problems(project_root: Path) -> list:
    """Global-mode recipient check: the project's private `.harness/` data
    skeleton must be present (bootstrap seeds it on install / first SessionStart).
    A project that never bootstrapped has no writeable data home, so every
    per-project write would land on a missing dir. Reported ACTIONABLE (name the
    fix) rather than crashing. NAMED per missing piece. `.harness/state/` is the
    load-bearing member — trace/telemetry/sessions live under it."""
    data = project_root / ".harness"
    if not data.is_dir():
        return [(".harness/", "project data skeleton missing — run bootstrap "
                 "(install --global seeds it, or it self-seeds on first "
                 "SessionStart)")]
    if not (data / "state").is_dir():
        return [(".harness/state/", "data skeleton incomplete (no state/ dir) — "
                 "run bootstrap to reseed")]
    return []


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--root", default=".", help="repo root (default: cwd)")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero on any drift")
    args = ap.parse_args(argv)
    root = Path(args.root).resolve()

    # Under a global install the shared binary (manifest, hook scripts, components,
    # plugins) lives BIN-side while `--root` is the project; the integrity checks
    # resolve against bin_root(). Self-install (HARNESS_BIN_ROOT unset) keeps
    # everything at `--root`, unchanged. Detected by HARNESS_BIN_ROOT being set
    # (the same signal the guards use).
    global_mode = bool(os.environ.get("HARNESS_BIN_ROOT"))
    if global_mode:
        import harness_paths
        bin_root = harness_paths.bin_root()
    else:
        bin_root = root

    # The pre-push hook is a per-project git hook — always resolved against --root.
    for rel, problem in prepush_copy_warnings(root):
        sys.stderr.write("WARN %s: %s\n" % (rel, problem))
    # Orphans (on disk but unlisted) are warn-only — they never fail --strict by
    # themselves; the installer's --prune removes them on demand.
    for rel, problem in orphan_problems(bin_root):
        sys.stderr.write("WARN %s: %s\n" % (rel, problem))
    # H1: SSOT (harness-hooks.yaml) vs LIVE (.claude/settings.json) hook-enable
    # drift is advisory too — settings.json is per-project (always `root`, never
    # bin_root under a global install), and a pre-install checkout legitimately
    # has none yet.
    for rel, problem in ssot_live_hook_drift_problems(root):
        sys.stderr.write("WARN %s: %s\n" % (rel, problem))
    # A git-tracked settings.json is a checkout-landmine regardless of mode — warn
    # so a recipient untracks it before a branch switch bricks their hooks.
    for rel, problem in tracked_settings_problems(root):
        sys.stderr.write("WARN %s: %s\n" % (rel, problem))
    # Localization applies to integrity hash drift only. Hook-registration
    # co-presence defects (a dangling wire, an unregistered entrypoint) are real
    # bugs regardless of which file they are keyed to, so they stay hard.
    hard, localized = split_localized(verify(bin_root))
    hard += hook_registration_problems(bin_root)
    hard += component_file_problems(bin_root)
    hard += plugin_presence_problems(bin_root)
    if global_mode:
        # The wired form lives project-side: confirm it points at the bin.
        hard += settings_wiring_problems(root)
        # The recipient's private data skeleton lives project-side too: confirm
        # bootstrap seeded it (missing → every per-project write hits a bare dir).
        hard += recipient_skeleton_problems(root)
    # Per-project .worktreeinclude presence (F1); skipped under global (R8).
    # Warn-only (like the pre-push hook): a partial/pre-install tree legitimately
    # lacks it, so its absence must not fail --strict — the installer writes it.
    for rel, problem in worktreeinclude_problems(
            root, mode=("global" if global_mode else "project")):
        sys.stderr.write("WARN %s: %s\n" % (rel, problem))
    for rel, problem in localized:
        sys.stderr.write(
            "WARN %s: %s (deployer-localized config — expected to differ from "
            "the shipped baseline)\n" % (rel, problem))
    if not hard:
        print("verify_install OK: manifest + hook registration consistent")
        return 0
    for rel, problem in hard:
        sys.stderr.write("DRIFT %s: %s\n" % (rel, problem))
    sys.stderr.write("verify_install: %d file(s) drifted\n" % len(hard))
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
