"""Regression guard: the retired OTEL collector/sidecar dogfood tool leaves no dangling
reference in active tracked files.

The monitoring dogfood was migrated to the langfuse-observability Claude Code plugin; the
collector+sidecar tree and its OTEL-pipeline launchers were removed. Active code/config must
not point at the deleted paths. Historical prose (plans/, docs/) legitimately records the
retirement and is exempt — this guard covers referrers that would actually route work to a
tool that no longer exists.

Banned tokens are assembled from fragments so this test file does not match its own guard
(the check greps tracked files in the working tree).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Fragment-built so the guard never self-matches when this file is tracked. Both tokens name
# the retired tool specifically; a bare OTLP port (:4318) is deliberately NOT banned — it is a
# generic endpoint a legitimate future monitoring setup could reuse, and either token below
# already catches any resurrection of the collector/sidecar tree by name.
BANNED_TOKENS = [
    "cc-otel" + "-langfuse",   # the collector/sidecar tree
    "claude" + "-otel",         # the retired OTEL-pipeline launcher
]

# Historical/prose homes that legitimately record the retirement.
EXEMPT_PATHSPECS = [
    ":!plans/",
    ":!docs/",
    ":!harness/tests/test_no_cc_otel_collector_refs.py",
]


def _tracked_hits(token: str) -> list[str]:
    cmd = ["git", "grep", "-I", "-l", "-e", token, "--", "."] + EXEMPT_PATHSPECS
    proc = subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True)
    # git grep exits 1 with no output when there are no matches — that is the pass case.
    return [line for line in proc.stdout.splitlines() if line.strip()]


def test_no_dangling_collector_refs_in_active_tracked_files():
    offenders = {tok: _tracked_hits(tok) for tok in BANNED_TOKENS}
    dangling = {tok: files for tok, files in offenders.items() if files}
    assert not dangling, (
        "Retired collector/sidecar still referenced by active tracked files "
        f"(exempt: plans/, docs/): {dangling}"
    )
