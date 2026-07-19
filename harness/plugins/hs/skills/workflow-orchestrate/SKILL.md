---
name: hs:workflow-orchestrate
injectable: true
description: Design a spawn strategy for a delegated task — pick subagents vs Workflow vs Agent Teams, size and group the fan-out, batch-consolidate, early-write findings — then present it for approval before spawning. The delegation planner other skills call.
argument-hint: "<task to delegate> [--mode subagents|workflow|team] [--run-id ID] [--product]"
allowed-tools: [Bash, Read, Write, Glob, Grep, Task, Workflow]
metadata:
  compliance-tier: workflow
---

# hs:workflow-orchestrate — plan the spawn before you spawn

Input: a task worth delegating to more than one agent — a research sweep, a multi-lens critique, a broad review, a decomposition. No input -> `AskUserQuestion`: what the task is, how wide, whether the findings feed a later stage, and where reports should land.

This skill does NOT do the research itself. It produces a **spawn-strategy proposal** — mode, group sizing, batch cadence, template reuse, early-write paths — presents it for approval, then drives it. It exists so delegation is planned and grouped, never a reflex of one-subagent-per-finding.

It builds ON `harness/rules/orchestration-protocol.md` (delegation context, write-lane preflight, base-workflow reuse) — it does not restate it. Load that rule when spawning.

## Three modes

| Mode | Shape | When |
|---|---|---|
| **A — subagents** | inline `Task` fan-out, batches of ≤2 | single stage, no barrier, few subs, no need for deterministic control flow |
| **B — workflow** | a `Workflow` script over subagents | multi-stage, needs a barrier (a stage needs all prior results), wants determinism, or wide enough (≥6 subs) that hand-driven batches get unwieldy |
| **C — agent teams** | persistent teammates that message each other + a shared task board, worktree-isolated, **mutating** | long-lived parallel *build/research* where units coordinate mid-flight (not one-shot advisory findings); requires the experimental Agent Teams flag |

A and B are one-shot advisory fan-outs — the sub reads, returns findings, exits. C is a different beast: teammates are long-lived, talk to each other, and write code. Do not model a build-many-slices-that-coordinate job as B, or a read-only findings sweep as C.

Mode is a decision, not a preference — `plan_orchestration.py` derives it from the task's knobs so the call is reproducible. It proposes **C only on the narrow `--coordinate --long-lived` signal** (mid-flight coordination over a long build/research); a plain wide fan-out never misroutes to the experimental team path. Agent Teams is CLI-only and **experimental** — gated by
`CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` (or `--agent-teams`) plus a server statsig — so C is
**never auto-launched**: the script always returns `exec.gate = confirm_required` for it. The live team API is `Agent(name=...)` + `SendMessage` over one implicit team; **`TeamCreate` / `TeamDelete` were removed in CC v2.1.178** — never emit them. Override the derived mode with `--mode` when you have a reason the knobs miss.

### Execution-gate policy (how the mode actually runs)

The script emits an `exec` block. Honor it — do NOT improvise a silent downgrade:

| Mode | ultracode ON | ultracode OFF |
|---|---|---|
| **A subagents** | run inline (`auto`) | run inline (`auto`) |
| **B workflow** | run the `Workflow` (`auto`) | **`confirm_required`** — a MANDATORY `AskUserQuestion` before running; **never** quietly fall back to inline subagents to dodge the ask |
| **C team** | `confirm_required` (experimental + high cost) | `confirm_required` |

`exec.no_silent_downgrade` is always true: when the right mode is workflow/team, the only sanctioned paths are *run it* (ultracode / user-approved) or *the user declines* — dropping to Mode A on your own is a policy violation.

## Process

1. **Frame + size.** Read the task and nearby context. Decide the groups (by *concern/dimension*, never one group per expected finding) and how many subs each needs. State the knobs: stage count, barrier?, determinism?, and — the load-bearing one — **fan-out→dedup vs find→verify shape**: ask "are the findings apply-ready, or will something act on them?" If a verdict, an edit, or a `--fix`
   consumes them, it is a **find→verify** task (pass `--find-verify`); a survey you will only read is fan-out→dedup. Getting this wrong ships unverified findings into a fix.

2. **Get the strategy (backing, not vibes).** Run the lead:

   ```bash
   python3 "${HARNESS_BIN_ROOT:-.}"/harness/plugins/hs/skills/workflow-orchestrate/scripts/plan_orchestration.py \
       --run-id <slug> --groups "research:4,critique:6,recommend:1" [--barrier] [--determinism] \
       [--fanout|--find-verify] [--coordinate --long-lived] [--ultracode] [--mode team] \
       [--reason "<why, cite evidence>" --strategy "<mode+template named>" --scope "<bounded>"] \
       [--token-budget <tokens> [--per-sub-cost <tokens>]] [--product]
   ```

   It emits JSON: `mode`, `groups`, `sub_count`, `batch_size`, `template`, `report_dir`, `reason`, and `exec` (the gate policy above). Pass `--ultracode` when ultracode is on, and `--coordinate --long-lived` when the task needs mid-flight coordination (Mode C). A team plan additionally carries `experimental`, `requires_flag`, and `api`.

   Pass `--reason/--strategy/--scope` to earn `route_depth:light` in the emitted `assess` block (a cheap-fan-out signal, never a verification bypass) — omitting them (or an unbounded scope / uncited reason) routes `route_depth:agent`.

   Pass `--token-budget <tokens>` to size the fan-out against a
   token/cost target: the plan gains a `token_budget` block with `capacity`, `within_budget`,
   and — when over — a concrete `trim_advice` map that fits (floor 1 sub/group, widest groups
   kept widest, tail groups dropped by name). The trim is **advisory**: original `groups` stay
   untouched; present the cut and let the user confirm it (same no-silent-downgrade contract as
   the exec gate). Default cost is ~100k
   output tokens/sub; override with `--per-sub-cost`. For a deep task, hand the framing to the `@workflow-orchestrator` agent instead — same structured plan, repo-read behind it.

   **`plan_orchestration.py` only SIZES the fan-out — it does NOT mint the spawn-budget token.**
   Its `token_budget` block is the token/cost axis, a different thing from the Layer-1b
   spawn-COUNT budget the provenance guard blocks on. Running this script leaves the guard's
   budget at the un-ticketed session threshold (default 5); the wider spawn budget is minted
   separately in Step 3, on approval. Do not read "strategy recorded" here as "budget widened."

3. **Present for approval.** Show the user: mode, total subs, the group→count map, the batch cadence, the template being reused, and the early-write dir. **Do not spawn before approval** — this skill's whole point. Use `AskUserQuestion` for any real fork (scope, sub-count, isolation).
   **On approval — and only then — mint the spawn-budget token.** This is the step that actually widens the provenance guard's spawn-COUNT budget from the un-ticketed session threshold to the approved `sub_count`; Step 2 did NOT do it. Never before approval (a rejected strategy must not bless a fan-out, memory `anchor-cli-self-auth-hole`), never at the strategy-emit step:

   ```bash
   python3 "${HARNESS_BIN_ROOT:-.}"/harness/plugins/hs/skills/workflow-orchestrate/scripts/run_state.py \
       write-token --run-id <slug> --mode <mode> --sub-count <sub_count> \
       --groups '<json: [{"key": "..."}]>' --report-dir <report_dir> \
       --session <session_id>
   ```

   Use the `run-id`, `mode`, `sub_count`, `groups`, and `report_dir` from the Step-2 plan JSON, and the current `session_id`. `sub_count` is clamped to `sub_count_cap` (default 32) at write time. If a prior budget block already tripped, this is the sanctioned way to widen it — not hand-editing state. Full rationale + the pre-spawn checklist: `references/spawn-discipline.md` §4.

   The token authorizes its full `sub_count` across every batch until it expires (~30min default, `spawn-provenance.yaml`'s `token_ttl_seconds`) — it does not need re-minting per batch, and it is never callable by a spawned subagent itself (only this skill, at this one approval point, mints it).

4. **Execute — reuse before you hand-roll.**

   **Pick the SHAPE before the base — the script sizes the spawn, it does NOT make this call for you.** *Mode (A/B/C) is the spawn mechanism; SHAPE (fan-out→dedup vs find→verify) is an orthogonal axis — both bases below are Mode B (Workflow scripts).* A fan-out→dedup (`base-fanout-consolidate`) returns RAW, unvalidated findings: fine for a survey you will read, NEVER apply-ready. If the
   findings will drive a verdict, an edit, or a `--fix`/`--fix-auto`, you **MUST** use the find→verify shape (`base-pipeline-verify`) OR verify each finding yourself against the source before acting. `route_depth:light` sizes the fan-out cheaply; it **never** licenses skipping verification.
   - Mode B, fan-out→dedup → `Workflow({name:"hs:base-fanout-consolidate", args:{lenses, findingsSchema, dedupKeyFields}})`. Findings are unverified — read-only survey, not apply-ready.
   - Mode B, find→verify → `Workflow({name:"hs:base-pipeline-verify", ...})`. Each finding is adversarially re-checked before it counts — use whenever findings feed a verdict/edit/`--fix`. (Per-finding verify is the intended pattern; only cap its WIDTH — the base warns when a lens over-produces.)
   - Mode B, bespoke multi-stage → inline `Workflow({script})` (research→consolidate→critique→recommend).
   - Mode A → inline `Task` fan-out in batches of `batch_size` (≤2 per turn).
   - Mode B when `exec.gate == confirm_required` (ultracode off) → **ask first** via
     `AskUserQuestion` ("run this as a Workflow, or you'd rather I don't?"). Run the Workflow on
     yes; on no, run the reduced Mode-A fan-out the user accepted. Never skip the ask by silently
     picking A (`exec.no_silent_downgrade`). Workflows are plan-gated — resolve per `orchestration-protocol.md`.
   - Mode C → hand off execution to the Agent Teams skill (see **See also**), passing it THIS plan JSON —
     do not re-derive the fan-out. Each `groups[].key` becomes one named teammate + `TaskCreate`;
     `report_dir` is the team's shared noticeboard; `exec.gate`/`experimental`/`requires_flag`
     carry the gate state (the team skill computes per-slice globs). Preflight
     `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`, spawn one worktree-isolated `Agent(name=...)` per
     slice, coordinate via `SendMessage` + the task board. Always `confirm_required`; if the flag
     is absent, tell the user and re-pick A/B — never silently degrade. The lead then runs the
     team skill's coordination loop.
   - Stamp which path ran: `Workflow(name)` | `Workflow(scriptPath)` | `inline-Task` | `agent-teams`.

5. **Early-write every finding.** Each spawned agent flushes its result to disk the moment it lands — an agent must not hold output in its return value alone (a stalled consolidation loses it):

   ```bash
   python3 "${HARNESS_BIN_ROOT:-.}"/harness/plugins/hs/skills/workflow-orchestrate/scripts/write_finding.py \
       --run-id <slug> --group <group> --title "<finding>" --body "<one-para>" [--product]
   ```

   Append-only, one file per group under `report_dir` (`docs/product/_refs/<slug>/` with `--product`, else `plans/reports/<slug>/`).

6. **Consolidate in batches.** Merge per group / per direction, not all subs at once — a single giant consolidation Write stalls at idle. Small batches, mechanical dedup (reuse the base template's dedup or `cat` the group files), then rank.

7. **Arbiter checklist.** Before the report ships, answer these independently — the final report is blocked until the arbiter answers:
   - Did each job produce the requested artifact?
   - Did any job fail, timeout, or emit an uncertainty marker?
   - Do job outputs contradict each other?
   - Were all listed checks run, and did they pass?
   - Are claims supported by file paths, command output, citations, or tests?
   - Are any destructive actions proposed but not approved?
   - Are unresolved questions listed plainly?

8. **Report.** Write the consolidated output to `report_dir` (or `plans/reports/` for a review). Return the run's absolute paths.

## Backing

- `scripts/plan_orchestration.py` — deterministic mode/template/report-dir proposal (tested).
- `scripts/write_finding.py` — append-only early-write per group (tested).
- `scripts/run_state.py` — atomic `state.json` writer + `--resume` reader (skip completed, re-dispatch in-flight) (tested).
- `scripts/orchestrate_metrics.py` — append-only `orchestrate-history.jsonl` corpus, actor+ts stamped (tested).
- `harness/plugins/hs/workflows/base-fanout-consolidate.js`, `base-pipeline-verify.js` — reusable bases.
- `harness/rules/orchestration-protocol.md` — delegation context, write-lane preflight, Workflow opt-in.
- Component agent: `@workflow-orchestrator` (strategy planner). Referenced by name; never imported.

## Boundaries

- This skill PLANS and DRIVES delegation; it does not do the analysis itself, and does not block. It never spawns before presenting the plan.
- Respect the 2-subagent-per-turn limit in Mode A — fan out in batches, never the whole set at once.
- Workflows are plan-gated: absent the opt-in, fall back to inline-Task batches (mandatory, not optional).
- Write-lane: before delegating a Write-bearing task, check the target role's lane (`orchestration-protocol.md`) or delegate read-only and let the parent write.
- Do not invent a bespoke `Workflow` script when a base template fits — reuse first.
- On completion: mode + template that ran, sub count, and the absolute report paths.

## Run-state, resume & metrics

Run-state (`state.json`, atomic + `--resume`) and the advisory metrics corpus
(`orchestrate-history.jsonl`, actor+ts) live under the **harness state dir** (gitignored), NOT
`plans/reports/`. Write on every job transition; after a crash rerun `--resume <run-id>`
(completed skipped, in-flight re-dispatched). The corpus is advisory — routing stays
reviewable, never silently edited mid-run. Full protocol: `references/run-state.md`.

## References (load on demand)

| Drawer | Content | When to load |
|---|---|---|
| `references/strategy-decision.md` | The mode A/B/C decision matrix, the exec-gate policy + override rules | Step 1–2 (every invocation) |
| `references/spawn-discipline.md` | Grouping, batch-consolidate cadence, early-write protocol | Step 4–6 |
| `references/templates.md` | Reusing the base workflows vs authoring a new `workflows/*.js` | Mode B execution |
| `references/run-state.md` | Run-state `state.json` fields, `--resume` semantics, metrics corpus + routing-suggestion rule | Fan-out resume / metrics |

## See also

- `hs:team` — executes Mode C. When the strategy resolves to Agent Teams (long-lived,
  coordinating, mutating teammates), hand it the sized plan JSON; this skill plans the mode,
  `hs:team` runs it. Soft pointer, not a co-install dependency (Teams is experimental-flag gated).
- `hs:coding-agent-orchestration` — the sibling for choosing an *external* coding agent/CLI (Codex, Cursor, Amp, Droid, OpenCode, Antigravity) when the internal partner/gemini lanes do not cover the tool. This skill owns internal harness fan-out; that one owns the cross-tool selection + handoff shape. Soft pointer, not a co-install dependency.
