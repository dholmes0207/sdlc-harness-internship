<!-- >>> harness onboarding (generated; edits between markers are overwritten on reinstall) >>> -->

## SDLC harness

This repo runs a file-based **SDLC harness** for Claude Code, vendored self-contained under `harness/`.

- **Probe before you build on a guess** (the load-bearing habit) — when a load-bearing assumption CAN be checked empirically (spike a thin slice, run the real tool once, read the source/docs, web-search), do that FIRST, before you design or build on top of it. A real check is cheaper and firmer than predicting-then-building, or verifying in circles without ever running the thing. A claim you have not exercised for real is `[ASSUMED]` (training knowledge you have not re-checked is `[PRIOR]`), never OBSERVED: label it with its honest type and gate it behind one real-run step — never report "works" from reasoning alone.
- **Skills** — drive the workflow with `/hs:<name>` (e.g. `/hs:plan`, `/hs:cook`, `/hs:test`, `/hs:ship`, `/hs:review-pr`). `/hs:find-skills` lists the full catalog.
- **Off skills** — a fresh install ships DEFAULT-OFF: only a recommended subset loads; the rest are stashed under `harness/plugins/hs/disabled-skills/<name>/` (present in the bundle, not deleted). `/hs:find-skills --list` shows them tagged `[OFF]`; `python3 harness/scripts/disabled_skills.py --status <name>` reports live|disabled|unknown. Run an off skill with `/hs:use <name>` (not the raw `/hs:<name>`); enable one for every session with `hs-cli skills --on <name>` (restart to apply). An off reference is a normal state, not a broken link — do not go hunting for a 'missing' skill.
- **Rules** — shared conventions load on demand from `harness/rules/` (routing in this file's project section, or ask a skill).
- **Hooks** — gates/telemetry are wired in `.claude/settings.json`; config knobs live in `harness/data/*.yaml` and `harness/hooks/*.yaml`. Run `/hs:setup` to configure (voice, guard/stage policy, output language).
- **State** — runtime telemetry/state is written under `harness/state/` (gitignored; never commit it).

<!-- <<< harness <<< -->
