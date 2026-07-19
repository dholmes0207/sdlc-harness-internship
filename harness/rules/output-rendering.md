# output-rendering.md — how a report skill/agent renders human-facing prose

Load before generating any report, doc, plan narration, or human-facing summary.
This is the single home of the report-rendering contract. The 20 report skills and
agents (plus `hs:setup`, which carries the same tokens inside its config preamble)
each carry one identical one-line pointer to this file — the register BEHAVIOR
(audience tiers, the humanize default, the evidence list) lives only here, never
restated in a stanza, so the contract changes in exactly one place. A drift test
(`test_audience_mirror_drift.py`) pins the pointer line verbatim across all of them.

## Resolve the live values FIRST

```bash
python3 harness/scripts/output_config.py --resolved
```

`--resolved` prints `resolve_all()` — the terminal-voice axes and the output-config
register knobs (`language`, `humanize`, `audience`, `code_style`) merged, honoring the
dev override (`HARNESS_OUTPUT`). It is the ONLY sanctioned read. **Do NOT hand-read the
tracked `harness/data/output.yaml`**: that file is the fail-closed gate path and ignores
the dev override by design, so a hand read returns the wrong value for a developer who
set a knob locally. A subagent never receives the session inject, so this resolve is how
it learns the register at all.

## Apply the knobs

- **`language`** (`en` | `vi`, default `vi`): write the prose in this language. Instruction
  text stays English; only the GENERATED prose follows it.
- **`audience`** (`off` | `0..5`, default off): the prose register for the report.
  - `0–1` (plain / guided): open with a plain-language "so what" summary, define every
    term inline on first use, and close with a short glossary.
  - `2–3` (informed / practitioner): normal domain vocabulary, define only specialist terms.
  - `4–5` (expert / peer): dense, terse, lead with the load-bearing point; at 5 assume full
    shared context.
  - The same `audience` value also shapes the terminal chat register (injected per session);
    here it shapes the written report.
- **`humanize`** (`true` | `false`, **default off**): apply
  `harness/data/output-styles/humanizer-and-anti-ai-tells.md` (strip AI-writing tells; when `language: vi`,
  also the Vietnamese translation-tells) ONLY when resolved true. Default is off to save
  tokens — turn it on when publishing a report externally.
- **`code_style`** (`off` | `0..5`): shapes generated CODE only (comment density, verbosity,
  examples). It does NOT alter report prose — that is `audience`. Most report rendering does
  not touch it.

## Evidence is invariant at every level

`audience` and `humanize` shape ONLY the surrounding prose. Evidence tokens —
`file:line` references, IDs, SHAs, numbers, and verbatim quotes — are never translated,
re-rounded, paraphrased, or rewritten at any `audience` level or with `humanize` on. Copy
them exactly as found.
