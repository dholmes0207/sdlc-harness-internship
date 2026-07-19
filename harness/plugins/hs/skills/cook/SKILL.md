---
name: hs:cook
injectable: false
description: Execute an approved plan phase by phase — TDD red→green, generate verification/review-decision artifacts, trace every step. Use when a human-approved plan is ready to implement.
argument-hint: "<plan-path> [--phase <id>] [--parallel] [--tdd] [--in-place] [--review <low|medium|high|xhigh|max>]"
allowed-tools: [Bash, Read, Write, Edit, MultiEdit, Grep, Glob, Task]
metadata:
  compliance-tier: workflow
---

# hs:cook — execute plan by phase

Input: path to a human-approved plan. No approved plan → return to hs:plan (gate `require_plan` blocks hard stages if you try to skip this).

**Context isolation:** cook should run from a CLEAN context — ideally `/clear` after approving the plan, then `/hs:cook <absolute-path>`. Planning carryover (research, debate, red-team) shifts cook's focus. The nudge `cook_isolation_nudge` reminds you if plan+cook are detected in the same session (advisory, does not block). Details: `references/context-isolation.md`; backing:
`harness/rules/workflow-handoffs.md` #5.

**Flags carried from the plan (`--tdd` / `--parallel`):** NOT new cook decisions — the plan already locked them; hs:plan hands you the exact cook command. Cook auto-inherits when a flag is absent (TDD plan runs red→green regardless; `--parallel` also resolves from env/config), but the flag makes the behavior **explicit and traced** vs implicit. Surface it to the user, do not swallow it:
- `--tdd` → announce the per-phase Tests Before → Implement → Tests After → Regression Gate split is active for this run (vs the plain red→green cook prints otherwise).
- `--parallel` → announce which phases will fan out concurrently BEFORE the first slice, and that the integration barrier still runs the full suite serially. If the plan's handoff recommended sequential (a batch touches core/shared) but `--parallel` was passed anyway, say so and let `cook_parallel_plan.py` re-demote overlapping batches — it is the final arbiter (see the Parallel execution
  section). Never fan out silently.
Rule backing: `harness/rules/workflow-handoffs.md` #5 (flag propagation).

**General rules**: `harness/rules/tdd-discipline.md` (red→green, 100% pass) + `harness/rules/verification-mechanism.md` (evidence, posture gate) + `harness/rules/agent-operational-discipline.md` (**probe-first ★**: a load-bearing assumption you CAN check empirically — RUN the real thing FIRST, before building on it; a plan claim not exercised is
`[ASSUMED]`, never OBSERVED). Read first. Gates that require human review (per-phase, review-decision) apply `harness/rules/plannotator-review-gates.md` — diffs use `review`.

## Step 0 — Standards are input

Same as hs:plan: read `docs/code-standards.md` + `docs/system-architecture.md` before writing code. Code follows the shared `docs/code-standards.md` — that is why the harness exists on this machine.

## Step 0.5 — Phase-DAG preflight (HARD)

The `plan-graph.yaml` sidecar is a mandatory plan artifact. Before the first phase, run `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/plan_graph.py <plan-dir> --require`.
A non-zero exit (`error: no plan-graph.yaml`) means the plan skipped a required artifact — **STOP** and send the user back to hs:plan to author the sidecar; do NOT start cooking. On exit 0, read the printed parallel-batch + conflict findings: a
`parallel-conflict` between phases you intend to run concurrently must be serialized first. This is the second belt of the same gate — plan approval already refuses a sidecar-less plan, so a missing sidecar here means an unapproved or hand-built plan.

## Step 0.6 — Open the plan (MUST)

Before phase 1, run `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/open_plan.py <absolute-plan-dir>` — it flips the plan frontmatter `pending|approved → in_progress` (idempotent, surgical).
This is MANDATORY, not a side-effect of cooking: the gate's active-plan resolver returns ONLY an `in_progress` plan, so skipping it makes ship/deploy unable to resolve this plan and the plan never auto-closes. Close out at the
end with `close_plan.py` (`in_progress → completed`). Full lifecycle: `references/workflow-steps.md` (Step 3.O + close-out).

## Per-phase loop

1. **Conformance checklist**: read the phase file; list files to create/modify; verify naming/format match standards; any deviation from the plan → STOP and ask, do not silently change.
   Details: `references/implement-test-loop.md` (5-item checklist + verify-after-file). On the FIRST phase, run `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/plan_layout_check.py` (soft, exit 0): a warning that the approval hash misses a phase file means the plan was hand-authored off-layout — re-approve via `plan_approval.py` before trusting the plan contract.
2. **TDD red→green**: follow tdd-discipline rule — test-first intentional FAIL → implement until green → re-run full suite → paired commit test+module. Sub-steps 3.T / 3.I / 3.V + fix-loop: `references/per-phase-tdd.md`.
   **Before a phase's red→green (3.T test + 3.I implement) on a `mode: hard` plan: STOP — the WHOLE phase delegates to `@developer` by default; going inline needs an explicit flag or a surfaced decision (see §Per-phase red→green).**
   Flag `--tdd` (carried from the plan, or passed here): each phase splits Step 3 into the explicit Tests Before → Implement → Tests After → Regression Gate sequence —
   `references/workflow-steps.md` (`### --tdd Flag Behavior`). Without it cook still runs red→green; `--tdd` just makes the per-phase test/implement split explicit and traced. On green, before advancing, a **simplification pass**: if the change grew a special case or duplicated an existing pattern, collapse it (suite stays green). Pattern library:
   `harness/plugins/hs/skills/problem-solving/references/simplification-cascades.md`.
3. **Verify before done**: before writing artifact and advancing phase — suite green, no new lint/type errors, every acceptance criterion has evidence (file:line), no silent contract change. If a side effect appears → STOP AskUserQuestion with 2-4 choices; do not self-patch. Details: `references/verify-before-done.md`.
4. **Artifacts (machine-written JSON)**: at end of phase write `plans/<plan>/artifacts/verification.json` **carrying `phase: <node-id>`** (the plan-graph node id for this phase) (+ `review-decision.json` when review occurred; schema in `harness/schemas/`). The `phase` field is load-bearing, NOT optional: the snapshot hook only copies a PASS verification to `verification-<phase>.json` when
   `phase` is present, and that per-phase snapshot is what `derive_plan_completion` counts. **Forget `phase` → no snapshot → the plan never auto-closes AND the ship/deploy gate blocks** with the node listed as missing evidence. (The hook now warns to stderr when a PASS lacks `phase`.) Write the file as
   **pretty-printed JSON** (`indent=2`, `ensure_ascii=False`) so the artifact is readable in diff and the gate's diagnostic output stays human-friendly. Gate reads artifact from **filesystem**, so no commit is needed for the gate to see it (rule verification-mechanism). `plans/` is tracked (only `plans/reports/` is scratch) — so commit these artifacts into the plan at finalize.
5. **Trace**: significant steps emit events via `harness/hooks/trace_log.py` (append_event — actor auto-resolved, do NOT hand-craft JSONL). Carve-out: phases declared stateless (frontmatter `stateless: true` or comment `# stateless-by-design`)
   → skip trace for that phase; gate_stage still runs (trace is telemetry fail-open, not a compliance gate).

## Per-phase red→green (3.T+3.I): delegate to `@developer` by default — STOP before coding inline

**Before you write ANY test or implementation code for a phase, STOP and resolve the delegation — this is a checkpoint, not a suggestion.** On a `mode: hard` plan the phase's **full red→green (test 3.T + implement 3.I)** goes to a `@developer` subagent BY DEFAULT.
Main keeps verify (3.V), a **review of the subagent's code AND test** (catch a tautological or weakened test — see `references/verify-before-done.md`), and the paired commit. This is the only delegation with **no gate behind it** — nothing mechanical stops you from sliding into inline coding, so the discipline is on you. Run this check every phase:

1. **Resolve the mode deterministically** (never a gut call on difficulty): `--in-place` flag → phase `in_place: true` → plan `mode:` → `plan-graph.yaml` assessment. **Only `--in-place` or `in_place: true` authorizes inline.** Full order + the `@developer` snippet: `references/per-phase-tdd.md` + `references/subagent-patterns.md`.
2. **No inline flag but you want to go inline anyway? That is a DECISION, not a default — surface it.** Do NOT bury it in an announce line. STOP and AskUserQuestion (why inline, what is lost), then wait. Rationalizing inline from a memory or an edge-case is the exact failure mode this guards against.
3. **Proactive / autonomous output style does NOT waive this.** "Prefer action, execute inline" is a style default; the `mode: hard` delegation is a plan mandate and **out-ranks** it. When the two conflict, delegate (or surface per #2) — do not let the style bias silently pick inline.
4. **Already inside a worktree** (cook was invoked in a worktree, or you entered one): still delegate — a plain `@developer` spawn (no `isolation` param) runs **in-place** and inherits the
   current working directory, so its writes land in THIS worktree.
   `@developer` no longer self-isolates from its frontmatter; only a spawn that passes `isolation="worktree"` gets its OWN tree, which is for `--parallel` slices editing shared files — never a reason to implement inline in a normal sequential phase.

## After the last phase — MANDATORY delegation (Steps 4–6, never skip)

Cook is NOT done at the last phase's integration barrier + `verification.json`. The post-build steps are MANDATORY and MUST be delegated via the Task tool — never done inline, never skipped on a "small" task. Full protocol + mode matrix: `references/workflow-steps.md` (Steps 4–6, §Critical Rules MANDATORY DELEGATION).

- **Step 4 — test**: MUST spawn `@tester` (+ `@debugger` on failure). Running the suite yourself is NOT a substitute for the independent tester delegation.
- **Step 5 — code review**: MUST spawn `@code-reviewer` on the diff — at this final gate, **DO NOT review code yourself**. The FINAL review is an independent `@code-reviewer` that re-derives correctness and catches what same-thread self-review misses.
  This end-of-cook gate does NOT replace main's per-phase review of each `@developer` slice's diff+test — that happens every phase. Auto-mode auto-approves only on `review-decision.json` PASS + artifact-gate + `risk-gate.autoStopRequired` false; otherwise a human approves.
  **Review effort — `--review <low|medium|high|xhigh|max>`**: dials *how hard* this Step-5 review digs, never *whether* it runs. There is no `skip` value — the floor is always a real independent review that writes `review-decision.json` (PASS/BLOCK); dialing effort scales the finding-production depth only.
  Default **`low`** (today's single-pass, non-breaking). The run's value is forwarded via the `--effort` flag to the review delegation; when absent, effort resolves by the review skill's own precedence (arg > `HARNESS_REVIEW_EFFORT` env > `code-review.yaml` > `low`). Wiring: `references/workflow-steps.md` §Step 5.
- **Step 6 — finalize**: MUST spawn `@docs-manager` + `@git-manager`, run project-management sync-back, then `close_plan.py`. Skipping any leaves the plan half-shipped.

**Hard rule**: if Task-tool calls = 0 at the end of a cook run, the workflow is INCOMPLETE.

## Pause cadence — HARNESS_AUTONOMY

| Level | Behavior |
|---|---|
| `default` | run per-phase sequence automatically; **pause at 2 checkpoints: plan approval + ship** |
| `ask_all` | pause after EVERY phase |
| `god` | no pauses (trace still records fully — autonomy comes with a trace) |

Resolve the level deterministically — do not eyeball the env. At each boundary run `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/autonomy_policy.py --boundary <plan_approval|phase|ship>` and pause only when it prints `pause` (`--show` emits the resolved level + full matrix). A missing/invalid level falls back to `default`.

**IMPORTANT**: `--boundary` and `--show` are mutually exclusive — pass exactly one, never both.

All levels do NOT self-ship: stage `push|pr|ship|deploy` always goes through artifact gate.

When pausing for human (`ask_all` after a phase, or before writing `review-decision.json`): ask AskUserQuestion with 3 options [Review directly (Plannotator) / Approve / Reject]; choosing (1) → `plannotator_surface.py review <diff>` (rule `plannotator-review-gates.md`).

## Parallel execution (opt-in `--parallel`)

Default is **sequential** (everything above). `--parallel` runs independent phases concurrently to save wall-clock — without weakening a gate or trusting a subagent on sight. Full protocol: `references/parallel-execution.md`. Backing: `harness/rules/orchestration-protocol.md`.

- **Resolve the opt-in deterministically** — do not eyeball it. `--parallel` flag > `HARNESS_COOK_PARALLEL` env > `cook.parallel` config (`harness/data/cook.yaml`) > default OFF.
`cook.parallel_max` (advisory) caps fan-out — the agent applies it; the planner emits the partition, not the cap. Run `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/cook_parallel_plan.py --root . --phases-json <f> --expand` (add
  `--parallel` to force ON) → it prints `parallel_enabled` + the safe partition.
- **Partition before fanning out**: the partitioner groups only phases marked `parallel_safe` in their phase frontmatter **whose `owns` globs are disjoint**. Any ownership overlap (same file / generated artifact / migration / shared config) demotes BOTH phases to sequential and is reported in `conflicts` — **never parallel-edit a shared path, never fall back silently**.
- **Delegate**: each parallel slice → one `@developer` subagent in an isolated **worktree** — pass `isolation="worktree"` **explicitly on the spawn**
  (`@developer` no longer self-isolates; without the param the slice runs in-place and collides), given the full delegation context (task · read/modify globs · acceptance · constraints · env).
- **Verify every slice — MANDATORY, never trust on sight** (two tiers):
  1. cook **self-verifies**: re-run that slice's tests + lint, read its diff against the
     phase's acceptance criteria;
  2. risky slice → spawn an **independent verifier subagent** (`@independent-revalidator` or
     `@code-reviewer`) that re-derives correctness from the diff alone.
  A slice that fails either tier does NOT merge — it returns to sequential rework.
- **Integration barrier**: after all verified slices merge, run the **full suite serially** (the real green gate) before writing `verification.json` and committing. Parallelism only speeds the build; the integration gate stays serial and strict.
- Gates are unchanged: `gate_stage.py` still requires the artifact; `--parallel` never bypasses it.

## Gate wiring (personal-first: generate local, enforce remote)

`harness/hooks/gate_stage.py` ADVISES on a hard stage when `verification.json` is missing / verdict != PASS — `[advisory]` + `gate_advisory` trace, command proceeds (exit 0). The one hard LOCAL block is the artifact-forgery arm (shell write to a receipt path — the agent cage). Presence enforcement lives in remote CI (receipts-gate).

## References (load on demand)

| Drawer | Content | When to load |
|---|---|---|
| `references/per-phase-tdd.md` | Sub-steps 3.T/3.I/3.V, fix-loop, stateless phase | When per-phase TDD detail is needed |
| `references/implement-test-loop.md` | 5-item conformance checklist, verify-after-file | When the code-check order needs reminding |
| `references/context-isolation.md` | Why isolation matters, /clear procedure, exceptions | When plan+cook are in the same session |
| `references/parallel-execution.md` | Opt-in `--parallel` protocol: resolve → partition → delegate → verify → integration barrier | When cooking with `--parallel` |
| `references/verify-before-done.md` | 5 verification invariants, end-of-phase checklist, side-effect check | When preparing to advance a phase |
| `references/subagent-patterns.md` | Task-tool snippets for every subagent role: researcher, scout, review, adversarial, simplify, git | When selecting which subagent pattern to use in a workflow step |
| `references/workflow-steps.md` | Step-by-step workflow for all modes (interactive/auto/fast/parallel/tdd/no-test/code) including review gates | When the full mode-specific step sequence needs reminding |
| `_shared/workflow-artifacts.md` | JSON artifact schema (context-snippets/risk-gate/verification/review-decision/adversarial-validation), approval rules, redaction policy | When writing or validating review/finalize artifacts |

## Observe checkpoint (end-of-work)

When cook finishes, if this run surfaced a judgment a counter cannot see, record ONE closed-vocab signal — a REAL observation only, not every run. Vocabulary lives in `harness/data/observation-signals.yaml`.

```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/emit_observation.py --skill hs:cook \
    --signal <gate-repeat-block|plan-revised-post-approval|thin-evidence> \
    --payload "<one line: what happened>"
```

Surfaces in the read-only `observations` lens (honesty-gated). Skip it silently when nothing notable happened — a fabricated signal is worse than none.

**Two-output nudge** — if this run hit recurring friction or a keeper convention, suggest capturing it: `hs:remember` for a durable rule/decision, or a per-repo review-rule via the rule-author skill (writes `standards.user.yaml`). (Suggestion only — no new gate, no heavy process.)

## Boundaries

- Do not modify `harness-hooks.yaml`/`stage-policy.yaml` to pass the gate — git-tracked, any change shows in diff + trace. Genuinely stuck → ask the human.
- Work that arises outside the plan scope → record via `backlog_register.py add`; do not steer the plan mid-flight.
- Mid-phase, if the planned direction stalls and 2-3 alternatives are measurably comparable, escalate to `hs:bakeoff` (probe the alternatives, decide by numbers) instead of grinding one direction — then resume cook.

## Auto-decision ledger

Record ONLY decisions that DEVIATE from the approved plan — see `harness/rules/auto-decision-ledger.md`.
