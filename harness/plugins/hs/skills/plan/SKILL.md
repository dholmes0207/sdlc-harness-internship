---
name: hs:plan
injectable: false
description: Create a verified implementation plan — research, constraint-scan, phase design, red-team, and validate before cook. Use when a real feature or refactor needs a verified plan before any code is written.
argument-hint: "[--fast | --hard] [--tdd] [--deep] [--parallel] [--in-place]"
allowed-tools: [Bash, Read, Write, Edit, Grep, Glob, Task]
metadata:
  compliance-tier: workflow
---

# hs:plan — verified planning

Creates a plan under `plans/<timestamp>-<slug>/` (plan.md + phase files). The plan is the contract for hs:cook: cook runs only when the plan has been approved by a HUMAN.

**General rules** (evidence, two-way Evidence Filter, posture gate) are in `harness/rules/verification-mechanism.md` — read that first; not repeated here.

**Probe-first ★** (`harness/rules/agent-operational-discipline.md` — the priority discipline): the *probe-first empirical checks* bookend held at MAIN (table below) IS this rule — a load-bearing assumption the plan rests on that CAN be checked empirically gets RUN before the plan builds on it, not deferred to cook. A doc / `--help` / grep / chain of reasoning is a *hypothesis*,
NOT a probe; an unrun claim is `[ASSUMED]` (`[PRIOR]` if training knowledge), never OBSERVED.

**Review gate**: every gate requiring HUMAN approval (red-team, validate, plan approval) applies `harness/rules/plannotator-review-gates.md` — offer the direct Review option (Plannotator) before asking the user to read manually.

**Plan-methodology rules** (LAW-embedded at their exact step below, not restated here):
`harness/rules/intake-and-interview-discipline.md`,
`harness/rules/plain-language-phrasing.md`, `harness/rules/testability-triad.md`,
`harness/rules/scope-and-contract-discipline.md`, and (should-tier)
`harness/rules/plan-quality-goodhart-premortem.md`.

## Who does what — main vs subagent (read this before the Steps)

On `--hard`, **delegate by default**. `--in-place` keeps the delegated middle (write-phase, red-team) at main; `--fast` skips research + red-team outright (the small / low-risk lane).

| Held at MAIN — never delegated | Delegated to a subagent |
|---|---|
| the interview bookends — understand · scope-challenge · validate · **approval** (they call AskUserQuestion, which dies with no TTY) · probe-first empirical checks · constraint-scan · the final consistency sweep · recording the approval | `@researcher` (Step 3) · `@planner` — writes plan.md + phase files (Step 5) · `@red-teamer` — attacks the plan (Step 6) |

A subagent has no TTY, so any step that must ask the user stays at main. This table is the split; the Steps below carry the mechanics. On `--hard`, do NOT hand-author plan.md/phase files at main — that is `@planner`'s job.

**Hard routes (MUST — not see-also, easy to miss inside Step 5's detail):** a design decision that must satisfy ≥2 conflicting hard constraints → route to `hs:sequential-thinking` FIRST (Step 5); a wide research or `--parallel` fan-out → route to `hs:workflow-orchestrate` FIRST (Steps 3/5).

## Step 0 — Standards are input

Read the shared standards BEFORE planning: `docs/system-architecture.md` +
`docs/code-standards.md` supplied by the user when cloning the harness. The plan must
reference shared standards, not invent its own. Standards missing -> STOP, prompt the
user to load them first (`harness/standards/README.md`). Do not plan on an empty base.

Also read the harness's canonical shared language — the SSOT is `docs/glossary.yaml`
(read it directly, or `glossary_register.py --root . --list` for JSON); `docs/GLOSSARY.md`
is the generated VIEW, do not treat it as the source. Name things with the settled
vocabulary instead of re-coining it; respect its forbidden wording.

Read `harness/LESSONS.md` as preflight input: let a known past failure mode shape
the plan instead of being rediscovered phase by phase.

## Modes

| Mode | When | Gates |
|---|---|---|
| `--fast` | small task, 1-2 files, low risk | skip research + red-team |
| `--hard` (default) | real feature/refactor | constraint-scan -> red-team -> validate |

**Orthogonal flags** (combine with either mode):

- `--tdd`: each phase records the test-first->implement-after pair explicitly (`harness/rules/tdd-discipline.md`).
- `--deep`: per-phase scout — each phase file gains a file-inventory table, a test-scenario matrix, and a dependency map (`references/phase-decomposition.md` Deep mode; TDD variant in `references/tdd-plan-mode.md`).
- `--parallel`: emit a dependency matrix + file-ownership table in plan.md so cook can fan independent phases out concurrently (`references/phase-decomposition.md` Parallel mode).
- `--in-place`: stay on `--hard` gates but keep plan-writing (Step 5) and red-team (Step 6) **at main** — no `@planner`/`@red-teamer` spawn — for a plan small enough that the delegation overhead isn't worth it, or when the subagent lane is unavailable. Bookends are at main regardless; this flag only moves the delegated middle back inline.

**Scope-driven default**: unless the user explicitly typed a mode or override,
assess the scope after Step 1 and default to `--hard` + `--tdd`. Only switch to
`--fast` or omit `--tdd` when the exception is clear: 1–2 files, no logic change,
no new tests needed, or the user asked for a lighter mode. The default honors
the red→green discipline in `harness/rules/tdd-discipline.md`.

## Workflow (hard)

1. **Understand**: read the request, docs, and related code; identify the real scope
   (cut YAGNI). Apply `harness/rules/intake-and-interview-discipline.md`:
   `interview_rigor` sets HOW HARD to probe, never whether — the 5-fact floor below is
   INVARIANT regardless of band. Before decomposing you MUST be able to state each in
   one concrete sentence (use AskUserQuestion, phrased per
   `harness/rules/plain-language-phrasing.md`, to pin any that stay vague): **expected
   output** (the artifact the user sees — path/behavior/endpoint+payload/CLI+flags),
   **acceptance criteria** (inputs→outputs/edge cases that mean "done"), **scope
   boundary** (what is explicitly OUT this round — an empty answer is a push-back
   trigger, `harness/rules/scope-and-contract-discipline.md`), **non-negotiable
   constraints** (stack, locations, naming, back-compat, perf), **touchpoints** (which
   existing files/contracts get modified — ground options in real paths).

   **Complexity hint (advisory cross-check)**: after reading the goal, print one line —
   `complexity: simple|standard|complex · ~N phases · risk: <area>`. Treat it as a
   **cross-check** against the mode the user typed (`--fast`/`--hard`), not a decision. If the
   hint **agrees** with the mode, stay silent and use that mode. If the hint **disagrees**
   (e.g. `--fast` but `complexity=complex`): in `interactive` mode, confirm with
   `AskUserQuestion` (recommended option first); in `headless`/autonomous mode (no TTY),
   record one line in `## Validation Log`
   (`VL-n | hint=complex but mode=--fast | keep user-chosen mode | <reason>`) and **continue
   with the mode the user chose**. The hint is **advisory only** — it never **auto-routes**
   gate depth and never changes the mode on its own (the agent stays caged).
2. **Scope challenge** (skip when `--fast` or task < 20 words and unambiguous): ask
   the user 3 questions — what existing code can be reused? what is the minimal
   change set? does the plan touch >8 files / >2 new classes / >3 phases? ->
   choose EXPANSION / HOLD / REDUCTION via AskUserQuestion before research.
3. **Research** (skip when `--fast` or a researcher report already exists): load
   `references/research-phase.md` — spawn <=2 `@researcher` agents in parallel,
   read `docs/system-architecture.md` + `docs/code-standards.md` first; synthesize findings into `plans/<slug>/research/`.
   When the research fans out to more than two agents, or `--parallel` will drive a
   wide phase fan-out at cook, route through `hs:workflow-orchestrate` first to size
   and group the spawn (subagents vs Workflow vs Agent Teams) instead of ad-hoc spawning.
4. **Constraint scan** (required BEFORE finalizing any open decision): load
   `references/constraint-scan.md` — grep `ownership.yaml`, `stage-policy.yaml`,
   `schemas/` to find zone/policy constraints that govern the decision.
   Also load `harness/rules/architectural-constraints.md` — model opacity + presence
   gate blindness, the two invariants that ground WHY the plan's verification design
   must rest on external, re-derivable evidence rather than a model's self-report.
5. **Plan**: load `references/phase-decomposition.md` — write plan.md (YAML
   frontmatter + phases + acceptance + rollback), classifying each AC per
   `harness/rules/testability-triad.md` (test/invariant/manual), + phase files
   sufficient for another developer to execute. **Stamp the plan dir with `scaffold.py plan` — do NOT hand-author the layout.**
   **If a discovery brief exists for this work, scaffold INTO its folder** (`plans/<id>-<slug>/discovery-brief.md`) by passing that dir's `--id`/`--slug` — do NOT let `--id` default to a fresh timestamp and orphan the brief in a sibling folder (`references/phase-decomposition.md`). Phase files MUST land
   at `phases/phase-N-<name>.md`; that exact path is what `plan_approval` folds into the approval hash, so a phase file placed
   anywhere else is silently OUT of drift detection. Never invent a plan-dir shape. For a non-trivial design, first shape the approach with
   `references/solution-design.md` (solution-design walkthrough); a change touching an
   existing contract records the delta per `harness/rules/scope-and-contract-discipline.md`
   (before/after/who-affected/migration-path). **When that design
   decision must satisfy >= 2 hard constraints SIMULTANEOUSLY that trade off against each
   other** (e.g. perf vs back-compat vs a fixed schema — cannot be reasoned in one linear
   pass), **you MUST route to `hs:sequential-thinking` FIRST** for an externalized,
   revisable Thought trace (parallel-constraint satisfaction) before locking the decision:
   a wrong multi-constraint call here propagates into every downstream phase. Hard route,
   not a see-also. **ALWAYS emit the `plan-graph.yaml` sidecar** next to plan.md — mandatory for every plan, no exception for phase
   count or mode; the shape/`post:` obligation/exit-2 hard-fail rule lives in `references/phase-decomposition.md` (Machine-readable phase-DAG sidecar). Flag `--tdd`: load
   `references/tdd-plan-mode.md` — add Tests Before / Implement / Tests After /
   Regression Gate to each phase. Flag `--deep`: load `references/phase-decomposition.md`
   (Deep mode) — add a file-inventory table, test-scenario matrix, and dependency map to
   each phase file. Flag `--parallel`: load `references/phase-decomposition.md`
   (Parallel mode) — add the dependency matrix + file-ownership table cook reads to fan
   phases out. Planner self-verifies inline: tag `[ASSUMED]`/`[PRIOR]` on
   every claim without a `file:line` anchor (rule `verification-mechanism.md` — two-way
   Evidence Filter).
   **Delegation (delegate-by-default on `--hard` — see the "Who does what" table up top):**
   hand plan.md/phase writing to `@planner`; the interview bookends (understand · scope-challenge ·
   validate · approval) STAY at main — a subagent has no TTY so AskUserQuestion dies there.
   `--in-place`/`--fast` keep plan-writing at main (no `@planner`).
   Details in `references/phase-decomposition.md` (delegate phase-writing; bookends at main).
6. **Red-team** (skipped entirely on `--fast` — see the Modes table): BEFORE spawning
   `@red-teamer`, run the Goodhart check + 6-lens pre-mortem per
   `harness/rules/plan-quality-goodhart-premortem.md` (should-tier; findings feed
   `risks:` frontmatter, C/H/M/L scale). Then load `references/red-team-gate.md` —
   spawn `@red-teamer` by default (an adversarial reviewer that finds failure modes);
   `--in-place` falls back to an inline persona pass. After applying findings: load
   `references/verification-roles.md` -> Whole-Plan Consistency Sweep.
7. **Validate**: load `references/validate-gate.md` — finalize self-assumed decisions
   + resolve `[ASSUMED]`/`[PRIOR]` tags. Verification pass (tier Light/Standard/Full by phase
   count) runs before asking; load `references/verification-roles.md` for roles. Read the
   knobs from `skill-config.yaml` via `skill_config.py --resolved`
   (`plan.validation.{mode,minQuestions,maxQuestions,focusAreas}`): `mode` `prompt` asks,
   `auto`/`strict`/`none` auto/require-all/skip; the bounds + focus areas shape the
   questions. Then Whole-Plan Consistency Sweep again.
   Run `plan_graph.py` over the (now-mandatory) `plan-graph.yaml` sidecar —
   cycle + ordering-hazard + parallel-batch + shared-file-conflict checks. This is
   **detection only**: report the findings to the planner, never auto-edit the plan.
   A shared-file conflict between same-batch phases is exactly what this catches; add
   a serializing edge (or split ownership) and re-run until clean before approval.
   Also run `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/plan_layout_check.py --plan <plan-dir>` (soft advisory, exit 0) — it flags a phase file placed
   outside `phases/` (silently OUT of the approval hash). Any warning → move the file under `phases/` before approval.
8. **Consistency sweep**: re-read plan.md + ALL phase files; scan for stale
   terms/reversed decisions + **name-honesty/SRP** (do new file/module names
   accurately and completely describe their real responsibility?). If the plan
   coins a new load-bearing term, register it via
   `glossary_register.py --root . --add` (or edit the `docs/glossary.yaml` SSOT) — do
   NOT hand-edit the generated `docs/GLOSSARY.md` view — so the shared language grows
   with the work. **0 unresolved contradictions** before recommending cook.

## Boundaries

- Do NOT write code, do not modify files outside `plans/`.
- Archiving a plan is a separate end-of-lifecycle action (post-cook), NOT part of authoring — to archive a **finished** plan see `references/archive-workflow.md` (it journals + `rm -rf`s the plan dir; never fire it on a plan you just created).
- Architecture decisions finalized during validate -> record DEC via
  `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/decision_register.py --append-alloc ...` — the register
  kills re-litigation: when old tension resurfaces, read the register first.
- Finish: return the **ABSOLUTE PATH** of the plan + recommend next step (additional
  validate / hs:cook / stop). Plan approval belongs to the HUMAN — autonomy at every
  level stops here. Ask for approval via AskUserQuestion with 3 options [Direct Review
  (Plannotator) / Approve / Reject] (rule `plannotator-review-gates.md`);
  "approved" -> record via `plan_approval.py`. **Required every call: `--plan`, `--verdict`, `--rationale`; `--author` is CONDITIONAL** —
  auto-resolved from a `plan_created` trace event or the plan.md `author:` frontmatter (scaffold stamps it at creation, so a scaffolded plan needs no `--author`); pass `--author user:<id>` only for an old plan lacking that line. Source it from plan.md `author:` or the `HARNESS_USER` identity. See the CLI `--help`.
- **Context isolation before cook**: after approval, recommend the user run
  `/clear` to isolate planning context, then run `/hs:cook <absolute-path-to-plan.md>`
  **carrying the same flags the plan was built with**. Flag propagation is mechanical,
  not a fresh decision — the scope/mode was already locked during planning:
  - **`--tdd` mirrors the plan.** The plan was built `--tdd` (the default) → the cook
    command MUST carry `--tdd`, e.g. `/hs:cook <absolute-path-to-plan.md> --tdd`. Only a
    `--fast`/no-test plan drops it. (`--hard`/`--fast`/`--deep` are plan-only and are NOT
    passed to cook.)
  - **`--parallel` is a dual recommendation whenever the plan is parallel-capable**
    (it emitted the dependency matrix + file-ownership table). Do NOT silently pick one
    side — print BOTH the parallel and the sequential cook command, then add a one-line
    risk read that recommends which to run:
    - If the parallel-safe phases have disjoint ownership and touch no core/shared
      surface → recommend the parallel line (saves wall-clock; the integration barrier
      still runs the full suite serially).
    - If any parallel batch touches core, a shared config, a migration, or a hot module
      → recommend sequential for safety, but STILL print the parallel line so the user
      can override with eyes open (e.g. *"phases 2+3 both touch the core resolver — run
      sequential; parallel command shown in case you split ownership first"*).
    Example pair:
    `/hs:cook <plan.md> --tdd --parallel`  ·  `/hs:cook <plan.md> --tdd`  → *recommend: <one>*.
    A plan with no dependency matrix is sequential-only — omit the parallel line entirely.
  The `/clear` should happen AFTER the user has noted the cook command. Write every
  command as **inline code** (single backticks), NOT a fenced code block — the terminal
  highlights inline `/...` commands but renders a ``` fence flat/uncolored.

## Gate wiring (personal-first: generate local, enforce remote)

On stage `push|pr|ship|deploy` `harness/hooks/gate_stage.py` ADVISES (not blocks) when
an active plan is missing its artifact (`require_plan`, `harness/data/stage-policy.yaml`)
— an `[advisory]` line + `gate_advisory` trace, command proceeds. The hard enforcement
is the remote receipts-gate. Still a presence gate (rule verification-mechanism).

## Interview rigor (voice knobs)

Read three knobs from `harness/data/terminal-voice.yaml` (resolved by `voice_prefs.py`, injected at
session start) and let them shape the interview, not the artifact:

- `interview_rigor` (light | standard | **deep**) — at `deep`, challenge claims harder and probe
  more gaps / edge-cases / acceptance-criteria holes in the scope-challenge + validate steps; at
  `light`, ask only the blocking questions.
- `action_prompting` (minimal | standard | proactive) — at `proactive`, offer more next-step
  suggestions at turn boundaries.
- `terminal_voice_level` (0–5) — sizes interview prose + follow-up count too (turn verbosity;
  the former `detail_level` folds in here). Render reports per `harness/rules/output-rendering.md`: resolve `language` / `audience` / `humanize` live via `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/output_config.py --resolved` (never hand-read the tracked file); the rule holds the register behavior and the evidence-invariant fence.

## Observe checkpoint (end-of-work)

When the plan is done, if this run surfaced a judgment a counter cannot see, record ONE
closed-vocab signal so the harness learns from it — emit only a REAL observation, not every
run. Vocabulary lives in `harness/data/observation-signals.yaml`.

```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/emit_observation.py --skill hs:plan \
    --signal <thin-evidence|red-team-reopened|plan-revised-post-approval|trigger-near-miss> \
    --payload "<one line: what happened>"
```

Surfaces in the read-only `observations` lens (honesty-gated). Skip it silently when nothing
notable happened — a fabricated signal is worse than none.
