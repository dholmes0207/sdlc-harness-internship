---
name: hs:fix
injectable: false
description: Fix bugs, test failures, and CI/CD failures with an evidence-based workflow. Use when there is a concrete bug, a clear error, or a red test.
argument-hint: "[quick | standard | deep] [--auto]"
allowed-tools: [Bash, Read, Write, Edit, Grep, Glob, Task]
metadata:
  compliance-tier: workflow
---

# hs:fix — evidence-based bug fixing

Standard flow: **debug → fix → red→green test → review → gate**. No step may be skipped. Diagnose the root cause BEFORE fixing.

**Who does what**: main owns diagnosis-approval, the fix (Step 3), and the red→green test (Step 4); `@debugger` investigates (Step 2) and `@code-reviewer` reviews (Step 5) — advisory, they do not mutate. When a slice is delegated to `@developer`, main re-verifies its test (Step 5).

**Evidence rule** (Evidence Filter, artifact = source of truth) in
`harness/rules/verification-mechanism.md` — read first; not repeated here.

**Probe-first ★** (`harness/rules/agent-operational-discipline.md` — the priority discipline): before you build a fix on "it's probably X", RUN the real thing to confirm the mechanism — reading code / `--help` / reasoning is a *hypothesis*, NOT a probe. An unconfirmed cause is `[ASSUMED]`, never OBSERVED (four claim labels in the evidence rule above); the red→green repro test
IS that one real-run confirmation — no fix ships ahead of it.

**TDD red→green**: `harness/rules/tdd-discipline.md` — test fails intentionally first, implement until green, paired commit, 100% pass before gate.

## Modes

| Mode | When | Behavior |
|---|---|---|
| `quick` | lint/type error, obvious single-file issue | minimal scout → diagnosis abbreviated → fix → verify |
| `standard` (default) | multi-file bug, unclear cause | full pipeline: scout→diagnose→fix→test→review→gate |
| `deep` | architectural impact, 3+ failures | escalate: deep hs:debug + hs:brainstorm before fixing |

No argument → `AskUserQuestion` asking for error description + mode selection.

**`--auto`** (additive; default behavior unchanged): suppress the mid-fix `AskUserQuestion` prompts (mode selection here, and the vague-answer / reviewer-flag prompts below) — auto-pick the mode from the symptom, self-decide, and proceed. Every auto-decision is **written to a concrete sink, never just narrated**: appended to the fix report under `plans/reports/` (what was decided + why) AND
emitted via `harness/scripts/emit_observation.py`. If the sink is not written, the decision was not made. This is the opt-in that lets a `--fix-auto` recall review drive a non-stop fix. Without `--auto`, hs:fix asks at every decision point as before. `--auto` never weakens the gate, the red→green test, or the review step, and never auto-applies a behavior-changing fix on a high-risk path
without a non-author reviewer — it only removes the human prompts.

## HARD-GATE (real wiring)

Stage `push|pr|ship|deploy` is blocked by `harness/hooks/gate_stage.py` when `verification.json` is missing, any check is non-PASS/SKIP, or the verdict is `BLOCKED` (schema: `harness/schemas/artifact-verification.json`). The gate is a presence gate — it proves the step ran, not who ran it
(`harness/rules/verification-mechanism.md`).

### HARD-GATE-NO-SIDE-EFFECTS

Before writing `verification.json`, run a blast-radius sweep (the reference workflows cite this anchor):
walk every dependent caller of the changed functions (Step 1 blast radius), run the tests in modules
that share files/contracts, and confirm public contracts (signatures, schemas, APIs, env vars) are
unchanged. Any NEW red test → STOP and report to the user (revert / narrow scope / update dependents /
accept with explicit note) — do not proceed to the gate.

## Standard procedure

### Step 1 — Scout (required, cannot be skipped)

Understand the codebase BEFORE forming hypotheses:
- Use `hs:scout` or an Explore subagent to find: affected files, callers/dependents, related tests, `git log --oneline -20` (which recent commit is the cause?).
- Record the "blast radius": every code path that depends on the broken behavior.
- Quick mode: only the affected file + direct deps.

### Step 2 — Diagnose (required, cannot be skipped)

Load `references/triage-and-scope.md`. Principles:
- **Capture state before**: copy-paste the exact error message, test failure output, stack trace. The baseline for Step 4 comparison.

- Spawn the `@debugger` agent: investigate the root cause with an evidence chain (observe → hypothesize → test hypothesis → trace back to root cause).
- Do not propose a fix until all 5 questions are answered:
  1. Exact symptom (precise error)?
  2. Reproduction steps (minimal command)?
  3. Expected vs actual?
  4. Root cause at `file:line`?
  5. Blast radius (which paths are affected)?
- If any answer is vague ("probably", "I think") → `AskUserQuestion` or scout further.
- If 2+ hypotheses fail → escalate to mode `deep`, ask the user.

### Step 3 — Fix (minimal scope)

Load `references/minimal-fix-discipline.md`. Principles:
- Fix the ROOT CAUSE, not the symptom.
- Minimal change: only necessary files, following existing patterns in the codebase.
- Do NOT create new abstractions when not needed; do NOT refactor outside bug scope.
- After 3 failures → STOP, reframe the architectural question with the user.

### Step 4 — Red→green test (required)

Load `references/regression-test.md`. Follow `harness/rules/tdd-discipline.md`:
1. Write a regression test **BEFORE** fixing (or confirm an existing test fails at the right point).
2. Run the test → must be **RED** (intentional failure).
3. Apply the fix → re-run → must be **GREEN**.
4. Run the full suite: `python3 -m pytest harness/tests/ -q` (or the repo suite per standards). All must pass.
5. Deleting/skipping/weakening tests to go green is forbidden — "Fix regressions, not the test."

Write `verification.json` (`harness/schemas/artifact-verification.json`): `stage`, `plan`, `actor`, `ts`, `checks[]`, `verdict`. The `verdict` is one of `PASS` / `PASS_WITH_RISK` / `BLOCKED`; for VERIFICATION a hard stage clears when no check FAILs and the verdict is not `BLOCKED` (both `PASS` and `PASS_WITH_RISK` pass). The exact-`PASS` rule applies to review-decision / critique-consensus,
not verification.

### Step 5 — Review

Spawn `@code-reviewer` agent:
- Input: modified files + blast radius from Step 1 + diagnosis report from Step 2.
- Ask reviewer to check: (a) root cause is genuinely resolved (not a symptom patch), (b) no regression in blast radius, (c) public contract unchanged (signatures, schemas, env vars), (d) no new errors.
- If reviewer flags a regression → `AskUserQuestion` with 2-4 specific options (revert / narrow scope / update dependents / accept with explicit note). Do not decide unilaterally.
- **Delegated slices** (parallel `@developer` per issue): main MUST re-run each delegated issue's regression test and confirm it fails WITHOUT the fix before accepting the slice — a subagent-authored test is not trusted red→green until main sees it go red then green.

### Step 6 — Gate and finalize

Load `references/verify-and-gate.md`. See HARD-GATE above for the block condition.
- After gate passes: ask user whether to commit (spawn `@git-manager` agent, conventional commit, no AI reference). If a plan is active → update plan status.
- If docs/behavior changed → spawn `@docs-manager` agent to update `docs/`.
- If the bug exposed a **repeatable failure mode** (wrong pattern, missing wiring, a gotcha that cost real time) → suggest `/hs:remember` to capture a one-line `harness/LESSONS.md` entry (failure → rule → check). Optional, human-approved; skip a one-off.

## Anti-Rationalization

| Thought | Reality |
|---------|---------|
| "I can see the problem, let me fix it" | Seeing symptoms ≠ understanding root cause. Scout first. |
| "Quick fix for now, investigate later" | "Later" never comes. Fix properly now. |
| "Just try changing X" | Random fixes waste time and create new bugs. Diagnose first. |
| "It's probably X" | "Probably" = guessing. Use structured diagnosis. Verify first. |
| "One more fix attempt" (after 2+) | 3+ failures = wrong approach. Question architecture. |
| "Emergency, no time for process" | Systematic diagnosis is FASTER than guess-and-check. |
| "I already know the codebase" | Knowledge decays. Scout to verify assumptions before acting. |
| "The fix is done, tests pass" | Without prevention, same bug class will recur. Add guards. |

## Boundaries

- Do NOT modify files outside bug scope (strict YAGNI).
- Do NOT create abstractions, wrappers, or helpers not directly required.
- Do NOT bypass the gate by writing PASS without running real tests.
- Do NOT weaken/skip/delete tests to fake green.
- On completion: report root cause, files modified (absolute paths), tests added, gate verdict.
- If gate blocks → clearly state the reason + the missing checklist items.

## Agent/rule wiring

| Backing | Role |
|---|---|
| `@debugger` agent | Root cause investigation (Step 2) |
| `@code-reviewer` agent | Review fix + blast-radius sweep (Step 5) |
| `harness/rules/tdd-discipline.md` | Red→green rule, 100% pass (Step 4) |
| `harness/hooks/gate_stage.py` | Presence gate before ship (Step 6) |
| `harness/schemas/artifact-verification.json` | verification.json schema |
| `harness/rules/verification-mechanism.md` | Evidence rule |
| `plans/reports/` | Diagnosis + review reports |

The `workflow-*.md` references below carry richer historical process detail (TaskCreate/TaskUpdate phase tracking, extra activations like project-management / sequential-thinking). SKILL.md's Standard procedure + Agent/rule wiring table above is the critical path — treat that reference detail as supplementary context, not additional MUST steps.

## References (load when needed)

- `references/triage-and-scope.md` — 5-question diagnosis, blast radius
- `references/minimal-fix-discipline.md` — minimal-fix principles, anti-patterns
- `references/regression-test.md` — red→green test procedure, verification.json
- `references/verify-and-gate.md` — pre-gate checklist, side-effect sweep
- `references/skill-activation-matrix.md` — which helper skill/agent to reach for by signal
- `references/workflow-quick.md` — quick-mode procedure (single-file / obvious issue)
- `references/workflow-standard.md` — standard-mode pipeline (scout→diagnose→fix→test→review)
- `references/workflow-deep.md` — deep-mode escalation (architectural / 3+ failures)
- `references/workflow-ci.md` — CI/CD failure triage
- `references/workflow-types.md` — failure-type taxonomy → workflow selection
- `references/workflow-ui.md` — UI/visual-bug workflow (design search, visual diff)
- `references/workflow-logs.md` — log-driven investigation


## Auto-decision ledger

When acting autonomously, append one advisory ledger line per governance-bearing decision (this is separate from and additional to the emit_observation signal) — see `harness/rules/auto-decision-ledger.md`.
