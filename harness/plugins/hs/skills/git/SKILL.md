---
name: hs:git
injectable: false
description: "Git operations with conventional commits. Use for commit, push, PR, merge. Auto-split commits by type/scope. Scan for secrets before committing."
allowed-tools: [Bash, Read, Grep, Glob, Task]
argument-hint: "cm|cp|pr|merge|merge-pr [args]"
metadata:
  compliance-tier: workflow
---

# hs:git — git operations

Delegates verbose git ops to the `@git-manager` agent. This skill is the thin orchestration core.

## Default (no argument)

Use `AskUserQuestion` to ask which operation:

| Operation | Description |
|-----------|-------------|
| `cm` | Stage + commit |
| `cp` | Stage + commit + push |
| `pr` | Create Pull Request |
| `merge` | Merge branch |
| `merge-pr` | Merge an open PR (gh) |

## Arguments

- `cm` — stage + commit
- `cp` — stage + commit + push
- `pr [to-branch] [from-branch]` — PR (default: main <- current)
- `merge [to-branch] [from-branch]` — merge (default: main <- current)
- `merge-pr [number] [--squash|--merge|--rebase]` — merge an existing open PR via `gh pr merge` (default: the current branch's PR, `--squash`); load `references/workflow-merge-pr.md`

## Core workflow

1. **Stage + analyze**: `git add -A && git diff --cached --stat && git diff --cached --name-only`
2. **Secret scan (required before commit, MUST NOT skip)**: scan the staged diff for secrets. **Secret found → STOP immediately.** Warn the user, suggest `.gitignore` / environment variables. Do not continue committing until the user confirms the issue is resolved. Exact scan command + STOP procedure: `references/workflow-commit.md` (Tool 1) / `references/safety-protocols.md`.
3. **Split decision + commit**: single-commit (same type/scope, FILES <= 3, LINES <= 50) vs multi-commit grouping (config/deps/test/code/docs separately), then `git commit -m "type(scope): description"`. Full grouping logic + standards: `references/workflow-commit.md` (Tools 2-3), `references/commit-standards.md`.

## Output format

```
✓ staged: N files (+X/-Y lines)
✓ security: passed
✓ commit: HASH type(scope): description
✓ pushed: yes/no
```

## Error handling

| Error | Action |
|-------|--------|
| Secret found | Block commit, show file |
| No changes | Exit cleanly |
| Push rejected | Suggest `git pull --rebase` |
| Merge conflicts | Report to user |

## Quick reference

| Task | Reference |
|------|-----------|
| Commit | `references/workflow-commit.md` |
| Push | `references/workflow-push.md` |
| Pull Request | `references/workflow-pr.md` |
| Merge | `references/workflow-merge.md` |
| Merge open PR | `references/workflow-merge-pr.md` |
| Commit standards | `references/commit-standards.md` |
| Safety / secret | `references/safety-protocols.md` |
| Branch lifecycle | `references/branch-management.md` |
| GitHub CLI | `references/gh-cli-guide.md` |

## HARD-GATE (real wiring)

**Pre-push hook** `harness/install/git-pre-push-hook.sh` blocks ALL pushes at the transport layer — regardless of whether push is invoked via alias, eval, wrapper, or `sh -c 'git push'`:
- Scrubs all `HARNESS_*` env before checking (prevents override)
- Calls `artifact_check.check_stage("push", root)` — missing `verification.json` -> exit 2
- `python3` not found -> fail-closed exit 2 (gate does not silently pass)

**git-manager agent** (`harness/plugins/hs/agents/git-manager.md`): receives delegated verbose git ops (2-4 tool calls); re-invokes the `hs:git` skill internally.

Do not modify `.git/hooks/pre-push` to bypass the gate — any anomaly -> ask a human.

## Boundaries

- MUST NOT commit if the secret scan matches — STOP and ask the user before continuing.
- NO force push to `main`/`master`/`production` — warn if user requests it.
- Do NOT push automatically when the user only invokes `cm` (without `cp`).
- Do not write AI attribution into commit messages.
- YAGNI: do not create new remotes/configs outside the request scope.
- Activate `hs:context-engineering` if context is nearly full before a large op.
