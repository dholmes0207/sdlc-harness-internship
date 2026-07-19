"""test_two_zone_guard.py — the two-zone write_guard under a global (bin≠project) layout.

write_guard must serve ONE shared binary to many projects: a foreign project may
not tool-Write ANYWHERE under the shared bin (whole-bin catch-all, red-team F1),
project-side guarded paths stay guarded relative to the PROJECT root (C3), the
dogfood self-hosted case (bin==project) keeps legacy single-root behavior (C4),
and an unresolved project under a global bin fails CLOSED (C5/F2) — never a silent
allow. Self-host is detected by HARNESS_BIN_ROOT being UNSET (a global bin is
itself a git checkout, so a `.git` walk-up must not collapse it).

All assertions are SUBPROCESS exit codes (the compliance fail-closed contract):
exit 2 = BLOCK, exit 0 = ALLOW. Every run scrubs the ambient root envs so the
dogfood session's own CLAUDE_PROJECT_DIR / HARNESS_* never leak in.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_GUARD = _HOOKS / "write_guard.py"

_SCRUB = ("CLAUDE_PROJECT_DIR", "HARNESS_BIN_ROOT", "HARNESS_ROOT",
          "HARNESS_DATA_ROOT", "HARNESS_STATE_DIR", "PYTEST_CURRENT_TEST",
          "HARNESS_HOOK_CONFIG")


def _run(tmp_path, tool, file_path, env_over, notebook=False):
    env = dict(os.environ)
    for k in _SCRUB:
        env.pop(k, None)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env.update(env_over)
    key = "notebook_path" if notebook else "file_path"
    payload = {"tool_name": tool, "tool_input": {key: str(file_path)},
               "session_id": "tz-test"}
    return subprocess.run([sys.executable, str(_GUARD)],
                          input=json.dumps(payload), capture_output=True,
                          text=True, env=env)


def _mk(base: Path, *rels) -> Path:
    for r in rels:
        (base / r).mkdir(parents=True, exist_ok=True)
    return base


def _global(tmp_path):
    """A global layout: bin=/…/bin (≠ project), project=/…/A."""
    binr = _mk(tmp_path / "bin", "harness/hooks", "harness/data", ".claude", "orchestrator")
    proj = _mk(tmp_path / "A", "docs", "plans/p/artifacts", ".harness/state/trace",
               "plans/p/phases")
    env = {"HARNESS_BIN_ROOT": str(binr), "CLAUDE_PROJECT_DIR": str(proj)}
    return binr, proj, env


class TestBinZoneBlocked:
    def test_c2_bin_listed(self, tmp_path):
        binr, _, env = _global(tmp_path)
        r = _run(tmp_path, "Write", binr / "harness/data/stage-policy.yaml", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_c2_bin_unlisted_catchall(self, tmp_path):
        binr, _, env = _global(tmp_path)
        r = _run(tmp_path, "Write", binr / "harness/data/new-thing.yaml", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_c2_bin_hook(self, tmp_path):
        binr, _, env = _global(tmp_path)
        r = _run(tmp_path, "Write", binr / "harness/hooks/x.py", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_f1_bin_settings(self, tmp_path):
        binr, _, env = _global(tmp_path)
        r = _run(tmp_path, "Write", binr / ".claude/settings.json", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_f1_bin_claudemd(self, tmp_path):
        binr, _, env = _global(tmp_path)
        r = _run(tmp_path, "Write", binr / "CLAUDE.md", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_f1_bin_orchestrator(self, tmp_path):
        binr, _, env = _global(tmp_path)
        r = _run(tmp_path, "Write", binr / "orchestrator/x.py", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_f4_bin_notebook(self, tmp_path):
        binr, _, env = _global(tmp_path)
        r = _run(tmp_path, "NotebookEdit", binr / "harness/x.ipynb", env, notebook=True)
        assert r.returncode == 2, r.stderr[:200]

    def test_f6_bin_case_variant(self, tmp_path):
        binr, _, env = _global(tmp_path)
        # a case-variant of the bin root must still be contained (case-insensitive
        # realpath — defends a case-insensitive FS even when detected on a
        # case-sensitive one).
        variant = str(binr).replace("/bin", "/BIN")
        r = _run(tmp_path, "Write", Path(variant) / "harness/data/x.yaml", env)
        assert r.returncode == 2, r.stderr[:200]


class TestProjectLaneRestored:
    def test_c3_proj_decisions(self, tmp_path):
        _, proj, env = _global(tmp_path)
        r = _run(tmp_path, "Write", proj / "docs/decisions.yaml", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_c3_proj_approval(self, tmp_path):
        _, proj, env = _global(tmp_path)
        r = _run(tmp_path, "Write", proj / "plans/p/artifacts/plan-approval.json", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_allow_proj_state(self, tmp_path):
        _, proj, env = _global(tmp_path)
        r = _run(tmp_path, "Write", proj / ".harness/state/trace/x.jsonl", env)
        assert r.returncode == 0, r.stderr[:200]

    def test_allow_proj_plans_body(self, tmp_path):
        _, proj, env = _global(tmp_path)
        r = _run(tmp_path, "Write", proj / "plans/p/phases/phase-1.md", env)
        assert r.returncode == 0, r.stderr[:200]


class TestSelfHostCollapse:
    def test_c4_selfhost_hook_blocks(self, tmp_path):
        # HARNESS_BIN_ROOT UNSET → self-host; HARNESS_ROOT aliases the single root.
        root = _mk(tmp_path / "R", "harness/hooks", "docs")
        env = {"HARNESS_ROOT": str(root)}
        r = _run(tmp_path, "Write", root / "harness/hooks/x.py", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_c4_selfhost_unlisted_bin_allowed(self, tmp_path):
        # dev editing its own harness/data under self-host → ALLOW (no catch-all).
        root = _mk(tmp_path / "R", "harness/data", "docs")
        env = {"HARNESS_ROOT": str(root)}
        r = _run(tmp_path, "Write", root / "harness/data/new-thing.yaml", env)
        assert r.returncode == 0, r.stderr[:200]


class TestFailClosed:
    def test_f2_git_present_no_collapse(self, tmp_path):
        # HARNESS_BIN_ROOT set + bin has .git + CLAUDE_PROJECT_DIR unset → a
        # project-lane write fails closed, NOT a self-host collapse.
        binr = _mk(tmp_path / "bin", "harness/hooks", ".git")
        proj = _mk(tmp_path / "A", "docs")
        env = {"HARNESS_BIN_ROOT": str(binr)}  # no CLAUDE_PROJECT_DIR
        r = _run(tmp_path, "Write", proj / "docs/decisions.yaml", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_c5_unresolved_project_blocks_guarded_tail(self, tmp_path):
        binr = _mk(tmp_path / "bin", "harness/hooks")
        proj = _mk(tmp_path / "A", "plans/p/artifacts")
        env = {"HARNESS_BIN_ROOT": str(binr)}  # no project env at all
        r = _run(tmp_path, "Write", proj / "plans/p/artifacts/plan-approval.yaml", env)
        assert r.returncode == 2, r.stderr[:200]

    def test_f7_alias_treated_as_bin_ne_project(self, tmp_path):
        # project dir is a symlink aliasing the bin (realpath-equal); under a
        # global layout this must NOT collapse — the bin write stays blocked.
        binr = _mk(tmp_path / "bin", "harness/hooks")
        alias = tmp_path / "alias"
        os.symlink(binr, alias)
        env = {"HARNESS_BIN_ROOT": str(binr), "CLAUDE_PROJECT_DIR": str(alias)}
        r = _run(tmp_path, "Write", binr / "harness/hooks/x.py", env)
        assert r.returncode == 2, r.stderr[:200]


class TestBreakGlassPreserved:
    def test_break_glass_skips_with_trace(self, tmp_path):
        binr, _, env = _global(tmp_path)
        (binr / "harness/data/write-guard.yaml").write_text(
            "enabled: false\n", encoding="utf-8")
        r = _run(tmp_path, "Write", binr / "harness/data/stage-policy.yaml", env)
        assert r.returncode == 0, r.stderr[:200]
