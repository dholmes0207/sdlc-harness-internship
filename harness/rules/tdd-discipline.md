# TDD discipline — shared rules for cook + test (always reference)

## Red -> green, non-negotiable

1. **Test first**: write the test for new or locked behavior, run it to **intentional
   FAIL** (wrong assert / ImportError) — do NOT skip, do NOT fake green.
2. **Implement to green**: write the minimum code to make the test pass.
3. **Run the full suite**: `python3 -m pytest harness/tests/ -q` (or the target repo's
   suite per standards) — not just the test just written.
4. **Commit the pair** test+module, conventional commit, no AI reference.

## 100% pass is a gate

- Fail means fix the **code**, or fix a **genuinely wrong test** (state the reason
  explicitly). Do not delete/skip/weaken a test to reach green. "Fix regressions, not
  the test."
- Reports must be honest: list **every** test failure (name + one-line reason); final
  verdict is PASS / PASS_WITH_RISK (state the risk) / BLOCKED.
- Verdict + checks[] are written to `verification.json` (schema
  `harness/schemas/artifact-verification.json`) — this is the hard input for the gate
  stage. See `verification-mechanism.md` for evidence rules.
