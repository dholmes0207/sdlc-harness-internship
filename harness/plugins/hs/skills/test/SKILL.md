---
name: hs:test
injectable: false
description: Run and validate tests for the current change — unit/integration profiles, concise QA report, 100% pass gate. Use when a change needs its tests run and a gate-ready verdict.
argument-hint: "[unit | integration] [--in-place]"
allowed-tools: [Bash, Read, Write, Grep, Glob, Task]
metadata:
  compliance-tier: workflow
---

# hs:test — disciplined verification

Run tests for the scope that just changed; report results truthfully — a failure is information, not an enemy.

**Default: delegate the suite run to a `@tester` subagent** (isolates the large test output from the
main context); `--in-place` opts out and runs at main. In the delegated flow the `@tester`
**runs + reports only** (verdict + `checks[]` + Unmapped); **main persists `verification.yaml`** from
that report. The Pre-flight / Profiles / Result-rules sections below describe what gets run and how the
artifact is written — main routes on them; they are not "run it all yourself at main."

**General rules**: `harness/rules/tdd-discipline.md` (100% pass, fix do not weaken) + `harness/rules/verification-mechanism.md` (verdict + checks[] are the evidence the gate reads). When human review of verdict/QA report is needed, apply `harness/rules/plannotator-review-gates.md` (diff → `review`, report → `annotate`).

## Pre-flight

1. **Resolve the test command for this repo's stack FIRST.** If the target is not Python (no `pytest`/`pyproject.toml`), run `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/detect_techstack.py --root <repo>` and use the reported `test_cmd` (e.g. `go test ./...`, `pnpm test`) — do not assume `pytest`. A `test_cmd: null` means the stack declares no runner; ask which one before proceeding.
2. `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/preflight_deps.py` — Python targets: if pytest/PyYAML is missing, stop with the install command; do not run blindly.
3. Quick import-check of the recently modified module (catching import errors here is cheaper than mid-suite).

## Profiles

| Profile | Scope | When |
|---|---|---|
| `unit` (default) | test the modified module → full unit suite | every TDD cycle |
| `integration` | add e2e (`harness/e2e/run_vertical_slice.py`) | before a hard stage |

Standard command: `python3 -m pytest harness/tests/ -q` (target repos use their own suite per standards — for a non-Python target use the `test_cmd` from Pre-flight step 1).

`unit` / `integration` here are **run-scope profiles** (how much to run). Do not confuse them with the canonical `test_type` DoD names the gate keys on (Result rules below) — running the `unit` profile is not the same as emitting a canonical `unit` check.

**Focused first pass** (large suite, small change): run only the tests a change can break, then the full suite before the gate — the bundled selector walks the import graph in reverse (it is a SUPERSET, never a replacement for the full pre-merge run):

```bash
python3 "${HARNESS_BIN_ROOT:-.}"/harness/plugins/hs/skills/test/scripts/affected_tests.py --base main --pytest
python3 "${HARNESS_BIN_ROOT:-.}"/harness/plugins/hs/skills/test/scripts/affected_tests.py --changed harness/scripts/foo.py
```

Detailed scope by change type (feature/fix/refactor/dep-bump) and coverage thresholds (line ≥80%, branch ≥70%) → `references/coverage-and-edge-cases.md`.

## Result rules

- **100% pass is the gate** (tdd-discipline rule): failure → fix the code or fix a genuinely wrong test (with a reason); deleting/skipping/weakening tests to fake green is forbidden.
- QA report <200 lines: list **ALL** test failures (name + 1-line reason), notable coverage changes, final verdict: PASS / PASS_WITH_RISK (state the risk) / BLOCKED. Full template → `references/qa-report-format.md`.
- Verdict + checks[] written to `plans/<plan>/artifacts/verification.yaml` (json accepted as legacy)
  (schema `harness/schemas/artifact-verification.json`) — the artifact the hard stage gate reads.
  **Owner: in the delegated flow MAIN writes it** from the `@tester`'s reported checks[]; a standalone
  `@tester` (top-level actor, no orchestrating main) writes its own. How to write it correctly +
  resolve_actor → `references/verification-artifact.md`.
- **Never write `verification.yaml` with a raw Bash redirect** (`>` / `cat >`) — it does not trip the PostToolUse hook, so the plan lifecycle never flips and the ship gate silently blocks. Write it via `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/write_verification.py` (preferred) **or** the Write tool — both trip the lifecycle hook.
- Each DoD-bearing check uses the **canonical `test_type` name** (`harness/data/test-policy.yaml`
  → `test_types`; map the runner: jest→`unit`, etc.) and carries `format` + a `file:` the gate re-derives from. Emit a machine-readable result file per stack:

  | stack | runner | reporter → JUnit | file |
  |---|---|---|---|
  | Python | `pytest` | `--junitxml` | `junit.xml` |
  | JS | `jest` | `jest-junit` | `junit.xml` |
  | Go | `go test` | `gotestsum --junitfile` / `go-junit-report` | `junit.xml` |
  | Rust | `cargo test` | `cargo2junit` | `junit.xml` |
  | Java | `mvn`/`gradle test` | surefire/gradle (native JUnit) | `target/surefire-reports/*.xml` |

- **MANDATE — after writing the artifact, before handing off to any hard stage**, the artifact writer
  (main in the delegated flow; the standalone `@tester` otherwise) runs the well-formedness validator
  and fixes anything it reports:
  `python3 "${HARNESS_BIN_ROOT:-.}"/harness/scripts/artifact_check.py --validate-verification plans/<active-plan>`
  (exit 1 = a non-canonical name or a phantom/unparseable result file — the push gate would block on it).

## Delegation + verdict-route (delegate-by-default)

The suite run is **delegate-by-default** to a `@tester` subagent — the win is isolating the (often large) test output from the main context. The `@tester` **runs + reports ONLY**: verdict + `checks[]` + the **Unmapped** list of code files with no covering test.
It **does not write tests**, and it does not re-spawn `@tester`/`/hs:test` (runs the suite directly via Bash in its own process). `--in-place` runs the suite straight at main (opt-out).

The main thread then routes on the verdict against a **DoD-anchored threshold**: a missing required `test_type` (a DoD FAIL) is a LARGE gap → `@developer` writes the test test-first; a coverage nicety or <2 unmapped files is SMALL → main fixes inline.
Full route table + the counting fallback: `references/delegation-route.md`.

## Fix loop and regression

When failing: QA report → hand off to hs:cook (fix) or hs:fix (single bug) → re-run. A bug fix must have a regression test written BEFORE the fix (intentional failure). Regression scope and build verification checklist → `references/regression-and-build.md`.

## Boundaries

hs:test **only runs and reports** — it does not modify code, does not weaken tests, and does not decide on merge. Modifying code → hs:cook / hs:fix. Post-test review → hs:code-review. Evidence validation → hs:debug when root cause needs deep tracing.

## Related skills

- `hs:manual-test`: exploratory / manual API or UX checks no result file captures; emits a `manual` evidence-tier check into the same verification.yaml.

## HARD-GATE (real wiring)

`gate_stage.py` reads verification.yaml (json legacy): any check `FAIL` → hard stage is blocked. Fraudulent reporting (PASS while failing) surfaces on the next CI re-run; the trace ledger keeps a record of who wrote what (attribution, verification-mechanism rule).
