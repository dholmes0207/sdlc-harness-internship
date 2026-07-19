---
name: hs:use
injectable: false
description: Run an install-disabled (off) skill from its stash + report; delegate discovery to hs:find-skills. Use when a skill is off.
argument-hint: "<skill> [args]"
allowed-tools: [Bash, Read, Grep, Glob, Skill]
metadata:
  compliance-tier: workflow
---

# hs:use — proxy to run an install-disabled skill

A fresh install turns a skill **off** by omitting its dir (only disable that works for a plugin skill). Off skill not deleted: dir stashed under `harness/plugins/hs/disabled-skills/<name>/`, recorded in the omit list. `hs:use` = controlled front door that **runs** an off skill from its stash without re-enabling first.

`hs:use` owns exactly one job: resolve a named target, run it (or redirect a live one). **Discovery — listing off skills, routing free-text purpose — NOT owned.** Belongs to `hs:find-skills`.

## Discovery is delegated (do not re-implement it)

- **MUST delegate** all discovery, listing, and purpose-routing to `hs:find-skills` — `hs:use` **NEVER** lists the catalog itself and **NEVER** re-derives a purpose→skill route. One owner for discovery means the off-skill catalog never forks.
- No argument, or free-text purpose instead of a skill name → hand off to `hs:find-skills` (tags off match `[OFF — gọi: /hs:use <name>]`), then run the name it returns.

## State source

State comes from one library — answer never drifts:

```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/disabled_skills.py --status <skill>   # live | disabled | unknown
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/disabled_skills.py --path  <skill>    # abs stash dir
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/disabled_skills.py --chain <skill>    # disabled deps, load order
```

## Run a named target (the one mechanism hs:use owns)

**MUST** resolve `disabled_skills.py --status <name>` FIRST — **NEVER** assume live vs off:

- **live** → **MUST delegate** to `/hs:<name>` and stop. **NEVER** paraphrase/reproduce a live skill's prose — forks a second copy that drifts.
- **disabled** → **MUST** read `--chain <name>` (off deps to load first, in order); read each dep's stashed `SKILL.md`, then target's stash `SKILL.md` (path from `--path`) + `references/`, perform its prose exactly, as if installed. After run, **MUST emit** demand so re-enable loop sees it:

  ```bash
  python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/emit_disabled_demand.py --skill hs:<name> --via proxy_run
  ```

  (Emit = fail-open telemetry, never blocks run. Live target emits NOTHING; demand loop is off-skills only.)
- **unknown** → hand off to `hs:find-skills` to locate right skill.

## Workflow

1. **Classify the argument** — bare/normalized skill name → run it (below); no name or free-text purpose → **delegate to `hs:find-skills`**, then run the name returned.
2. **Resolve state** via `disabled_skills.py --status` — never assume live vs off.
3. **Act**: live → delegate `/hs:<name>`; off → load `--chain` + stash prose, run it, emit demand.
4. **Report**: which skill, its state, and — for an off skill run — stash path read + `hs_cli.py skills --enable <name>` command if user wants it back permanently.

## Persistent on/off (toggle — batch)

Running an off skill is one-shot. To change what LOADS every session, toggle it. One command covers dev tree + installed copy (auto-detects):

```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/hs_cli.py skills --off drawio,vibe,shopify   # turn OFF (comma-list)
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/hs_cli.py skills --on  vibe                  # turn back ON
```

- **Dev setup** (plugin loaded from repo dir + `.harness-dev/dev-off-skills.yaml` off-list): `--off/--on` edits that list, rebuilds curated symlink farm — repo's `skills/` tree never touched.
- **Installed copy** (no off-list file): `--off/--on` dir-omit into `disabled-skills/` (same as `--disable/--enable`).
- 16-skill floor refused. **Toggle takes effect only after RESTART Claude Code** (plugin catalog loads at session start) — always tell user.

Only toggle when user ASKS to change what loads; to run an off skill once, use proxy above instead (no restart, no toggle).

## Boundaries

- **Discovery is not yours.** Listing/purpose-routing → `hs:find-skills`, always.
- **Live → delegate, never duplicate.** For a live target, `hs:use` only redirects to `/hs:<name>`; never reproduces the skill's prose.
- **Never toggle during a run-once.** Running an off skill via proxy NEVER moves dirs or edits off-list. Persistent on/off = explicit `--off/--on` toggle above — run ONLY when user asks to change what loads every session, always name the restart.
- **Never edit a skill file** (live or stashed) while running it.
- Do not fabricate a skill/capability that `hs:find-skills` does not show.
