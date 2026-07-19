#!/usr/bin/env python3
"""hs-cli — a thin operator front-end over the harness scripts.

Every verb wraps a script that already owns the logic; the CLI adds no new
behaviour, just one discoverable entry point and install-time group selection.

    hs doctor                      verify_install --strict + preflight_deps
    hs migrate [--check|--dry-run] run the decomposition migrate engine
    hs list                        plugins, their skills, and on/off state
    hs components --enable G ...    flip a group on/off (--disable G; bare or hs-G)
    hs version                     harness_version + kit_digest from release.json
    hs install [install.py args]   install + interactive group selection

No watch/content/dashboard verbs — those belong to a different tool (YAGNI).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parent
_ROOT = _SCRIPTS.parents[1]  # harness/scripts -> harness -> repo root
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))


def _run(argv: list[str]) -> int:
    """Run a child process, inheriting stdio, and return its exit code."""
    return subprocess.run(argv).returncode


# --------------------------------------------------------------------- verbs

def cmd_version(args) -> int:
    import harness_release
    rel = harness_release.read_release(_ROOT)
    print("hs {ver} ({ch})  kit_digest={dig}".format(
        ver=rel.get("harness_version", "?"),
        ch=rel.get("channel", "?"),
        dig=rel.get("kit_digest", "?"),
    ))
    return 0


def cmd_trust(args) -> int:
    """Trust a repo root (TOFU) so its rule shell-detectors may auto-fire, or
    list the trusted roots. Per-machine, recorded outside git."""
    import trust_store
    if args.list:
        for root in sorted(trust_store.load_trust()):
            print(root)
        return 0
    try:
        rp = trust_store.add_trust(args.repo)
    except trust_store.TrustError as exc:
        print("trust refused: %s" % exc, file=sys.stderr)
        return 2
    print("trusted: %s" % rp)
    return 0


def cmd_doctor(args) -> int:
    # verify_install reds on inconsistency; preflight never blocks (advisory).
    rc = _run([sys.executable, str(_SCRIPTS / "verify_install.py"), "--strict"])
    _run([sys.executable, str(_SCRIPTS / "preflight_deps.py")])
    return 1 if rc else 0


def cmd_gates(args) -> int:
    """Report the resolved stage-gate posture: per-stage hard/soft + required
    artifacts, active posture overrides, and whether the opt-in security-scan gate is
    on. Read-only — the operator's window into what actually gates a push/ship."""
    sys.path.insert(0, str(_SCRIPTS))
    import artifact_check
    try:
        stages = artifact_check.load_policy().get("stages", {}) or {}
    except Exception as e:  # noqa: BLE001 — a malformed policy should say so, not crash
        print("could not load stage-policy: %s" % e, file=sys.stderr)
        return 1
    overrides = [k for k in ("HARNESS_STAGE_POLICY", "HARNESS_GUARD_POLICY",
                             "HARNESS_PROTECTED_BRANCHES")
                 if (os.environ.get(k) or "").strip()]
    print("stage-gate posture (source: %s)"
          % ("ENV OVERRIDE" if overrides else "tracked stage-policy.yaml"))
    if overrides:
        print("  ! posture override(s) active: %s — in-session policy is redirected; "
              "the pre-push transport re-judges with tracked config" % ", ".join(overrides))
    for name, spec in stages.items():
        if not isinstance(spec, dict):
            continue
        reqs = spec.get("requires") or []
        rp = "" if spec.get("require_plan", True) else "  [require_plan: false]"
        print("  %-8s %-4s  requires: %s%s"
              % (name, "HARD" if spec.get("hard") else "soft",
                 ", ".join(str(r) for r in reqs) if reqs else "(none)", rp))
    sec_on = any("security-scan" in (s.get("requires") or [])
                 for s in stages.values() if isinstance(s, dict))
    print("  security-scan gate: %s"
          % ("ON" if sec_on else "OFF (opt-in — add 'security-scan' to a stage's requires)"))
    return 0


def cmd_guards(args) -> int:
    """Report the resolved GUARD posture: the off/warn/block preset + any per-guard
    override, and the protected branches. Pairs with `gates` (stage gates) for the
    full picture. Read-only."""
    sys.path.insert(0, str(_SCRIPTS))
    import branch_policy
    import guard_policy
    overrides = [k for k in ("HARNESS_GUARD_POLICY", "HARNESS_PROTECTED_BRANCHES")
                 if (os.environ.get(k) or "").strip()]
    print("guard posture (source: %s)"
          % ("ENV OVERRIDE" if overrides else "tracked config"))
    if overrides:
        print("  ! override(s) active: %s — in-session only; pre-push uses tracked config"
              % ", ".join(overrides))
    try:
        pol = guard_policy.load_guard_policy()
        print("  preset: %s" % pol.get("preset", "?"))
        ov = pol.get("overrides")
        print("  per-guard overrides: %s"
              % (ov if ov else "(none — every guard runs at the preset)"))
    except Exception as e:  # noqa: BLE001 — a bad policy should report, not crash
        print("  guard-policy: could not load (%s)" % e)
    try:
        prot = branch_policy.load_protected()
        print("  protected branches: %s" % (", ".join(prot) if prot else "(none)"))
    except Exception as e:  # noqa: BLE001
        print("  protected-branches: could not load (%s)" % e)
    return 0


def _hook_registry(root: Path) -> list:
    """Parse hooks-registration.yaml into [{name, event, class}], name = the hook
    script's basename. The installer's registration is the COMPLETE hook map;
    component_config only knows the 3 hook-bearing components, so it would
    under-report. A hook_dispatch.py command is EXPANDED into its per-core hooks
    (from hook-dispatch.yaml) — the migrated leaf gates still fire in-process, and
    tầng-2 discovery must see each of them, not the multiplexer. Read-only, off the
    harness root (never CWD)."""
    import yaml
    reg = root / "harness" / "install" / "hooks-registration.yaml"
    data = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
    disp = {}
    disp_path = root / "harness" / "data" / "hook-dispatch.yaml"
    if disp_path.is_file():
        dd = yaml.safe_load(disp_path.read_text(encoding="utf-8")) or {}
        for gk, cores in (dd.get("groups") or {}).items():
            ev, _, mt = str(gk).partition(":")
            disp[(ev, mt or None)] = cores or []
    out = []
    for entry in (data.get("hooks") or []):
        if not isinstance(entry, dict):
            continue
        name = None
        for tok in str(entry.get("command", "")).split():
            if tok.endswith(".py"):
                name = os.path.basename(tok)[:-3]
        if not name:
            continue
        if name == "hook_dispatch":
            for c in disp.get((entry.get("event"), entry.get("matcher")), []):
                if isinstance(c, dict) and c.get("module"):
                    out.append({"name": c["module"], "event": entry.get("event", ""),
                                "class": c.get("class", "nudge")})
            continue
        out.append({"name": name, "event": entry.get("event", ""),
                    "class": entry.get("class", "nudge")})
    return out


def cmd_capabilities(args) -> int:
    """Emit the harness's registered hooks + stage gates as JSON (read-only) — the
    tầng-2 orchestrator's path-free discovery source (the CLI exposes no hook/gate
    registry, spike 260709). Wraps the existing config layer: hooks-registration
    (name/event/class) + hook_runtime.hook_enabled (live state) + stage-policy (gates).
    Never re-implements gate logic. Fail-loud: a broken config source exits nonzero +
    stderr so the consumer maps to an explicit error state, never a silent empty map."""
    sys.path.insert(0, str(_SCRIPTS))
    sys.path.insert(0, str(_ROOT / "harness" / "hooks"))
    try:
        import artifact_check
        import hook_runtime
        hooks = []
        for h in _hook_registry(_ROOT):
            enabled = hook_runtime.hook_enabled(h["name"], h["class"])
            hooks.append({"name": h["name"], "event": h["event"],
                          "enabled": bool(enabled), "class": h["class"]})
        stages = artifact_check.load_policy().get("stages", {}) or {}
        gates = [{"stage": name, "requires": list(spec.get("requires") or [])}
                 for name, spec in stages.items() if isinstance(spec, dict)]
    except Exception as e:  # noqa: BLE001 — a broken source is an error state, reported
        print("capabilities: could not build map: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps({"schema": "hs-capabilities/1", "hooks": hooks, "gates": gates},
                     ensure_ascii=False))
    return 0


def _component_name(group: str) -> str:
    """Accept either the plugin name (hs-flow) or the bare component (flow)."""
    return group[3:] if group.startswith("hs-") else group


def _default_settings_path() -> str:
    """The same .claude/settings.json the installer writes enabledPlugins into.
    Honors CLAUDE_PROJECT_DIR (the running project) like the rest of the CLI."""
    base = Path(os.environ.get("CLAUDE_PROJECT_DIR") or _ROOT)
    return str(base / ".claude" / "settings.json")


def _skill_args(args, enable=None, disable=None):
    """A cmd_skills-shaped namespace, carrying the components verb's --root."""
    import argparse
    return argparse.Namespace(enable=enable, disable=disable,
                              root=getattr(args, "root", None))


def cmd_components(args) -> int:
    import component_config as cc
    base = (Path(args.root) if getattr(args, "root", None)
            else Path(os.environ.get("CLAUDE_PROJECT_DIR") or _ROOT))
    comps = cc.load_components(base / "harness" / "data" / "components.yaml")
    if not args.enable and not args.disable:
        argv = ["show"]
        if args.policy_file:
            argv += ["--policy-file", args.policy_file]
        if args.hooks_file:
            argv += ["--hooks-file", args.hooks_file]
        if args.state_file:
            argv += ["--state-file", args.state_file]
        if args.settings_file:
            argv += ["--settings-file", args.settings_file]
        return cc.main(argv)

    # Post-collapse there is one plugin, so the former plugin groups are SKILL
    # LABELS: toggling them must omit/restore their skill dirs, not flip a dead
    # enabledPlugins key. Only the hook-bearing components (rbac, decision-capture)
    # still ride the hook-flag path. Classify each requested name accordingly.
    hook_sel: dict[str, bool] = {}
    label_enable, label_disable = [], []
    for g in args.enable or []:
        n = _component_name(g)
        if comps.get(n, {}).get("hooks"):
            hook_sel[n] = True
        else:
            label_enable.append(n)
    for g in args.disable or []:
        n = _component_name(g)
        if comps.get(n, {}).get("hooks"):
            hook_sel[n] = False
        else:
            label_disable.append(n)

    rc = 0
    for grp in label_enable:
        skills = list(comps.get(grp, {}).get("skills") or [])
        if not skills:
            print("error: unknown component/group %r" % grp, file=sys.stderr)
            rc = 2
            continue
        rc = cmd_skills(_skill_args(args, enable=skills)) or rc
    for grp in label_disable:
        skills = list(comps.get(grp, {}).get("skills") or [])
        if not skills:
            print("error: unknown component/group %r" % grp, file=sys.stderr)
            rc = 2
            continue
        rc = cmd_skills(_skill_args(args, disable=skills)) or rc

    if hook_sel:
        # --root must govern the hook branch too: write the flags into the TARGET,
        # not this repo's settings. When --root is given, derive every apply_selection
        # path from base; otherwise keep the legacy CLAUDE_PROJECT_DIR/default paths.
        rooted = getattr(args, "root", None) is not None
        settings_path = args.settings_file or (
            str(base / ".claude" / "settings.json") if rooted
            else _default_settings_path())
        try:
            cc.apply_selection(
                hook_sel,
                policy_path=args.policy_file or (
                    str(base / "harness/data/component-policy.yaml") if rooted else None),
                settings_path=settings_path,
                hooks_path=args.hooks_file or (
                    str(base / "harness/data/harness-hooks.yaml") if rooted else None),
                state_path=args.state_file or (
                    str(base / "harness/state/install-state.json") if rooted else None))
        except cc.ComponentConfigError as e:
            print("error: %s" % e, file=sys.stderr)
            return 2
        print("components updated: " + ", ".join(
            "%s=%s" % (k, "on" if v else "off") for k, v in sorted(hook_sel.items())))
    return rc


def _enabled_plugins() -> dict:
    merged: dict = {}
    base = Path(os.environ.get("CLAUDE_PROJECT_DIR") or _ROOT) / ".claude"
    for fn in ("settings.json", "settings.local.json"):
        try:
            d = json.loads((base / fn).read_text(encoding="utf-8"))
            ep = d.get("enabledPlugins")
            if isinstance(ep, dict):
                merged.update(ep)
        except Exception:  # noqa: BLE001 — absent/invalid settings
            continue
    return merged


def _plugin_on(plugin: str, enabled: dict) -> bool:
    if plugin == "hs":
        return True  # spine is always on
    for key, val in enabled.items():
        if str(key).split("@", 1)[0] == plugin:
            return bool(val)
    return False


def cmd_list(args) -> int:
    plugins_dir = _ROOT / "harness/plugins"
    enabled = _enabled_plugins()
    rows = []
    for pdir in sorted(plugins_dir.iterdir()):
        sdir = pdir / "skills"
        if not sdir.is_dir():
            continue
        # a skill dir carries SKILL.md; a bare resource dir (e.g. `common`, shared
        # ai-group helpers) is not a skill — match the 96-invokable count the
        # selection + STANDARDIZE enumerators use.
        skills = sorted(p.name for p in sdir.iterdir()
                        if p.is_dir() and (p / "SKILL.md").is_file())
        if not skills:
            continue
        state = "spine" if pdir.name == "hs" else ("on" if _plugin_on(pdir.name, enabled) else "off")
        rows.append((pdir.name, state, skills))
    width = max((len(n) for n, _, _ in rows), default=2)
    for name, state, skills in rows:
        print("%-*s  [%-5s]  %2d  %s" % (width, name, state, len(skills), ", ".join(skills)))
    return 0


def _skills_paths(args):
    base = Path(args.root) if getattr(args, "root", None) else Path(
        os.environ.get("CLAUDE_PROJECT_DIR") or _ROOT)
    return (base / "harness/plugins/hs/skills",
            base / "harness/plugins/hs/disabled-skills",
            base / "harness/state/install-omitted-skills.json",
            base / "harness/data/skill-deps.yaml")


def _dependents(skill, deps_path, among) -> set:
    """Skills in `among` that auto-tick `skill` (declare it as a dep)."""
    try:
        import skill_deps
        graph = skill_deps.load_deps(deps_path)["skills"]
    except Exception:  # noqa: BLE001 — no graph -> no known dependents
        return set()
    return {s for s in among if skill in (graph.get(s, {}).get("deps") or [])}


def _split_csv(v):
    """A comma-joined --off/--on value (repeatable) -> flat, de-duped, ordered list."""
    out = []
    for chunk in (v or []):
        for name in chunk.split(","):
            name = name.strip()
            if name and name not in out:
                out.append(name)
    return out


def cmd_skills(args) -> int:
    """Enable/disable individual skills for the collapsed hs plugin (dir-omit).

    --disable stashes the skill dir under harness/plugins/hs/disabled-skills (a
    TRACKED sibling of skills/, so the off skill ships with the bundle and toggles
    without a reinstall; the loader only scans skills/ so it stays hidden) and records
    it in install-omitted-skills.json (the verify_install seam keeps the absence from
    reading as drift). The move is a git rename in the source repo, not a deletion, so
    disabling in-place is safe. --enable restores the dir and auto-restores its deps.
    The spine core is refused. Bare lists on/off."""
    import shutil
    import skill_deps
    skills_dir, stash_dir, omit_path, deps_path = _skills_paths(args)

    def _present():
        return ({d.name for d in skills_dir.iterdir() if (d / "SKILL.md").is_file()}
                if skills_dir.is_dir() else set())

    import omit_record
    _base = (Path(args.root) if getattr(args, "root", None)
             else Path(os.environ.get("CLAUDE_PROJECT_DIR") or _ROOT))

    def _load_omit():
        return omit_record.read_omitted(_base)

    def _save_omit(s):
        omit_record.write_omitted(_base, s)

    # --off/--on <csv>: batch toggle, context-aware. A dev loading the plugin from
    # the repo directory curates via a symlink farm (.harness-dev/dev-off-skills.yaml
    # + dev_skill_farm) so the repo stays full; an installed copy toggles in-tree
    # (the --disable/--enable dir-omit below). Either way a RESTART applies it.
    off_csv = _split_csv(getattr(args, "off", None))
    on_csv = _split_csv(getattr(args, "on", None))
    if off_csv or on_csv:
        import dev_skill_farm as dsf
        if (_base / dsf._OFFLIST_REL).is_file():   # dev symlink-farm setup
            bad = dsf.validate_off(_base, off_csv)
            if bad:
                print("error: " + "; ".join(bad), file=sys.stderr)
                return 2
            dsf.toggle_record(_base, add=off_csv, remove=on_csv)
            dsf.build_farm(_base, _base / dsf._DEFAULT_FARM_REL, dsf.load_off_list(_base))
            if off_csv:
                print("off (dev farm): %s" % ", ".join(off_csv))
            if on_csv:
                print("on (dev farm): %s" % ", ".join(on_csv))
            print("→ restart Claude Code to apply (the plugin reloads on session start)")
            return 0
        # installed copy: fold into the in-tree dir-omit path below
        args.disable = (args.disable or []) + off_csv
        args.enable = (args.enable or []) + on_csv

    # --enable/--disable are repeatable, but the onboarding protocol documents a CSV
    # (`skills --enable <csv-of-cluster-skills>`); split each element so both the CSV
    # and the repeated-flag forms work (a CSV used to collapse into one bogus name).
    args.enable = _split_csv(getattr(args, "enable", None))
    args.disable = _split_csv(getattr(args, "disable", None))

    if args.disable:
        try:
            core = set(skill_deps.core_immutable(deps_path))
        except Exception:  # noqa: BLE001
            core = set()
        for s in args.disable:
            if s in core:
                print("error: %r is a spine core skill — never disabled" % s,
                      file=sys.stderr)
                return 2
        omit, present = _load_omit(), _present()
        still_on = present - set(args.disable)
        moved, problems = [], []
        for s in args.disable:
            src, dst = skills_dir / s, stash_dir / s
            if not src.is_dir():
                if s in omit:
                    print("skills: %r already disabled" % s, file=sys.stderr)
                else:
                    print("skills: %r is not an installed skill" % s, file=sys.stderr)
                    problems.append(s)
                continue
            if dst.exists():
                # never shutil.move into an existing stash dir — it NESTS the skill
                # (disabled-skills/<s>/<s>/) and corrupts the re-enable round-trip.
                print("error: stash already holds %r — refusing to nest; run "
                      "`hs-cli skills --enable %s` first" % (s, s), file=sys.stderr)
                problems.append(s)
                continue
            dependents = _dependents(s, deps_path, still_on)
            if dependents:
                print("warning: %r is a dep of still-enabled %s"
                      % (s, ", ".join(sorted(dependents))), file=sys.stderr)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            omit.add(s)
            # persist per-move, not only once after the loop: a crash mid-loop then
            # leaves at most the in-flight skill moved-but-unrecorded, instead of
            # every already-moved skill — so verify --strict cannot report broad
            # integrity drift for a skill the user merely disabled.
            _save_omit(omit)
            moved.append(s)
        _save_omit(omit)  # final write: also creates the record when nothing moved
        if moved:
            print("skills disabled: %s" % ", ".join(moved))
            print("→ restart Claude Code to apply (the plugin reloads on session start)")
        return 2 if problems else 0

    if args.enable:
        try:
            targets = skill_deps.resolve(args.enable, deps_path)
        except Exception:  # noqa: BLE001
            targets = set(args.enable)
        omit = _load_omit()
        restored, missing = [], []
        for s in sorted(targets):
            src, dst = stash_dir / s, skills_dir / s
            if dst.is_dir():
                omit.discard(s)  # already present
                continue
            if src.is_dir():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))
                omit.discard(s)
                restored.append(s)
            else:
                # an install-omitted skill has no stash to restore from — the dir
                # was never copied. Report the real recovery path, do NOT claim
                # success (the omit record is kept so the nudge still points here).
                missing.append(s)
                print("skills: no stashed copy of %r — re-run "
                      "`install --skills %s` to fetch it" % (s, s), file=sys.stderr)
        _save_omit(omit)
        if restored:
            print("skills enabled: %s" % ", ".join(restored))
            print("→ restart Claude Code to apply (the plugin reloads on session start)")
        return 2 if missing else 0

    # bare: list on/off state (dev-farm-aware — a skill still living in skills/ but
    # named in the dev off-list reads as off, not on; also covers stash + omit record)
    import disabled_skills
    off = disabled_skills.effective_disabled(disabled_skills.default_sources(_base))
    present = _present()
    for s in sorted(present - off):
        print("on   %s" % s)
    for s in sorted(off):
        print("off  %s" % s)
    return 0


def cmd_cleanup(args) -> int:
    """Re-run the safe orphan cleanup — the manual door for the prompt layer.

    Lists the classified buckets; --apply backs up + removes the safe layer.
    Modified files stay in the prompt layer unless explicitly promoted with
    --remove (the headless equivalent of the skill's Keep/Change)."""
    import cleanup_orphans
    base = (Path(args.root) if getattr(args, "root", None)
            else Path(os.environ.get("CLAUDE_PROJECT_DIR") or _ROOT)).resolve()
    # On a manual re-run no snapshot is passed — fall back to the durable one
    # install.sh persisted, so the deferred (modified) layer stays reachable.
    snap = args.old_manifest
    if not snap:
        persisted = base / "harness" / "state" / "cleanup-prev-manifest.json"
        if persisted.is_file():
            snap = str(persisted)
    old = cleanup_orphans._load_manifest(snap)
    plan = cleanup_orphans.plan_cleanup(base, old)

    for line in cleanup_orphans.render_plan(plan):
        print(line)

    # --dry-run is explicit intent and wins over a co-passed --apply.
    if not args.apply or args.dry_run:
        if plan["prompt"]:
            print("\n%d modified file(s) — re-run with --remove <path> to clean, or "
                  "use hs:cleanup to decide interactively" % len(plan["prompt"]))
        print("\n(dry-run — re-run with --apply to back up + remove the safe layer)")
        return 0

    # headless promotion: move named prompt-layer files into the remove layer.
    # An unmatched --remove is surfaced, never a silent no-op.
    for rel in (args.remove or []):
        if rel in plan["prompt"]:
            plan["prompt"].remove(rel)
            plan["remove"].append(rel)
        else:
            print("note: %r is not a prompt-layer path (typo, or already removed) "
                  "— nothing promoted" % rel)

    backup = (Path(args.backup_dir) if args.backup_dir
              else base / "harness" / "state" / "cleanup-backup")
    result = cleanup_orphans.apply_cleanup(plan, base, backup)
    if result["backup_dir"]:
        print("\nbacked up to %s; removed %d, unlinked %d"
              % (result["backup_dir"], len(result["removed"]), len(result["unlinked"])))
    else:
        print("\nnothing to clean up")
    return 0


def cmd_install(rest: list[str]) -> int:
    argv = [sys.executable, str(_ROOT / "harness/install/install.py")]
    return _run(argv + (rest or []))


# ----------------------------------------------------------------------- cli

def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="hs", description="harness operator CLI")
    sub = ap.add_subparsers(dest="verb", required=True)

    sub.add_parser("version", help="print harness version + kit digest").set_defaults(fn=cmd_version)
    sub.add_parser("doctor", help="health-check the install").set_defaults(fn=cmd_doctor)
    sub.add_parser("gates", help="report the resolved stage-gate posture").set_defaults(fn=cmd_gates)
    sub.add_parser("guards", help="report the resolved guard preset + protected branches").set_defaults(fn=cmd_guards)
    sub.add_parser("capabilities", help="emit registered hooks + stage gates as JSON "
                   "(read-only; tầng-2 discovery source)").set_defaults(fn=cmd_capabilities)
    sub.add_parser("list", help="list plugins, skills, and on/off state").set_defaults(fn=cmd_list)

    sub.add_parser("migrate", help="run the decomposition migrate engine "
                   "(args pass through to the engine)")

    c = sub.add_parser("components", help="enable/disable a group")
    c.add_argument("--enable", action="append", metavar="GROUP")
    c.add_argument("--disable", action="append", metavar="GROUP")
    c.add_argument("--policy-file", default=None)
    c.add_argument("--settings-file", default=None)
    c.add_argument("--hooks-file", default=None)
    c.add_argument("--state-file", default=None)
    c.add_argument("--root", default=None,
                   help="target repo root (default: CLAUDE_PROJECT_DIR or this repo)")
    c.add_argument("--force", action="store_true",
                   help="for a label group: disable even when skill dirs are git-tracked")
    c.set_defaults(fn=cmd_components)

    sk = sub.add_parser("skills",
                        help="enable/disable individual skills (dir-omit); bare lists state")
    sk.add_argument("--enable", action="append", metavar="SKILL",
                    help="restore a skill (and its deps) from the disabled stash")
    sk.add_argument("--disable", action="append", metavar="SKILL",
                    help="stash a skill dir + record the omission (core is refused)")
    sk.add_argument("--off", action="append", metavar="A,B,C",
                    help="turn skills OFF (comma-list, repeatable) — dev-farm setups "
                         "edit the off-list + rebuild the farm, installs dir-omit; restart applies")
    sk.add_argument("--on", action="append", metavar="A,B,C",
                    help="turn skills back ON (comma-list, repeatable); restart applies")
    sk.add_argument("--root", default=None,
                    help="target repo root (default: CLAUDE_PROJECT_DIR or this repo)")
    sk.set_defaults(fn=cmd_skills)

    cl = sub.add_parser("cleanup",
                        help="safely remove files an over-install left behind "
                             "(reuses the cleanup engine; --prune is the coarse path)")
    cl.add_argument("--root", default=None,
                    help="target repo root (default: CLAUDE_PROJECT_DIR or this repo)")
    cl.add_argument("--old-manifest",
                    help="pre-overwrite manifest snapshot (omit on a manual re-run)")
    cl.add_argument("--backup-dir", default=None,
                    help="backup root (default: <root>/harness/state/cleanup-backup)")
    cl.add_argument("--remove", action="append", metavar="PATH",
                    help="headless: promote a modified (prompt-layer) file to removal")
    cl.add_argument("--apply", action="store_true",
                    help="write (default: dry-run plan)")
    cl.add_argument("--dry-run", action="store_true",
                    help="explicit dry-run (the default when --apply is absent)")
    cl.set_defaults(fn=cmd_cleanup)

    sub.add_parser("install", help="install + pick which skills/groups to install "
                   "(args pass through to install.py)")

    tr = sub.add_parser("trust",
                        help="trust a repo so its rule shell-detectors may auto-fire "
                             "(TOFU; per-machine, recorded in ~/.harness/trust.json)")
    tr.add_argument("repo", nargs="?", default=".",
                    help="repo root to trust (default: current dir)")
    tr.add_argument("--list", action="store_true", help="list trusted repo roots")
    tr.set_defaults(fn=cmd_trust)

    return ap


# Verbs whose args are forwarded verbatim to the wrapped tool — intercepted
# before argparse so engine flags like --check are not swallowed by this parser.
_PASSTHROUGH = {"migrate", "install"}


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in _PASSTHROUGH:
        verb, rest = argv[0], argv[1:]
        if verb == "migrate":
            import migrate_decomposition as md
            return md.main(rest)
        return cmd_install(rest)
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
