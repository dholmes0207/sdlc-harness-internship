# Parallel execution for cook (opt-in)

`cook --parallel` cooks independent phases concurrently to cut wall-clock. **Off by default** — fresh install behaves exactly as before. This drawer = full protocol; SKILL.md section = summary. Backing rule: `harness/rules/orchestration-protocol.md` (delegation context, parallel-ownership safety, `claims.py` 1-winner, status protocol).

## When NOT to use it

Parallelism buys nothing — and adds merge risk — when phases are a dependency chain, when ownership cannot be cleanly split, or when the plan has ≤2 independent phases. If in doubt, stay sequential. The default already is.

## 1. Resolve the opt-in (deterministic, not eyeballed)

Precedence, highest first:

1. `--parallel` flag on the cook invocation
2. `HARNESS_COOK_PARALLEL` env (`1/true/yes/on` → on)
3. `cook.parallel` in `harness/data/cook.yaml`
4. default **false**

`cook.parallel_max` (default 4) is an advisory cap the orchestrating agent applies when fanning out — the planner below emits the safe partition but does NOT itself limit concurrency:

```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/cook_parallel_plan.py --root . --phases-json phases.json --expand
# add --parallel to force ON; prints {parallel_enabled, parallel, sequential, conflicts}
```

## 2. Build the phase list

Each phase file declares its parallel eligibility in frontmatter:

```yaml
parallel_safe: true
owns:
  - harness/hooks/simplify_gate.py
  - harness/tests/test_simplify_gate.py
```

`owns` is the set of paths (globs allowed) the phase may create or modify. A phase with no `parallel_safe: true` is always sequential.

## 3. Partition (safety core)

`cook_parallel_plan.partition()` returns three lists:

- `parallel` — `parallel_safe` phases whose `owns` are disjoint from every other `parallel_safe` phase. These may run concurrently.
- `sequential` — everything else: non-parallel-safe phases, plus any phase whose ownership **overlaps** another (overlap demotes BOTH, conservatively).
- `conflicts` — the overlapping pairs and the shared paths, so the fallback to sequential is logged, never silent.

Rule, non-negotiable: **never run two phases that touch a shared path in the same batch.** Same file, a generated artifact, a migration sequence, or shared config all count as shared.

## 4. Delegate each parallel slice

One `hs:developer` subagent per slice, in its own **worktree** (isolation prevents parallel edits from colliding). Pass `isolation="worktree"` **explicitly on the spawn** — `@developer` no longer
self-isolates from its frontmatter, so a slice spawned without the param would run in-place and collide with its peers.
The prompt MUST carry the full delegation context (orchestration-protocol): task · files allowed to read · files allowed to modify (the phase's `owns`) · acceptance criteria · constraints · work-context path · env (CWD, OS). Ownership is enforced 1-winner via
`harness/scripts/claims.py`.

**Worktree base ref (non-negotiable for dependency-coupled phases).** An isolated worktree branches from the configured base ref — which may be the fresh origin-default, NOT the current branch HEAD. A parallel slice fanned out in the same run as an earlier phase it depends on (it reads a module/file the predecessor just committed) will find that prerequisite ABSENT in the worktree, and the
subagent will improvise — reconstructing the predecessor's files inside its own slice, violating ownership and producing a diff that cannot merge cleanly. Before delegating ANY slice whose phase depends (per the plan-graph edges) on an in-session committed phase: (a) ensure the worktree branches from the current HEAD (so prerequisite phases are present), and (b) verify each prerequisite file
is present in the worktree (`ls <worktree>/<prereq>`). An absent prerequisite means the base ref is wrong — STOP, fix the base, do not let the subagent rebuild it. A phase with no in-session dependency may use the default base. (See `harness/LESSONS.md`.)

Each subagent ends with the status protocol block (`DONE | DONE_WITH_CONCERNS | BLOCKED | NEEDS_CONTEXT`). `BLOCKED`/`NEEDS_CONTEXT` → change scope/approach, do not re-run the same prompt.

## 5. Verify every slice — MANDATORY, never trust on sight

A returned subagent is an **unverified claim**, not a result. Two tiers:

1. **Self-verify (always)**: cook re-runs that slice's tests + lint and reads the diff against the phase's acceptance criteria. Any red → the slice does not merge.
2. **Independent verify (risky slices)**: spawn an `hs:independent-revalidator` (or `hs:code-reviewer`) subagent that re-derives correctness from the diff ALONE, without the builder's reasoning. Disagreement → the slice returns to sequential rework.

Risky = touches a gate/hook, changes a contract, or has thin test evidence. When unsure, verify independently.

## 6. Integration barrier

After all verified slices merge into the working tree, run the **full suite serially** — the real green gate. Only then write `plans/<plan>/artifacts/verification.json` and commit. `gate_stage.py` is unchanged: `--parallel` never bypasses the artifact gate.

## Failure handling

- Partition reports a conflict → those phases run sequentially; log the shared paths.
- A slice fails verification → pull it out of the parallel batch, rework sequentially.
- Worktree merge conflict → ownership was mis-declared; fix `owns`, fall back to sequential.
- The integration suite goes red after a clean per-slice pass → an integration gap the per-slice tests missed; bisect by slice, this is exactly what the barrier exists to catch.
