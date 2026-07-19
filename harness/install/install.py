#!/usr/bin/env python3
"""install.py — one-command install of the harness into a target repo.

Turns the source harness tree into an installed-and-wired copy:
  1. copy the git-tracked harness/ tree into <target>/harness/ (the tracked set
     is exactly what manifest.json covers, so verify stays clean for free;
     untracked org standards under standards/ are INPUT, never shipped);
  2. materialize hooks-registration.yaml into <target>/.claude/settings.json in
     Claude Code shape, $HARNESS_ROOT -> "$CLAUDE_PROJECT_DIR", MERGED into any
     user-authored hooks (additive, dedup by command — never clobbers);
  3. install the pre-push transport gate into <target>/.git/hooks/pre-push,
     backing up a pre-existing foreign hook to pre-push.bak;
  4. check the org standards are present (warn-only — authoring them is the
     org's job, the installer never fabricates);
  5. run verify_install as the final gate and report drift per file.

Every mutation is idempotent and previewable with --dry-run; --uninstall
reverses the settings and pre-push edits (the harness/ tree is left in place —
deleting it is the documented clean uninstall).

--non-interactive (alias --yes) forces the non-prompt path even on a TTY (the
CI case); a non-TTY never prompts.

Usage:
    python3 harness/install/install.py [--target <repo>]
                                       [--local] [--dry-run] [--strict]
                                       [--non-interactive|--yes] [--uninstall]
"""

import argparse
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import verify_install  # noqa: E402

if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
from _prompts import (  # noqa: E402 — sibling install-package module
    _stdin_is_tty, _prompt_statusline, _prompt_cli, _prompt_components,
    _prompt_skill_selection, _prompt_install_mode)
from _errors import InstallError  # noqa: E402
from _settings import (  # noqa: E402
    _settings_path, _read_json, _load_settings, _write_settings)
import _wire_env  # noqa: E402 — sibling install-package module (global env wiring)
import _harden_bin  # noqa: E402 — sibling install-package module (opt-in bin hardening)
from _hooks import (  # noqa: E402 — sibling install-package module
    ALLOWED_EVENTS, to_command, hook_interpreter, load_registration,
    materialize_hooks, _find_group, merge_hooks, strip_harness_hooks,
    _invokes_harness_hook)
from _tree import (  # noqa: E402 — sibling install-package module
    _manifest_harness_files, _tracked_harness_files, _harness_is_tracked,
    _rel_escapes, _copy_tree, _write_omitted_skills, _prune_orphans,
    _remove_omitted_skill_dirs)
from _components import (  # noqa: E402 — sibling install-package module
    MARKETPLACE, SPINE_PLUGIN, _PLUGINS_REL,
    _chosen_components, _apply_components, _marketplace_plugins, _wire_plugins,
    _resolve_policy_components)
from _integrations import (  # noqa: E402 — sibling install-package module
    _statusline_config_home, _wire_statusline, _cli_bindir, _wire_cli)
from _prepush import (  # noqa: E402 — sibling install-package module
    _prepush_backup_dest, _install_prepush, _uninstall_prepush)
from _target_files import (  # noqa: E402 — sibling install-package module
    _standards_maxloc,
    _write_gitignore, _claude_md_block, _strip_stale_claude_blocks,
    _write_claude_md, _write_worktreeinclude, _check_standards,
    _CLAUDE_BEGIN, _CLAUDE_END)

# Files the installer (or the deploying team) localizes per target ship as a
# baseline in the manifest, but post-install divergence is customization, not
# integrity drift — so the final verify reports it as a note rather than failing
# --strict over it. The classifier is verify_install.is_localized (single source
# of truth, shared with the verify CLI — no second copy of the rule here).


# --- filesystem steps (guarded by dry_run via the orchestrator) ----------


def _wire_settings(target_root, registration, local, result, dry_run, mode="project"):
    new_hooks, skipped = materialize_hooks(registration, mode=mode)
    result["skipped_events"].extend(skipped)
    path = _settings_path(target_root, local)
    settings = _load_settings(path)
    # Reconcile, don't just add. Strip every harness-owned entry first so a hook
    # the new version dropped — or a narrowed component now omits — loses its
    # stale wiring instead of lingering: a command pointing at a removed hook .py
    # bricks every Bash tool-call with "can't open file". strip_harness_hooks only
    # drops commands that invoke a harness hook script, so user-authored hooks
    # survive; the current registration is then re-wired on top.
    reconciled = strip_harness_hooks(settings.get("hooks") or {})
    settings["hooks"] = merge_hooks(reconciled, new_hooks)
    _write_settings(path, settings, dry_run)
    result["actions"].append(
        "wire %d event(s) into %s" % (len(new_hooks), path.name))
    for event, _cmd in skipped:
        result["warnings"].append(
            "skipped non-Claude-Code event %r (not wired)" % event)


def _final_verify(target_root, strict, result, *, mode="project",
                  source_root=None):
    # Under a global install the shared binary (manifest, hook scripts,
    # components, plugins) lives BIN-side (source_root) — the project target
    # deliberately carries NO harness/ tree, only its wired settings + private
    # data skeleton. So integrity is verified against the bin, and the project
    # side is checked for correct wiring — mirroring verify_install.main. Project
    # mode verifies the copied tree at target_root, unchanged.
    integrity_root = source_root if mode == "global" and source_root else target_root
    # The pre-push hook is a per-project git hook — always resolved at the target.
    for rel, prob in verify_install.prepush_copy_warnings(target_root):
        result["warnings"].append("%s: %s" % (rel, prob))
    # Orphans (on disk, unlisted by the manifest) are warn-only — reported so a
    # reinstall surfaces stale files, but never failing --strict (--prune removes).
    for rel, prob in verify_install.orphan_problems(integrity_root):
        result["warnings"].append("%s: %s" % (rel, prob))
    # A git-tracked settings.json is a checkout-landmine (stale/wrong-mode hook
    # wiring restored on branch switch) — warn so the recipient untracks it.
    for rel, prob in verify_install.tracked_settings_problems(target_root):
        result["warnings"].append("%s: %s" % (rel, prob))
    # Localization applies to integrity hash drift only; hook-registration
    # co-presence defects stay hard (a dangling wire is a bug, not config).
    hard, localized = verify_install.split_localized(
        verify_install.verify(integrity_root))
    hard += verify_install.hook_registration_problems(integrity_root)
    hard += verify_install.component_file_problems(integrity_root)
    hard += verify_install.plugin_presence_problems(integrity_root)
    if mode == "global":
        # The wired form + the recipient's private data skeleton live
        # project-side: confirm settings point at the bin and bootstrap seeded
        # the skeleton (a bare project dir means every per-project write fails).
        hard += verify_install.settings_wiring_problems(target_root)
        hard += verify_install.recipient_skeleton_problems(target_root)
    # Per-project .worktreeinclude presence (F1); skipped under global (R8).
    # Warn-only (like the pre-push gate): the installer writes it, but a partial
    # tree must not fail --strict over its absence.
    for rel, prob in verify_install.worktreeinclude_problems(target_root, mode=mode):
        result["warnings"].append("%s: %s" % (rel, prob))
    for rel, prob in localized:
        result["warnings"].append(
            "%s: %s (deployer-localized — expected to differ from the "
            "shipped baseline)" % (rel, prob))
    result["problems"].extend(hard)
    if hard and strict:
        result["ok"] = False


def _uninstall_settings(target_root, local, result, dry_run):
    path = _settings_path(target_root, local)
    if not path.is_file():
        result["warnings"].append("no %s to clean" % path.name)
        return
    settings = _read_json(path)
    stripped = strip_harness_hooks(settings.get("hooks") or {})
    if stripped:
        settings["hooks"] = stripped
    else:
        settings.pop("hooks", None)
    _write_settings(path, settings, dry_run)
    result["actions"].append(
        "remove harness hook entries from %s" % path.name)


# --- orchestrator --------------------------------------------------------


def install(source_root, target_root, *, dry_run=False, local=False,
            uninstall=False, strict=False,
            no_track=False, components=None, prune=False,
            statusline=False, statusline_home=None,
            cli=False, cli_bindir=None,
            skills=None, skill_groups=None, all_skills=False, add_skills=None,
            mode="project", harden_bin=False) -> dict:
    """Run the full install (or uninstall) and return a result dict:
    {source_is_target, actions, skipped_events, warnings, problems, ok}.

    `components` selects optional components: a CSV of names, "all", "" (none), or
    None. None falls back to the shipped component-policy default (default-only-hs:
    the themed plugin groups ship OFF) rather than force-enabling everything.

    `prune` (off by default) removes harness/ files on disk but absent from the
    manifest — the orphans a prior install/upgrade leaves when the shipped set
    shrinks. Without it those orphans are only reported as a warning."""
    source_root = Path(source_root).resolve()
    target_root = Path(target_root).resolve()
    if components is None:
        components = _resolve_policy_components(source_root)
    result = {
        "source_is_target": source_root == target_root,
        "actions": [], "skipped_events": [], "warnings": [],
        "problems": [], "ok": True,
    }

    if uninstall:
        _uninstall_settings(target_root, local, result, dry_run)
        _uninstall_prepush(source_root, target_root, result, dry_run)
        return result

    # Global install serves ONE shared binary via env-resolve — it must NOT be
    # overlaid on a project that already carries its own per-project harness/
    # tree (mixed state: two harness trees, ambiguous which the guards resolve).
    # Refuse and guide to uninstall-first; NO auto-migrate (error-prone).
    if mode == "global" and not result["source_is_target"]:
        existing = target_root / "harness"
        if existing.exists():
            result["ok"] = False
            result["problems"].append(
                "a per-project harness/ tree already exists at %s — a global "
                "install serves the shared binary via env and must not overlay "
                "it. Uninstall the per-project harness first "
                "(python3 harness/install/install.py --target %s --uninstall, "
                "then remove the harness/ tree), then re-run with --global."
                % (existing, target_root))
            return result

    # Resolve per-skill selection -> the skill dirs to OMIT at copy. With no explicit
    # --skills/--skill-groups the default is now the shipped default-OFF catalog
    # (skill-defaults.yaml: ~38 ON, 71 stashed), NOT ship-all — `--all-skills` is the
    # explicit escape back to shipping everything. The spine core is always kept
    # (skill_selection enforces it), so this can never omit a load-bearing spine dir.
    omitted_skills = frozenset()
    if not result["source_is_target"] and not all_skills:
        ssel_dir = str(source_root / "harness" / "scripts")
        if ssel_dir not in sys.path:
            sys.path.insert(0, ssel_dir)
        try:
            import skill_selection as ssel
        except Exception as e:  # noqa: BLE001 — selector unavailable: install all, warn
            result["warnings"].append("skill selection unavailable: %s" % e)
        else:
            sk = ([s.strip() for s in skills.split(",") if s.strip()]
                  if skills is not None else None)
            gp = ([g.strip() for g in skill_groups.split(",") if g.strip()]
                  if skill_groups is not None else None)
            try:
                # sk=None AND gp=None AND add_skills=None -> the default-off catalog;
                # add_skills=[...] -> recommended baseline + opt-in clusters (unclosed
                # baseline); otherwise the explicit selection (deps auto-tick, floor bare).
                enabled = ssel.resolve_enabled(
                    source_root=source_root, skills=sk, groups=gp, add_skills=add_skills)
                omitted_skills = ssel.omitted(source_root, enabled)
            except ssel.SelectionError as e:
                # a typo'd skill/group must NOT silently install everything (the
                # opposite of the user's intent). Mirror the component-selection
                # path and abort with a deployer-actionable message instead.
                raise InstallError("skill selection invalid: %s" % e)

    registration = load_registration(source_root)
    if result["source_is_target"]:
        # source == target is the in-repo dev no-op ONLY when harness/ is really
        # tracked here. If it is untracked, the user almost certainly unpacked a
        # release bundle INSIDE the target and ran `--target .` — skipping the copy
        # would install nothing and look like success. Refuse on a real run; on a
        # dry-run, warn (planning previews must not raise).
        if not _harness_is_tracked(source_root):
            mistake = (
                "source == target but harness/ is not git-tracked here — this "
                "looks like a release bundle unpacked INSIDE the target repo. The "
                "installer would skip the file copy (the in-repo dev no-op) and "
                "install nothing. Extract the bundle OUTSIDE the repo and point "
                "--target at the repo, or pass --source at the extracted tree.")
            if not dry_run:
                raise InstallError(mistake)
            result["warnings"].append(
                "a real run would install nothing: " + mistake)
        result["actions"].append(
            "source == target: skip tree copy (dogfood no-op)")
    elif mode == "global":
        # Global install resolves the shared binary via env — no per-project
        # harness/ tree is copied into the target. Hooks point at
        # $HARNESS_BIN_ROOT (wired below) and _wire_env records the value.
        result["actions"].append(
            "global mode: env-resolve, no per-project tree copy")
    else:
        result["actions"].append(
            _copy_tree(source_root, target_root, dry_run, omitted_skills))
        # Remove any omitted skill dir left from a wider prior install (a re-install
        # NARROWING the selection) so "omit = off" is real, not a no-op; then ALWAYS
        # rewrite the record so an install-all re-run overwrites a stale narrowed
        # record (else verify --strict reads it and skips a real drift).
        _remove_omitted_skill_dirs(target_root, omitted_skills, result, dry_run)
        _write_omitted_skills(target_root, omitted_skills, result, dry_run)
    _wire_settings(target_root, registration, local, result, dry_run, mode=mode)
    if mode == "global":
        # Record the machine-specific bin/data roots into settings.local.json —
        # the value behind the $HARNESS_BIN_ROOT placeholder the hooks now carry.
        # The shared binary IS the source tree the installer runs from.
        env = _wire_env.wire_env(target_root, bin_root=str(source_root),
                                 data_root=str(target_root / ".harness"),
                                 dry_run=dry_run)
        result["actions"].append(
            "wire global env (HARNESS_BIN_ROOT) into settings.local.json")
        result["warnings"].append(_wire_env.RESTART_NOTE)
        # Seed the recipient's private .harness/ data skeleton — the installer is
        # a legit writer, so it owns the first-run mkdir (readers stay PURE). The
        # SessionStart net re-seeds a project cloned onto a fresh machine where
        # .harness/ (gitignored) never came along.
        import bootstrap  # sibling install-package module
        seeded = bootstrap.ensure_skeleton(target_root / ".harness", dry_run=dry_run)
        result["actions"].append(
            "bootstrap: seed project .harness/ skeleton (%d path(s))" % len(seeded))
        if harden_bin:
            n = _harden_bin.harden_bin(source_root, dry_run=dry_run)
            result["actions"].append(
                "harden-bin: strip write bits from %d path(s) under %s"
                % (n, source_root))
    if not result["source_is_target"] and mode != "global":
        # a dogfood install must not project onto the source's own hand-authored
        # harness-hooks.yaml (the live gate config) — selection is a deploy step.
        _apply_components(target_root, components, result, dry_run)
        # wire the plugin marketplace + enabledPlugins into the same settings
        # file the hooks went to (skipped on dogfood for the same reason). On a
        # dry run the target tree isn't copied, so pass source_root to preview
        # what WOULD be wired from the marketplace.
        _wire_plugins(target_root, components, local, result, dry_run,
                      source_root=source_root)
        if statusline:
            _wire_statusline(source_root, target_root, local, result, dry_run,
                             statusline_home)
        if cli:
            _wire_cli(source_root, target_root, result, dry_run, cli_bindir)
        if prune:
            # remove orphans the prior install left (off by default — see flag).
            _prune_orphans(target_root, result, dry_run)
    if mode == "global":
        # The pre-push hook loads push_gate from <repo>/harness/scripts — a tree
        # a global install deliberately never places in the project ("no
        # per-project tree copy"). Installing it would brick EVERY push with an
        # ImportError, forcing --no-verify. So under global we do NOT install it;
        # we run the reverse instead, so a stale harness hook left by a prior
        # per-project install is cleaned (a foreign user hook is left as-is).
        # The primary push gate under global is the PreToolUse(Bash) hook wired
        # to $HARNESS_BIN_ROOT, which sees only a push through Claude Code's Bash
        # tool. The git hook governed EVERY push at the transport layer (terminal,
        # IDE, CI, and sh -c / alias / wrapper spellings) — that coverage is not
        # available under global. A documented posture delta (global-install-guide).
        _uninstall_prepush(source_root, target_root, result, dry_run)
    else:
        _install_prepush(source_root, target_root, result, dry_run)
    if not result["source_is_target"]:
        # a dogfood (source==target) install must not append to the source's
        # own .gitignore / CLAUDE.md — they are hand-authored here.
        _write_gitignore(target_root, result, dry_run, no_track=no_track)
        _write_claude_md(target_root, result, dry_run)
        if mode != "global":
            # Global serves one shared bin, so there is no per-project harness/
            # tree to carry into a worktree; CC copies harness/ + settings.json via
            # .worktreeinclude only for a per-project checkout (R8). verify_install
            # likewise skips the presence check under global.
            _write_worktreeinclude(target_root, source_root, result, dry_run)
    _check_standards(target_root, result)
    if not dry_run:
        _final_verify(target_root, strict, result, mode=mode,
                      source_root=source_root)
    return result


def _force_utf8_stdio() -> None:
    """Windows consoles default to a legacy codepage (cp1252) that cannot encode
    the em-dash / arrow glyphs in this tool's output, so a plain print() raises
    UnicodeEncodeError — even on `--help` (argparse writes the docstring there).
    Force UTF-8 on stdout/stderr so the installer prints identically on every
    platform without the user having to set PYTHONUTF8."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):  # detached/!TextIOWrapper — leave as-is
                pass


def main(argv=None) -> int:
    _force_utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target", default=".",
                    help="target repo root to install into (default: cwd)")
    ap.add_argument("--source", default=str(_HERE.parent.parent),
                    help="source harness tree (default: this installer's repo)")
    ap.add_argument("--local", action="store_true",
                    help="wire into settings.local.json instead of settings.json")
    ap.add_argument("--dry-run", action="store_true",
                    help="plan the writes, change nothing")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if the final verify reports drift")
    ap.add_argument("--non-interactive", "--yes", dest="non_interactive",
                    action="store_true",
                    help="never prompt, even on a TTY (the CI/automation path); "
                         "the CI/automation path")
    ap.add_argument("--uninstall", action="store_true",
                    help="reverse the settings + pre-push edits "
                         "(harness/ is left in place; delete it to fully remove)")
    ap.add_argument("--no-track", dest="no_track", action="store_true",
                    help="install a running harness but gitignore harness/ so it "
                         "is never committed into the target's product git "
                         "(roster localization is skipped — the gate needs "
                         "harness/ tracked to be tamper-visible)")
    ap.add_argument("--components", default=None,
                    help="which optional components to ENABLE: 'all', or a CSV "
                         "(e.g. rbac,decision-capture). All components ship + wire "
                         "regardless; deselected ones are only runtime-disabled "
                         "(enabled:false). Omitted on a TTY → interactive prompt; "
                         "omitted otherwise → the shipped component-policy default "
                         "(spine-only: the themed plugin groups ship OFF), NOT "
                         "'all'.")
    ap.add_argument("--skills", default=None,
                    help="comma-separated SKILLS to install (per-skill selection "
                         "for the single hs plugin). Deps auto-tick and the 13 "
                         "spine skills are always included; every other skill's "
                         "dir is omitted at copy. Omitted entirely → install all.")
    ap.add_argument("--skill-groups", default=None,
                    help="comma-separated GROUP labels to install (e.g. think,viz) "
                         "— each expands to its skills (components.yaml). Combines "
                         "with --skills; deps + spine core always come along.")
    ap.add_argument("--all-skills", action="store_true",
                    help="ship EVERY skill (the explicit escape from the default-off "
                         "catalog). Without it and without --skills/--skill-groups, a "
                         "fresh install ships the recommended set (~38 ON) and stashes "
                         "the rest for hs:use / --enable.")
    ap.add_argument("--prune", action="store_true",
                    help="COARSE orphan removal: unlink every harness/ file absent "
                         "from the manifest, no backup, no user-added distinction. "
                         "Off by default. The safe default path is cleanup_orphans "
                         "(install.sh runs it on upgrade; hs:cleanup re-runs it) — "
                         "use --prune only to wipe a throwaway tree. Pairs with "
                         "--dry-run to preview the removals.")
    ap.add_argument("--statusline", action="store_true",
                    help="opt in to the ccstatusline terminal status bar: wire a "
                         "statusLine block into settings.json and copy a default "
                         "ccstatusline config into ~/.config (both no-clobber). "
                         "Omitted on a TTY → interactive prompt; omitted "
                         "otherwise → off.")
    ap.add_argument("--cli", action="store_true",
                    help="opt in to the on-PATH `hs-cli` launcher: symlink "
                         "~/.local/bin/hs-cli at the shipped wrapper (POSIX; "
                         "Windows prints a PATH hint), no-clobber. Omitted on a "
                         "TTY → interactive prompt; omitted otherwise → off.")
    ap.add_argument("--global", dest="global_mode", action="store_true",
                    help="GLOBAL install: one shared binary serves many projects. "
                         "Hooks point at $HARNESS_BIN_ROOT (wired into "
                         "settings.local.json); no per-project harness/ tree is "
                         "copied. Mutually exclusive with --project; neither → "
                         "prompt on a TTY, refuse otherwise (no silent default).")
    ap.add_argument("--project", dest="project_mode", action="store_true",
                    help="PROJECT install (back-compat): the harness/ tree lives "
                         "inside the project; hooks point at $CLAUDE_PROJECT_DIR.")
    ap.add_argument("--harden-bin", dest="harden_bin", action="store_true",
                    help="GLOBAL only, opt-in: after wiring, chmod the shared "
                         "binary read-only to the runtime user (the real fence for "
                         "Bash writes into the bin). Default OFF — forcing it would "
                         "block a dev editing the harness during dogfood.")
    args = ap.parse_args(argv)

    source_root = Path(args.source).resolve()
    target_root = Path(args.target).resolve()

    # Install mode is load-bearing (it decides whether the guards resolve one
    # shared binary or a per-project tree), so it is NEVER silently defaulted.
    # Explicit --global/--project wins; neither → PROMPT on a TTY, REFUSE
    # otherwise. Irrelevant on uninstall (settings-only reversal).
    if args.global_mode and args.project_mode:
        sys.stderr.write("ERROR --global and --project are mutually exclusive\n")
        return 2
    mode = "global" if args.global_mode else ("project" if args.project_mode else None)
    if mode is None and not args.uninstall:
        if _stdin_is_tty() and not args.non_interactive:
            mode = _prompt_install_mode()
        else:
            # Non-interactive with no flag: keep the historical PROJECT default so
            # every existing automation still installs — but warn loudly (never a
            # fully silent choice), and steer the caller to pass the flag.
            mode = "project"
            sys.stderr.write(
                "[warn] no install mode flag on a non-interactive run — defaulting "
                "to --project (per-project harness/ tree). Pass --global for the "
                "shared-binary install, or --project to silence this.\n")
    mode = mode or "project"

    # Deps precondition for a real install. install.sh runs preflight first, but
    # running install.py directly used to skip it — a missing pyyaml/defusedxml
    # then surfaced as an opaque hook ImportError much later. Fail fast here with
    # the exact pip command. A dry-run only plans, and uninstall touches JSON only,
    # so neither needs the runtime deps.
    if not args.dry_run and not args.uninstall:
        import preflight_deps
        missing = preflight_deps.missing_deps()
        if missing:
            sys.stderr.write(
                "install aborted — missing Python dependencies: %s\n"
                "The harness runtime needs them (every hook imports these). "
                "Install, then re-run:\n    %s\n"
                % (", ".join(missing), preflight_deps.install_command(missing)))
            return 2

    # Resolve the component selection. An explicit --components always wins.
    # On an interactive install we prompt (seeded from the shipped policy so the
    # themed groups read as opt-in). Otherwise we leave it None and install()
    # falls back to the component-policy default (default-only-hs) — never force-all.
    components_arg = args.components
    if (components_arg is None and not args.non_interactive and not args.uninstall
            and not args.dry_run and source_root != target_root and _stdin_is_tty()):
        comps_default, defaults = {}, {}
        try:
            sys.path.insert(0, str(source_root / "harness" / "scripts"))
            import component_config as cc
            comps_default = cc.load_components(
                source_root / "harness" / "data" / "components.yaml")
            defaults = cc.resolved_selection(
                comps_default,
                cc.load_policy(source_root / "harness" / "data" / "component-policy.yaml"))
        except Exception:  # noqa: BLE001 — no components map -> nothing to prompt
            comps_default, defaults = {}, {}
        # Post-collapse the themed/ck-port group components are selected via the
        # skill prompt (dir-omit), not the plugin toggle. The component prompt
        # governs only the HOOK-bearing components (rbac, decision-capture).
        if comps_default:
            hook_comps = {n: s for n, s in comps_default.items() if s.get("hooks")}
            if hook_comps:
                components_arg = _prompt_components(sorted(hook_comps), defaults)

    # Per-skill selection: pick which skill dirs install (single hs plugin). An
    # explicit --skills/--skill-groups wins; on an interactive install we prompt
    # over the group labels (the components with no hooks — the former plugins).
    skills_arg, skill_groups_arg, add_skills_arg = args.skills, args.skill_groups, None
    if (skills_arg is None and skill_groups_arg is None and not args.all_skills
            and not args.non_interactive and not args.uninstall
            and not args.dry_run and source_root != target_root and _stdin_is_tty()):
        try:
            sys.path.insert(0, str(source_root / "harness" / "scripts"))
            import component_config as cc
            _comps = cc.load_components(
                source_root / "harness" / "data" / "components.yaml")
            group_labels = [n for n, s in _comps.items() if not s.get("hooks")]
        except Exception:  # noqa: BLE001 — no map -> skip the skill prompt
            group_labels = []
        if group_labels:
            skills_arg, skill_groups_arg, add_skills_arg = _prompt_skill_selection(
                group_labels, source_root=source_root)

    # ccstatusline is opt-in. An explicit --statusline always wins; on an
    # interactive install we ask once; otherwise it stays off.
    statusline = args.statusline
    if (not statusline and not args.non_interactive and not args.uninstall
            and not args.dry_run and source_root != target_root
            and _stdin_is_tty()):
        statusline = _prompt_statusline()

    # the on-PATH hs-cli launcher is opt-in, same cadence as statusline.
    cli = args.cli
    if (not cli and not args.non_interactive and not args.uninstall
            and not args.dry_run and source_root != target_root
            and _stdin_is_tty()):
        cli = _prompt_cli()

    try:
        result = install(source_root, target_root, dry_run=args.dry_run,
                         local=args.local, uninstall=args.uninstall,
                         strict=args.strict,
                         no_track=args.no_track, components=components_arg,
                         prune=args.prune, statusline=statusline, cli=cli,
                         skills=skills_arg, skill_groups=skill_groups_arg,
                         all_skills=args.all_skills, add_skills=add_skills_arg,
                         mode=mode, harden_bin=args.harden_bin)
    except InstallError as e:
        sys.stderr.write("ERROR %s\n" % e)
        return 1

    head = "PLAN (dry-run)" if args.dry_run else "INSTALL"
    if args.uninstall:
        head = "UNINSTALL"
    print("== %s == target: %s" % (head, target_root))
    for a in result["actions"]:
        print("  - %s" % a)
    for w in result["warnings"]:
        sys.stderr.write("WARN %s\n" % w)
    for rel, prob in result["problems"]:
        sys.stderr.write("DRIFT %s: %s\n" % (rel, prob))
    if result["problems"]:
        sys.stderr.write("verify: %d file(s) drifted\n" % len(result["problems"]))
    else:
        print("verify: clean" if not args.dry_run and not args.uninstall else "done")

    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
