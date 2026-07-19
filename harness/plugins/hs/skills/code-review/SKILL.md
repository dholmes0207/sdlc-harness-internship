---
name: hs:code-review
injectable: false
description: Review code with technical rigor — bugs, regressions, security. Supports pending changes, PR number, commit hash, codebase scan. Verdict in review-decision.json; failing review blocks pr/ship/deploy. Use when code needs a rigorous pre-merge review.
argument-hint: "[--effort <low|medium|high|xhigh|max> | --low|--medium|--high|--xhigh|--max] [--profile <name>] [--rounds <n>] [--auto] [--in-place] [--fix | --fix-auto] [--reply] [--spec <plan>] [--pending | <#PR|commit|codebase>]"
allowed-tools: [Bash, Read, Write, Grep, Glob, Task]
metadata:
  compliance-tier: gate
---

# hs:code-review — evidence-based code review

Review focused on production risk: logic defects, regressions, trust-boundary violations, security, performance. No rubber-stamping; no praise-padding. Evidence before claims.

**Posture**: assume code was written by an AI agent until proven otherwise. Clean structure, confident comments, and happy-path tests are not evidence of correctness. Rulebook-first, not a friction-avoiding collaborator.

## Input modes

Auto-detect from argument. No argument → `AskUserQuestion`.

| Input | Mode | Diff source |
|---|---|---|
| `--pending` | Pending | staged + unstaged (`git diff`) |
| `#123` or PR URL | PR | `gh pr diff` |
| `abc1234` (7+ hex) | Commit | `git show` |
| `codebase` | Full scan | `hs:repomix` compact then analyze |
| *(none)* | Recent | changes in current context |

Argument resolution details: `references/input-mode-resolution.md`.

## Effort (recall-mode)

Effort scales Stage-2 finding production (fan-out lenses → adversarial verify → sweep). Pass it as a flag:
`--effort <low|medium|high|xhigh|max>`, or the shorthand `--low|--medium|--high|--xhigh|--max` (equivalent).
A bare leading token (`high`) is still accepted for back-compat but the flag forms are canonical.
Default **`low`** = today's single-pass review (non-breaking). Resolution precedence, highest first:
arg (`--effort` / `--<level>` / bare token) > `HARNESS_REVIEW_EFFORT` env > `harness/data/code-review.yaml`
> default `low`. The full protocol — effort table, scope read, `--auto`, the `high`+ ultracode Workflow
orchestration (the shared `hs:base-pipeline-verify` base, with inline + Task fallback), and the "gate unchanged" statement — is in `references/recall-mode.md`. The recall engine changes only HOW findings are produced; the verdict, gate, rules-layer, and dismissals path are unchanged.

## Orthogonal flags

| Flag | Effect |
|---|---|
| `--auto` | Agent self-adjudicates the **REVIEW / effort decision only** (auto-bumps to the suggested effort on a scope mismatch + logs it). Does NOT govern fixes. |
| `--in-place` / `--inline` | Review directly in the main agent. **Default is OFF** — the skill must spawn subagents (Task / Workflow) for effort ≥ medium and even `low` should prefer the `@code-reviewer` agent unless the user explicitly opts into in-place review. |
| `--fix` | Apply only the fixes that need no user confirmation (clear correctness bugs + safe cleanups); skip + report anything risky, out-of-diff, or a judgment call |
| `--fix-auto` | Auto-fix and self-decide ALL findings → drives `hs:fix --auto`. Mutually exclusive with `--fix`; both OFF by default |
| `--reply` | Post review to PR via `gh pr review` after review (or after fix loop). One-word alias: `--comment` |
| `--spec <plan>` | Enable Stage 1 spec-compliance before Stage 2 quality review |
| `--profile <name>` / `--rounds <n>` | Multi-round committing review (N rounds + four axes) via a `review-policy.yaml` profile; clamped by caps. Default `default` = one low pass. Protocol: `references/multi-round.md` |

Flags are composable: `hs:code-review --high #123 --fix --reply` (or `--effort high`) runs a high-recall review, the fix loop, then posts the final review.

## Workflow

```
1. Parse argument → determine mode + flags + effort. Map `--effort <level>` or the shorthand `--<level>` (or a bare leading token) to the level, then resolve via `review_recall.py --effort <level>`
1.5 Scope read (recall-mode.md): print `scope · N files · signals → suggested effort`
   (review_recall.assess_scope). On mismatch vs resolved effort — no --auto →
   AskUserQuestion [bump (rec)/keep/other]; headless → record + proceed at resolved;
   --auto → self-bump + log. Agent never auto-bumps without --auto.
2. Edge-case scout (required when ≥3 files changed OR the diff carries a contract-delta — see references/contract-delta.md): hs:scout before review
2.5 Review-rules layer (references/review-rules-layer.md):
   - rules ← rule_view.load_rules_dual(root, changed_files) (operational rules from the standards tree — the single source)
   - judge applied rules → violations[]; write rule-scan.json via rule_view.build_rule_scan (records `changed_files`, the coverage-gate's diff source)
   - any violation severity==critical → verdict BLOCKED (review-decision BLOCKED too)
   - only info → PASS_WITH_RISK / Suggestion
2.7 Risk ceremony (references/risk-ceremony.md): risk_rubric.derive_risk(root, changed_files)
   → high_risk (auth/migration/secret/api) ⇒ run hs:security-scan to PASS + reviewer≠author before verdict
   (skill-enforced, NOT a hard gate — security-scan is not in stage requires:; see risk-ceremony.md boundary)
3. [If --spec] Stage 1 — Spec compliance (references/spec-compliance.md)
   → FAIL? fix → re-review Stage 1 → then continue
4. Stage 2 — Quality review (effort-scaled, **subagent-by-default unless --in-place/--inline**):
   - **Route first through `hs:workflow-orchestrate`** (for effort ≥ medium, when the review fans out,
     before any spawn): state `reason` (why this recall fan-out), `strategy` (mode + `hs:base-pipeline-verify`
     + lens→count by effort), `scope` (diff surface + lens count from `code-review.yaml`). Findings feed a
     verdict, so this is a **find→verify** shape. Consume `route_depth` — `light` → proceed via the
     recall-mode ladder; `agent` → escalate the `@workflow-orchestrator` agent before spawning. The exact
     sizing commands + the `groupCap`/`earlyWrite` handoff live in `harness/rules/orchestration-protocol.md`.
   - no `--in-place`:
     - effort==low → spawn the `@code-reviewer` agent (do not review inline in the main agent).
     - effort≥medium → load references/recall-mode.md (fan-out lenses → verify → sweep;
       high+ via the ultracode Workflow tool — shared hs:base-pipeline-verify base,
       inline + Task fallback). Every path feeds the SAME verdict-truth-table +
       dismissals + review-decision.json.
   - `--in-place` → the main agent may perform the review directly; this is opt-in only
     and must be logged in the report (stamp: `in-place:true`).
   Checklist: correctness · security · breaking-changes ·
   performance · testing · anti-slop · architecture (references/review-dimensions.md)
   Architecture (structural diffs): if `changed_files` intersect `standards.yaml
   drift.structural_globs`, the reviewer MUST load `docs/system-architecture.md`, judge
   component-map / layer-boundary / store-&-hook-contract drift, and record
   `architecture_review{checked, doc_sha, drift[]}` in review-decision.json. Skipping it
   on a structural diff blocks a hard stage (artifact_check presence gate).
   On the naming/anti-slop pass, read the glossary (`glossary_register.py --root . --list`,
   or the `docs/glossary.yaml` SSOT — `GLOSSARY.md` is its view): name with the settled
   vocabulary, do NOT re-coin an existing term; using a term's registered forbidden
   wording is a finding.
5. Verdict: Approve / Request changes / Comment. Per-finding confirmed / dismissed / needs-human, code_evidence mandatory on confirmed, any condition FALSE ⇒ dismissed (references/verdict-truth-table.md). Read `harness/rules/scoring-rigor-contract.md` before scoring — the severity taxonomy below pins to the harness's existing scales, never a 4th vocabulary.
   Before finalizing, lookup the dismissals store by fingerprint and SHOW any prior dismissal — never auto-hide (harness/scripts/dismissals_store.py).
6. Write artifact via write_review_decision.py (stamps run_seq + atomic write — NOT a hand-written YAML); see references/verdict-and-artifact.md
7. [If --fix] apply no-confirm fixes only (skip+report risky); [If --fix-auto]
   hs:fix --auto (self-decide all) → hs:git cp → re-review. --auto never implies fix.
8. [If --reply] Post review to PR (references/pr-review-workflow.md)
```

## Severity taxonomy

| Level | Meaning | Action |
|---|---|---|
| **Critical** | Bug, security, data loss, breaking change | Must fix before merge |
| **Important** | Wrong logic, missing validation, structural slop | Should fix |
| **Suggestion** | Style, minor, micro slop | Nice-to-have |

Full taxonomy + anti-slop patterns: `references/severity-taxonomy.md`.

**Complexity ladder (anti-slop sub-dimension).** On the anti-slop pass, judge whether new code
could drop to a lower rung of the minimal implementation ladder — **Delete** /
**Standard library** / **Existing** dependency-utility / **Tiny** change / **Shrink**. This **adds** to the
anti-slop taxonomy above, it does not replace it: report a complexity note separately and never
let it override a correctness, security, or scope finding.

## Output language

Render reports per `harness/rules/output-rendering.md`: resolve `language` / `audience` / `humanize` live via `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/output_config.py --resolved` (never hand-read the tracked file); the rule holds the register behavior and the evidence-invariant fence.

## Boundaries

- Do NOT self-merge, do NOT self-deploy; review is a gate — the merge decision belongs to the human.
- Verdict `BLOCKED` → write artifact with `verdict: BLOCKED` → `gate_stage.py` blocks pr/ship/deploy (verdict != PASS) and the artifact records the blocking reason for audit/replay.
- Verdict `PASS` → artifact written with verdict PASS → a hard stage may proceed.
- Verdict `PASS_WITH_RISK` → artifact written, but a HARD stage (pr/ship/deploy) still BLOCKS: only an exact PASS clears it. `PASS_WITH_RISK` is a conscious soft-accept (record the risk, keep moving on soft stages), never a ship license.
- Re-review cycle maximum 3 times — if no convergence, escalate to user.
- Do not modify files outside `plans/reports/` (reports) and the active plan's artifact path.
- On completion: report verdict + absolute artifact path + unresolved questions.

## HARD-GATE (real wiring)

```
harness/hooks/gate_stage.py          — blocks stage pr|ship|deploy when
                                       plans/<plan>/artifacts/review-decision.json
                                       is absent or verdict != PASS
harness/schemas/artifact-review-decision.json
                                     — verdict schema (PASS|PASS_WITH_RISK|BLOCKED)
                                       + reviewer + role + rationale + plan_hash
harness/plugins/hs/agents/code-reviewer.md
                                     — agent that performs the review (Stage 2)
plans/reports/                       — long-term review report storage
```

Stage policy is read from `harness/data/stage-policy.yaml` — when a stage's `requires:` lists `review-decision`, the gate activates for that stage. The gate is a presence gate (verifies artifact EXISTS and verdict satisfies policy), not a role gate.

## Verification gate

Before any claim of "review complete" or "PASS":

1. **IDENTIFY** — which command proves this claim?
2. **RUN** — run it fully, do not reuse cached results
3. **READ** — read the output, count failures, check exit code
4. **VERIFY** — does the output confirm? If not → report the true status
5. **THEN claim** — with evidence

No verification evidence → no PASS claim. (This rule has no exceptions.)

## Observe checkpoint (end-of-work)

When the review is done, if this run surfaced a judgment a counter cannot see, record ONE closed-vocab signal so the harness learns from it — emit only a REAL observation, not every run. Vocabulary lives in `harness/data/observation-signals.yaml`.

```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/emit_observation.py --skill hs:code-review \
    --signal <thin-evidence|gate-repeat-block|trigger-near-miss> \
    --payload "<one line: what happened>"
```

Surfaces in the read-only `observations` lens (honesty-gated). Skip it silently when nothing notable happened — a fabricated signal is worse than none.

**Two-output nudge** — if this review (a) hit the same friction a prior review did, or (b) produced a rule worth keeping, suggest capturing it: `hs:remember` for a durable rule/decision, or add a per-repo review rule via the rule-author skill (writes `standards.user.yaml`). A reusable insight that dies with the run is a wasted review. (Suggestion only — no new gate, no heavy
process.)

## Workflow position

Typically runs after: `hs:cook` (review after implement) · `hs:fix` (review after bug fix) Typically runs before: `hs:git` ship · `hs:afk` submit
Related: `hs:scout` (scout before review) · `hs:test` (test before review)

## References (load on demand)

| Drawer | Content | When to load |
|---|---|---|
| `references/checklists/base.md` | Universal two-pass checklist (critical/informational): injection, race conditions, security boundaries, auth, test gaps, performance | Always load when running checklist-based review |
| `references/checklists/web-app.md` | Overlay for frontend frameworks: XSS, CSRF, N+1 queries, a11y, responsive layout | When project has React/Vue/Svelte/Next or JSX/TSX in diff |
| `references/checklists/api.md` | Overlay for REST/GraphQL/gRPC: rate limiting, input validation, data exposure, API design, observability | When project exposes API routes or OpenAPI spec exists |
| `references/checklist-workflow.md` | Step-by-step workflow to apply checklists: auto-detect type, load overlays, two-pass diff review, suppression check, output format | When running structured pre-landing or security-audit review |
| `references/recall-mode.md` | Effort-scaled finding production: effort table, scope read + `--auto`, fan-out → verify → sweep, `high`+ ultracode Workflow orchestration + inline fallback, "gate unchanged" | When effort ≥ medium (recall-mode) |
| `references/multi-round.md` | Multi-round committing review: profiles, the round loop, the four axes (compounding/per_aspect/blind_main_sub/refute), caps, `--profile`/`--rounds`, `rounds_run` stamp | When `--profile`/`--rounds` is used |
| `references/feedback-reception.md` | How to RECEIVE review feedback: triage findings, push back with evidence, resist reflexive agreement | When acting on a review as the author, or coaching feedback reception |
| `references/issue-routing.md` | Where a deferred finding goes: BACKLOG entry + report link (headless) or interactive routing | When routing a finding that will not be fixed this round |

## Auto-decision ledger

When acting autonomously (editing or deciding without asking), append one advisory line per governance-bearing decision — see `harness/rules/auto-decision-ledger.md`.
