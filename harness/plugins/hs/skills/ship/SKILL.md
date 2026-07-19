---
name: hs:ship
injectable: false
description: "Gated ship pipeline: review PASS → verification PASS → human approval → push/pr. Highest-risk stage — all gates must be real. Use when a completed branch needs to become a PR."
allowed-tools: [Bash, Read, Write, Edit, Grep, Glob, Task]
argument-hint: "[official|beta] [--skip-tests] [--dry-run]"
metadata:
  compliance-tier: workflow
---

# hs:ship — gated ship pipeline

`hs:ship` is the harness's highest-risk skill. It **does not bypass gates** — it orchestrates all prerequisites before push/PR reaches the transport layer.

**Gates (personal-first)**: `harness/hooks/gate_stage.py` + `harness/data/stage-policy.yaml` ADVISE on stage `ship` when artifacts are missing (exit 0 + `[advisory]`); the pre-push hook `harness/install/git-pre-push-hook.sh` WARNS on a missing receipt but still BLOCKS the two Tier-A floors (destructive-to-protected + secret). Hard presence enforcement is the remote receipts-gate.

**`hs:ship` (this skill) ≠ the `ship` stage.** The `ship` stage in `stage-policy.yaml` is triggered by release/publish verbs (`gh release create`, `npm publish`, `docker push`) — which this pipeline never issues. Two gated transport points:
- **Step 9 `git push`** → the `push` stage, which requires only `verification` (verdict PASS, no FAIL) and only **ADVISES** locally.
- **Step 10 `gh pr create`** → the `pr` stage, which requires **all 3 artifacts**:
  - `verification.json` — verdict PASS (no FAIL checks)
  - `review-decision.json` — verdict **exactly PASS** (PASS_WITH_RISK does not qualify)
  - `plan-approval.json` — verdict APPROVED + plan hash has not drifted. **Personal-first SLIM: no roster, no reviewer≠author check — self-approval is allowed by design.** "No self-ship" is therefore NOT machine-enforced; treat it as a human-discipline directive if you want it.

An org wanting a stricter gate can add a 4th — `critique-consensus.json` (produced by `/hs:critique --gate`) — by listing `critique-consensus` in the `pr`/`ship` stage's `requires` in `stage-policy.yaml`. It ships **off** so a spine-only install is never blocked by a plugin it has not enabled.

## Arguments

| Flag | Effect |
|------|----------|
| `official` | Target main/master — full pipeline |
| `beta` | Target dev/beta branch |
| (empty) | Infer from branch name; ambiguous → ask |
| `--skip-tests` | Skip the test step (use when tests were already run separately) |
| `--dry-run` | Show the plan, do not execute |

> `--skip-tests` reuses the existing `verification.json` — only sound when it was
> produced in THIS branch state. A stale artifact from an earlier state passes the
> gate on evidence that no longer matches the diff (the Iron Law: a stale run is not
> a pass). Re-run tests if HEAD moved since the artifact was written.

## When to STOP (blocking)

- Currently on the target branch → ABORT
- Merge conflict that cannot be auto-resolved → STOP, show conflicts
- Tests fail → STOP, show failures
- `hs:code-review` returns `review-decision` verdict ≠ PASS → STOP, ask
- Artifact missing → **you MUST stop** — the local gate only ADVISES on the push, it will not stop you; do not lean on it. The `pr` stage (Step 10) and the remote receipts-gate are where presence is truly enforced.
- Artifact drift (plan modified after approval) → the `plan-approval` check fails on the `pr` stage; stop and re-approve

## When NOT to ask

- Commit message → compose from diff + commit log
- Patch version bump → decide autonomously
- Changelog → generate automatically (do not ask for content)
- No version/changelog file present → skip silently

## Pipeline

```
Step 1: Preflight         → check branch, mode, diff, dry-run
Step 2: Merge target      → fetch + merge origin/<target>
Step 3: Test              → delegate hs:test (auto-detect runner)
Step 4: Review            → delegate hs:code-review → write review-decision.json
Step 5: Verification      → hs:cook has written verification.json (check artifact)
Step 6: Plan approval     → plan-approval.json artifact must exist
Step 7: Changelog/Version → generate automatically, no prompts
Step 8: Release notes     → load references/release-notes.md
Step 9: Human confirm     → live AskUserQuestion confirm BEFORE push (REQUIRED — this is the description's "human approval")
Step 10: Commit + Push    → conventional commit + git push (via pre-push hook)
Step 11: PR               → gh pr create with standard body
```

**Autonomous self-ship is prohibited.** The gates are personal-first and self-approvable (an agent can
write its own PASS artifacts), so the ONLY real human checkpoint is Step 9's live confirm — the
description's "human approval" means a live `AskUserQuestion`, NOT a pre-existing `plan-approval.json`.
This MUST out-ranks the proactive "just push it" bias. (The "When NOT to ask" items below scope to commit-message / version / changelog content only — never the push itself.)

**Role split:** `hs:ship` delegates the test run (Step 3 → `hs:test`) and review (Step 4 → `hs:code-review`) but **owns git transport inline** (commit/push/PR) — no hand-off to `@git-manager`.

Detail: `references/gate-sequence.md`
Preflight checklist: `references/preflight-checklist.md`
Release notes: `references/release-notes.md`

## Step 4 — Review (real gate)

`hs:code-review` writes `review-decision.json` to `plans/<active-plan>/artifacts/review-decision.json`. Verdict must be `PASS` — `PASS_WITH_RISK` does not qualify for ship. If verdict ≠ PASS: AskUserQuestion (fix now / accept risk / cancel).

**Simplify before review**: scan the branch diff first — a special case, a duplicated pattern, or an abstraction earning its keep nowhere should be collapsed before review. A smaller diff is a cheaper review and a smaller risk surface. Pattern library: `harness/plugins/hs/skills/problem-solving/references/simplification-cascades.md`.

## Steps 5-6 — Artifact check

Before pushing, verify manually:
```bash
python3 -c "
import pathlib, sys
sys.path.insert(0, 'harness/scripts'); import artifact_check
d = artifact_check.resolve_active_plan('.')
if not d: sys.exit('No active in-progress plan resolved')
for kind in ['verification','review-decision','plan-approval']:
    p = artifact_check._artifact_path(pathlib.Path(d), kind)
    if not p.is_file(): sys.exit(f'MISSING {kind}')
    rec, err = artifact_check._load_artifact(pathlib.Path(d), kind)
    print(f'{kind}: OK', (rec or {}).get('verdict','?'))
"
```
The local gate **ADVISES** on `git push` (exit 0) — it does NOT block on a missing/failed receipt; only a destructive push to a protected branch or a detected secret hard-blocks at the transport layer. Presence enforcement = the `pr` stage (Step 11) + the remote receipts-gate. This manual check surfaces gaps early so you don't rely on a local block that won't fire.

## Output format

```
✓ Preflight: branch feature/foo, 5 commits, +200/-50 lines (mode: official)
✓ Merged: origin/main
✓ Tests: 42 passed
✓ Review: PASS (0 critical)
✓ Verification: PASS
✓ Approval: reviewer: alice (hash match)
✓ Changelog: updated
✓ Commit: feat(scope): description
✓ Pushed: origin/feature/foo
✓ PR: https://github.com/org/repo/pull/123
```

## HARD-GATE (real wiring)

| Gate | Real backing |
|------|-------------|
| Stage ship (artifact check) | `harness/hooks/gate_stage.py` + `harness/data/stage-policy.yaml` |
| Transport layer (pre-push) | `harness/install/git-pre-push-hook.sh` |
| Plan approval artifact | `harness/scripts/plan_approval.py` + `harness/schemas/artifact-plan-approval.json` |
| Review decision artifact | `harness/schemas/artifact-review-decision.json` (verdict PASS strict) |
| Verification artifact | `harness/schemas/artifact-verification.json` (no FAIL check) |

**Do not shortcut the gate.** If a gate blocks incorrectly, investigate the artifact — do not edit the hook.

## Boundaries

- DO NOT force-push any branch.
- DO NOT proceed when artifacts are missing — you MUST stop; the local push gate only ADVISES, don't lean on it (the `pr` stage + remote gate are the real enforcement).
- DO NOT commit secrets — scan the staged diff before committing (pattern: `AKIA|token|password|secret`).
- On exit: return the PR URL. If the gate blocks, return which artifact is missing.
- Enable `hs:context-engineering` if context is nearly full before this long pipeline.

## Auto-decision ledger

When acting autonomously (editing or deciding without asking), append one advisory line per governance-bearing decision — see `harness/rules/auto-decision-ledger.md`.
