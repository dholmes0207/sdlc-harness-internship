---
name: hs:find-skills
injectable: true
description: Locate and route to the correct hs:* skill — analyze intent, query the hs plugin registry, return the exact invoke command. Use when unsure which skill fits or to browse the full hs:* catalog.
argument-hint: "[task description] | --list | --stage <sdlc>"
allowed-tools: [Bash, Read, Grep, Glob]
metadata:
  compliance-tier: workflow
---

# hs:find-skills — skill discovery and routing

Analyze the user's task → query the hs plugin catalog → return the matching skill with its invoke command. Does not write code or modify files.

**Registry wiring**: catalog loaded via `harness/scripts/catalog.py` (`load_catalog()`) — reads `harness/plugins/hs/skills/<dir>/SKILL.md` frontmatter `name:` for every skill dir under the single `hs` plugin. A skill must have an existing `SKILL.md` to be available.

## Modes / flags

| Argument | When to use |
|---|---|
| _(task description)_ | route to the most suitable skill |
| `--list` | list all hs:* skills — live, plus off skills tagged `[OFF]` |
| `--stage <sdlc>` | filter by SDLC stage (plan/build/verify/ship/doc/meta) |

No argument → `AskUserQuestion`: what are you trying to do?

## Disabled (off) skills — find-skills OWNS this discovery

A fresh install can turn a skill **off** by omitting its dir; it still exists, stashed under `harness/plugins/hs/disabled-skills/<name>/`. `hs:find-skills` is the single owner of off-skill discovery — `hs:use` delegates listing and routing here. Fold off skills into every answer:

- **Catalog step** — `--list` and every purpose-route **MUST** merge `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/disabled_skills.py --list` alongside the live catalog, so an off skill is never invisible to a search.
- **`--list` render** — each off match **MUST** be tagged `[OFF — gọi: /hs:use <name>]`, carrying the exact command to reach it, plus its stash path from `disabled_skills.py --path <name>`.
- **Purpose route** — when the best-fit skill for the user's intent is off, **MUST** propose `/hs:use <name>`. **NEVER** propose a raw `/hs:<name>` call for an off skill — the raw invoke is blocked/absent; only the `hs:use` proxy loads the off skill (and its off deps) from the stash and runs its prose without re-enabling it.

## Workflow

1. **Parse intent** — identify task type: planning, implement, debug, review, deploy, or meta (skill/project management).

2. **Query routing map** — load `references/hs-routing-map.md`; match SDLC stage + intent to a skill. If 2+ skills match: return the primary skill plus a supporting skill and explain the division of responsibilities.

3. **Verify existence** — call `load_catalog()` from `catalog.py` (or `ls harness/plugins/hs/skills/`) to confirm the skill's directory and SKILL.md exist. An opt-in skill (e.g. excalidraw) lives in the same catalog — invoke under the `hs:` prefix. **Do not propose phantom skills.**

4. **Return result** — format:
   ```
   Suggested skill: /hs:<name>
   Purpose: <one sentence>
   Invoke: /hs:<name> [args if any]
   Supporting skill (if needed): /hs:<name2>
   ```

5. **Gap report** — if no skill matches: clearly state "No skill for [intent]"; suggest a workaround using the nearest skill or native Claude tools. Do not fabricate skills.

## HARD-GATE (actual wiring)

- **Catalog**: `harness/scripts/catalog.py` `load_catalog()` — the sole authority for confirming a skill exists. `owned` set = directories whose `name:` is in the hs plugin (every skill is `hs:*`).
- **Registry root**: `harness/plugins/hs/skills/` — the single hs plugin's skills; only directories with `SKILL.md` are counted as available (catalog.py invariant).
- **Phantom guard**: a skill without `SKILL.md` → listed as "not yet ported" (directory exists but file is missing) or "does not exist" (directory also absent).

## Boundaries

- Do NOT write code. Do NOT modify files.
- Route only to `hs:*` skills. Do not suggest skills from other plugins.
- Do not self-invoke the chosen skill — the user decides when to invoke.
- End with: full invoke command + the SDLC stage the skill serves.
