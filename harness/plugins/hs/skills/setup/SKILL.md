---
name: hs:setup
injectable: false
description: "Configure this project's harness settings — terminal voice, guard/stage policy, output language — through the validated config CLIs. Use when onboarding a fresh install or changing a configuration decision. Reads config-reference.md and reminds about the session-restart need for env-bound guard/stage changes."
allowed-tools: [Bash, Read, Grep, Glob]
argument-hint: "[full | short | show | none] [--global]"
metadata:
  compliance-tier: workflow
---

# hs:setup — project posture configuration

Walks the user through the harness's tunable posture, writing choices via the validated config CLIs (never hand-edit YAML in-session). The authoritative index of every knob, its file, default, and env override is `docs/harness/config-reference.md` — read it before presenting options so the defaults you quote are correct.

For onboarding (fresh install) or later changes; re-invoking re-shows the menu.

## Scope — localized config is BIN-GLOBAL under a shared install (read FIRST)

**Self-host (bin == project — a plain dev checkout, `HARNESS_BIN_ROOT` unset):** writes land in your one repo, so "project posture" is exact — rest of this caveat is moot.

**Global / courier install (`HARNESS_BIN_ROOT` points at a shared engine):** the localized-config knobs — terminal voice, guard/stage policy, output language, agent-permissions/RBAC — are **BIN-GLOBAL by design**, NOT per-project.
Their CLIs resolve the target file relative to the shared engine (`$HARNESS_BIN_ROOT/harness/data/…`, off `__file__`), so a knob you write here reaches **EVERY project that shares this binary** — not confined to this repo's `.harness/` (only runtime STATE + trace are per-project).
Deliberate: agent-permissions/RBAC and guard policy ARE the cage, so a per-project writeable override would let an agent widen its own lanes with a Bash-write (the F3 hole). Consequences:
- Before writing ANY localized-config knob under a shared bin, SAY it is bin-global — the change hits every co-tenant project, not just this one — and get explicit intent (the same care `--global` demands).
- To scope ONE knob to just this project, do NOT write the shared file: wire an explicit `HARNESS_XXX` env override (`HARNESS_TERMINAL_VOICE`, `HARNESS_GUARD_POLICY`, `HARNESS_OUTPUT`, …) in this repo's `.claude/settings.local.json` pointing at a project-local file — the pattern the `.harness-dev/*` overrides and the committed `<root>/test-policy.yaml` tier-2 already use.
- If the shared bin was hardened read-only (`--harden-bin`), the CLIs CANNOT write it (write_guard blocks the `$HARNESS_BIN_ROOT/**` zone) — the per-project `HARNESS_XXX` override is then the ONLY way to change posture.

**`--global` governs the shared ENGINE-BINARY state (install / repoint / recipient bootstrap), separate from the config-file scope above.** setup never installs or repoints the engine itself — it assumes phase-7 recipient bootstrap already placed the shared binary.
If (and only if) the user passes `--global`, STOP and re-confirm with an `AskUserQuestion` before touching any shared-bin engine state: state plainly that it affects EVERY project sharing this binary, show what would change, and proceed only on an explicit yes. Treat a missing re-confirmation as a hard stop.

## Schema migration check (run before Layer-0)

Before Layer-0, check the LIVE terminal-voice file (resolved by `voice_prefs.py`: `$HARNESS_TERMINAL_VOICE`
→ `.harness-dev/terminal-voice.yaml` → shipped) for pre-split keys `output_style` / `detail_level` / `coding_level`, and clean them via a `voice_prefs.py --set` re-save. Full procedure (which file to grep, why a bare migrate dry-run lies, the per-key shim differences): **see `references/schema-migration.md`**. If no candidate carries a legacy key, skip silently and go to Layer-0.

## Layer-0 — archetype onboarding (run this first on a fresh install)

Before showing the per-knob menu, **verify the harness's Python dependencies are present**. Run:

```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/preflight_deps.py
```

- If it prints `preflight OK`, proceed.
- If it fails, it prints the exact `pip install …` command. Install the missing packages, then re-run the preflight command before continuing. Do not proceed with onboarding while deps are missing — later hooks and tests will fail with opaque `ImportError` messages otherwise.

Then present the 10 archetypes from `harness/data/voice-presets.yaml` as a numbered list and ask "which best describes you?". The user enters a number or picks "I'll configure manually" to skip to Layer-1. After applying a preset,
**always ask "adjust anything? [keep / fine-tune]"** — preset is a seed, not a lock (the refinement path is mandatory).

Load and display presets with:
```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/voice_presets.py list
```

Apply a preset (all-or-nothing — validates all axes before writing either file):
```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/voice_presets.py apply <number>
```

After applying, proceed to Layer-0.6, then Layer-1 if the user says "fine-tune".

## Layer-0.6 — pick a character (persona bundle → delegate to `/hs:voice`)

A **persona bundle** gives the session a named CHARACTER — opt-in, OFF by default. On a fresh onboard OFFER it, then **delegate to `/hs:voice`** (it owns the character + RELATIONSHIP interview; do NOT re-implement here).
Order matters — run AFTER the preset, since a preset CLEARS an active bundle. Bin-global like every localized knob above: it sets the character for EVERY project of this user, so say so first.

## Layer-0.5 — personal-first is the default

The harness is **personal-first**: local never blocks the HUMAN at commit/push/ship — it generates receipts (plan-graph / verification / review-decision) and traces; quality is enforced at REMOTE CI. The AGENT cage stays (guard `enforcement=block` + the safety floors). There is no solo-vs-team posture question and no reviewer roster to configure.

## Layer-0.7 — skill onboarding (fresh default-off install)

A fresh install ships **default-off** (~38 ON, rest stashed, reachable via `/hs:use`). Surface the split, offer to re-enable whole clusters (`AskUserQuestion` over `skill-defaults.yaml` `clusters:` → `hs_cli.py skills --enable <csv>`), never auto-enable.
Protocol: `references/skill-onboarding.md`.

## Preamble — pick the depth (present in the project's output language)

Present these four options first and wait for the choice (resolve `language` + `audience` via `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/output_config.py --resolved`, per `harness/rules/output-rendering.md` — at `audience` 0–1, plain with "so what" opener; at 5, maximum density; evidence tokens unchanged at every level; apply the humanizer only when `humanize` is true, default off):

1. **Full** — walk every group (voice, guard/stage, roster, output language, DoD/test-policy) with current value + default, running the per-step coherence lint after each group and the consolidated coherence pass at the end (see "Coherence lint").
2. **Short** — only the three that matter most on day one: review-policy profile (default/thorough/ship-grade), guard preset, output language.
3. **Show meanings + defaults** — read `config-reference.md` back to the user, change nothing.
4. **None** — exit without changes.

## The groups and their CLIs

Quote the CURRENT resolved value before asking for a new one (run the read form, no `--set`).

**Path form (courier):** the `harness/scripts/*.py` tools and `harness/data/*.yaml` files below are named by their engine-relative path. Under a courier/global install the engine is NOT at `./harness`, so run/read them through the env:
`python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/<tool>.py …` (a bare `harness/…` resolves from the repo CWD and misses; the `${…:-.}` fallback keeps a self-host clone running from `.`).

| Group | Knobs | Tool | Takes effect |
|---|---|---|---|
| Terminal voice | persona, voice_level, terminal_voice_level, no_markdown, interview_rigor, action_prompting | `harness/scripts/voice_prefs.py --set k=v` (or `/hs:voice`) | **live** — no restart |
| Output prose | language (en\|vi), humanize, audience (off\|0–5), code_style (off\|0–5) | `harness/scripts/output_config.py --set k=v` | live — read per invocation |
| Cook execution | parallel (bool), parallel_max (int>0) | `harness/scripts/cook_config.py --set k=v` | live — read per cook run (chain: `--parallel` flag > `HARNESS_COOK_PARALLEL` > this file) |
| Critique | mode (advisory\|gate) | `harness/scripts/critique_config.py --set mode=v` | live — read per critique run; **mode=gate writes a verdict but does NOT enforce** until a stage lists `critique-consensus` in its `requires:` |
| Guard policy | preset (strict\|balanced\|lenient), per-guard off\|warn\|block | `harness/scripts/guard_config.py set-preset <p>` / `set <guard> <mode>` | **restart** (env-bound) |
| Stage policy | per-stage artifact requirements / hard | edit `harness/data/stage-policy.yaml` (hand edit; git-visible) | **restart** (env-bound) |
| Hook components | runtime on/off for hook-backed features (rbac, decision-capture, docs-ssot, …) | `harness/scripts/component_config.py --set <name>=enabled\|disabled` (read: `show` / `list`) | **live** (hook self-skips on the flag) |
| Gemini partner lane | master, mode, write, stop_review_gate, `route_all_injection`; purposes→tier; `loop` {max_rounds, default_mode} | hand-edit `harness/data/gemini-partner.yaml` (dev override `$HARNESS_GEMINI_PARTNER`) | **restart** (env-bound) |

**Gemini partner lane (opt-in, ships OFF).** Inert while `master: off` — leave it unless the user wants a second-engine pass.
Blast-radius axes to call out before enabling: `write: sandbox_write` (gemini edits a worktree) and `route_all_injection: on` (AUTO-sends whole skill methodology + task to Google; `secret_scrub` is warn-only; needs `mode: route-all`, forbids `sandbox_write`). The `injectable` allowlist is a separate one-time step: `injectable_bootstrap.py --propose` → ratify → `--apply` (never blind).

`code_style` (in output.yaml, not the voice file) is the one output knob **NOT scope-fenced** — shapes generated CODE only, not chat/report prose (that is `audience`); profiles in `harness/data/output-styles/`. Treat a non-off value as a deliberate save.

**Advisory nudges / gates — discover live, do NOT trust a static list.** The harness ships many toggleable nudge/gate hooks across THREE control planes (class-default · `harness-hooks.yaml` `enabled` · guard-policy mode), and a repo's `settings.json` can be stale — a hook shipped on disk but not wired never fires. Before surfacing nudges in Full mode, READ the live state (registration spec +
`harness-hooks.yaml` + `guard_config.py show` + `settings.json` + the hook dir) and present what actually exists/fires, flagging NOT-WIRED and class-default (implicit-state) hooks. Method + cross-reference script + the control model + drift checks: **see `references/hook-discovery.md`**.

Nudges in `harness-hooks.yaml` are NOT `--set` knobs — flip `<nudge>: {enabled: true|false}` by hand (git-visible). Dev-only enablement uses `.harness-dev/harness-hooks.yaml` + `HARNESS_HOOK_CONFIG` (gitignored, scrubbed at push, restart to bind). The goal-cycle nudge pairs with the cycle-memory convention at `harness/plugins/hs/skills/goal/references/cycle-convention.md`.

`component_config.py` governs **hook components** only — features backed by a hook self-skip on an `enabled:false` flag (live, no restart). It does NOT add or remove SKILLS. Post-collapse every skill ships in the one `hs` plugin; a skill is on by being present on disk and off by install-omission, and is toggled after install with `hs-cli skills --enable/--disable <skill>` (restart to re-index
— see the restart reminder). So "turn on viz/uiux/devops" is a **skill** action (`hs-cli skills` / install selection), NOT a component toggle — do not route it through `component_config`.

Read forms (no write): `voice_prefs.py`, `output_config.py --file <path>`, `guard_config.py show`.

## Restart reminder — say this out loud

Guard and stage policy are **env-bound**, so they only take effect on a **new session**; voice and output language are live. Full details, the `/clear` vs full-restart distinction, and the plugin-re-enable case: **see `references/restart-reminder.md`**. After changing guard or stage, say out loud:

> Guard/stage changed — restart the session (or open a new terminal) for it to take effect.

## Trust this repo for shell-detector auto-fire

A standards rule may carry a **shell detector** — an arbitrary command the review-time runner executes. `/hs:setup` is the deliberate "this repo is mine" moment, so it is the one place that grants trust. Full procedure, revoke path, and why a fresh clone stays grep-only by default: **see `references/trust-repo.md`**.

Run the trust step in the open (never silently):

```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/hs_cli.py trust "$(pwd)"
```

Mention how to inspect or undo it: `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/hs_cli.py trust --list`.

## Boundaries

- Always write through the CLIs (`*_config.py --set`, `voice_prefs.py --set`, `guard_config.py`) so validation runs and the change is a clean git diff. Do not hand-edit the YAML from inside the session.
- An unknown key or out-of-range value exits non-zero and writes nothing — report the failure, do not retry blindly.
- Setting `voice_level` 6–9 is a deliberate save; there is no second prompt. The universal-harm floor in `harness/rules/terminal-voice.md` is what holds at every level.
- This skill changes CONFIG, never code or evidence. Gate decisions still follow the written files.

## Review and test gate config (4 tiers — surface during Full or when asked)

The MAIN tier (review-policy, code-review, test-policy/DoD) affects the ship gate — surface it on day one. The DoD walk is posture-aware (solo-lean vs team-grade) and has THREE override surfaces with different reach: tier-1 (shipped default), tier-2 (`<root>/test-policy.yaml`, committed, grace-gated), and `.harness-dev/test-policy.yaml` via `HARNESS_TEST_POLICY` env (gitignored, scrubbed at
push — the guard/stage pattern). EDGE / SHOW-warn (RBAC fences) / INTERNAL tiers follow. Full tier tables, the DoD walk, and the override-surface decision: **see `references/gate-config.md`**. Write-RBAC is a deny-list: a `workflow-subagent` edits project source out of the box (no lane setup), refused only the hard floor. Retired overlay recipe: `references/workflow-fix-overlay.md`.

## Coherence lint — warn on a config that fights itself (per-step + final)

After each group AND once at the end (Full mode), check the written config for internal incoherence or a mismatch to who the user is — too strict/loose for solo/team, prose register vs code/voice expertise split, voice asking for challenge but rigor set light. Warn and ASK, never silently correct; user decisions hold. Full check list + framing: **see `references/coherence-lint.md`**.

## Related skills

- `hs:techstack`: read-only stack detection — run first on a non-Python repo so posture matches the real test command.
- `hs:rule-author`: for authoring new compliance standards or shell-detector rules — invoked from setup's TIER 3 SHOW step when the user wants to change `standards.yaml`.
