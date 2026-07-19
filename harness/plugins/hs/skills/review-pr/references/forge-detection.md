# Forge detection (GitHub `gh` ↔ GitLab `glab`)

This is a **tier-1** (developer-tool) capability: `hs:review-pr` reviews a PR/MR on whichever forge the repo uses. **Independent** of the tier-2 orchestrator's GitLab support — a separate **declared seam** (deferred): enabling GitLab here does NOT enable it there.

Detect the hosting forge **before any review command**, then use exactly one CLI for the rest of the run. Never mix `gh` and `glab` in a single review.

```bash
REMOTE_URL="$(git remote get-url origin 2>/dev/null || true)"
case "$REMOTE_URL" in
  *gitlab*) echo "FORGE=gitlab CLI=glab" ;;
  *github*) echo "FORGE=github CLI=gh" ;;
  *)        echo "FORGE=unknown CLI=unknown REMOTE=$REMOTE_URL" ;;
esac
```

## Detection rules

- Remote hostname/path has `gitlab` → use `glab` + GitLab **MR** terms.
- Remote hostname/path has `github` → use `gh` + GitHub **PR** terms.
- GitHub Enterprise and self-managed GitLab supported when the remote URL clearly contains `github`/`gitlab`.
- **Unknown forge** → inspect all remotes (`git remote -v`); ask the user only if repo state can't answer it. Do NOT guess — a wrong-forge command is worse than a question.
- Verify the selected CLI exists and is authenticated before running:
  - GitHub: `command -v gh` and `gh auth status`
  - GitLab: `command -v glab` and `glab auth status`
- If the selected CLI is missing/unauthenticated, stop with the install/auth command needed. Do NOT silently fall back to the other forge.

## Command mapping

| Operation | GitHub (`gh`) | GitLab (`glab`) |
| --- | --- | --- |
| View metadata | `gh pr view "$PR_REF" --json ...` | `glab mr view "$PR_REF"` (text) — for JSON: `glab api "projects/:fullpath/merge_requests/<iid>"` |
| List / search prior work | `gh pr list --state all --search "<terms>" --json ...` | `glab mr list --all --search "<terms>"` |
| Fetch diff | `gh pr diff "$PR_REF"` | `glab mr diff "$PR_REF"` |
| Check out branch | `gh pr checkout "$PR_REF"` | `glab mr checkout "$PR_REF"` |
| Read comments | `gh pr view "$PR_REF" --comments` | `glab mr view "$PR_REF" --comments` |
| Post comment / approve | `gh pr review "$PR_REF" ...` | `glab mr note "$PR_REF" -m "<body>"` (comment); `glab mr approve "$PR_REF"` (approval) |
| CI / pipeline status | `gh pr checks "$PR_REF"` | `glab ci status` (on the MR branch) — or the pipeline field via `glab api "projects/:fullpath/merge_requests/<iid>"` |

## GitLab notes (probed against `glab` 1.36.0)

- `glab mr view` accepts an MR id or branch; flags: `-c/--comments`, `-p/--page`, `-P/--per-page`, `-s/--system-logs`, `-w/--web`, `-R/--repo`. **No** `--output`/`--jq` — prints text; for machine-readable output use `glab api` (REST v4).
- `glab mr list` defaults to open MRs; add `-A/--all` for duplicate/prior-work searches, filter with `--search "<string>"`. No JSON flag either — use `glab api` for structured output.
- `glab mr note` (alias `glab mr comment`) posts a comment: `glab mr note "$PR_REF" -m "<body>"`; `--unique` skips duplicates. **No** `note create`/`note list` subcommand — read comments via `glab mr view "$PR_REF" --comments`.
- GitLab has no direct `gh pr checks` equivalent: read pipeline state via `glab ci status` or MR JSON via `glab api`.
