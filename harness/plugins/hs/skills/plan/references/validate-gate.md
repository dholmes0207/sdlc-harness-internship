# Validate gate — critical-questions interview (on-demand)

Runs AFTER red-team. Goal: every decision the plan is currently SELF-ASSUMING must be explicitly finalized by the user before cook invests effort in the wrong direction.

## Mode (taken from `## Plan Context`, default is interactive)

| Mode | Behavior |
|---|---|
| `interactive` (default) | ask the user via AskUserQuestion (see Asking rules) |
| `headless` | Do NOT ask (subagent/headless has no TTY): EMIT a table "Validation Questions + suggested defaults" into `## Validation Log` (one row per question: `VL-n \| question \| suggested default \| reason`), then continue with the defaults. A later interactive session or cook can read the table and override. |
| `off` | skip the validate step (only when the caller declares it explicitly). |

## Verification pass (before asking or emitting)

Re-read the plan + red-team disposition and collect: decisions with no owner (who finalizes? when?), assumptions without evidence, unweighed trade-offs, invented thresholds, **and every `[ASSUMED]` tag** (plus any load-bearing `[PRIOR]`) left by the planner (two-way Evidence Filter — rule verification-mechanism).
Each item becomes ONE question — merge duplicates, drop questions answerable by reading the repo (Scout First).

To make the scan systematic, sweep the plan text for these keyword triggers and bucket each hit into a question category — the keyword is the tell that a decision is implicit:

| Category | Keywords that flag an implicit decision |
|---|---|
| **Architecture** | "approach", "pattern", "design", "structure", "database", "API" |
| **Assumptions** | "assume", "expect", "should", "will", "must", "default" |
| **Trade-offs** | "tradeoff", "vs", "alternative", "option", "either/or" |
| **Risks** | "risk", "might", "could fail", "dependency", "blocker", "concern" |
| **Scope** | "phase", "MVP", "future", "out of scope", "nice to have" |

Tag each question with its category in the Validation Log so phase propagation knows the target section (Architecture→Architecture, Scope→Implementation Steps, Risk→Risk, etc.).

**Escalation (Architecture / Trade-offs):** when a flagged decision is *load-bearing*, *costly to reverse*, AND the alternatives differ on a MEASURABLE axis, do not just ask the user to guess — propose `hs:bakeoff` (build cheap probes, decide by numbers) and lock the plan on the winning direction. Only when no mechanical metric exists does this stay a judgment question.

## Phase-graph check (the `plan-graph.yaml` sidecar is MANDATORY)

The sidecar is authored in step 5 for every plan; if `plan_graph.py` reports `error: no plan-graph.yaml`, the plan is incomplete — author the sidecar, then re-run. Run `plan_graph.py <plan-dir>` and read its findings: dependency **cycles**, **ordering hazards** (a prereq touches a file a later phase only creates), **parallel batches** (topological antichains), and **shared-file conflicts**
(two same-batch phases own the same path → must serialize). This is **read-only / detection only** — surface the findings to the planner; never auto-edit the plan (red line: no AI rewrite after approval). A cycle or a shared-file conflict is a question for the user, not a silent fix.

## Present before asking (MANDATORY)

Before the first question, output a brief visible recap in the response: the plan's phases, the key decisions, and the assumptions/risks this interview will probe (5–10 bullets). The interview often runs in a fresh session where the user has not seen the plan body — a question referencing unseen plan content appears to come from nowhere. Extended-thinking reasoning is invisible; externalize it
first.

Write each question and option to stand alone: name the plan section or decision it refers to instead of assuming earlier turn text is still on screen.

## Asking rules

1. Ask via AskUserQuestion; place the recommended option FIRST and mark it "(Recommended)". When the question is about *approving a plan/artifact* (not choosing config), offer `Direct Review (Plannotator)` first (rule `harness/rules/plannotator-review-gates.md`): `annotate` on `plan.md`, and annotations return as decisions.
2. At most 4 questions per round; open a new round if more remain. A long interview is fine — a wrong decision is costly.
3. User overriding the recommended option is normal: record it, do NOT argue back unless there is new evidence (rule `harness/rules/verification-mechanism.md`, "User decision").

## Recording results

- **Validation Log in plan.md, verbatim decisions** (table: VL-n | topic
  | decision | record DEC?): the log is the source of truth when context is compressed.
- Architecture decisions -> DEC via `decision_register.py --append-alloc` immediately.
- Propagate every decision into the affected phase files (mark as updated).
- Gate pass condition: **Failed: 0** — no blocking question remains open. Unresolved non-blocking items may stay; record clearly which wave will resolve them.
