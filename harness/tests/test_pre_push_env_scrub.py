"""test_pre_push_env_scrub.py — the transport gate ignores HARNESS_* env.

The env-override posture hole at the transport layer: HARNESS_ROOT /
HARNESS_STAGE_POLICY / HARNESS_ACTIVE_PLAN (and any future HARNESS_* knob a
loader grows) could point the pre-push gate at permissive config, changing
its verdict with no git-visible diff. The hook now scrubs the ENTIRE
HARNESS_* prefix (allowlist-by-prefix, not a name-by-name denylist that rots)
and resolves the repo root from `git rev-parse --show-toplevel`, so a real
push is judged ONLY by tracked config.

Tests drive the real shell hook as a subprocess inside a git-inited temp
repo carrying the real artifact_check + policy.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

_HARNESS = Path(__file__).resolve().parent.parent
_HOOK_SRC = _HARNESS / "install" / "git-pre-push-hook.sh"


@pytest.fixture()
def repo(tmp_path):
    """Git repo with the real gate code + policy and an in_progress plan."""
    root = tmp_path / "repo"
    (root / "harness").mkdir(parents=True)
    shutil.copytree(_HARNESS / "scripts", root / "harness" / "scripts")
    shutil.copytree(_HARNESS / "hooks", root / "harness" / "hooks")
    shutil.copytree(_HARNESS / "data", root / "harness" / "data")
    shutil.copytree(_HARNESS / "install", root / "harness" / "install")
    # Pin a protective branch policy so the fixture exercises the merge-grade
    # floor regardless of the dev repo's own posture — a solo dev tree ships an
    # empty floor (`protected: []`), which would otherwise make main unprotected
    # here and mask the gate logic these tests assert.
    (root / "harness" / "data" / "protected-branches.yaml").write_text(
        'protected:\n  - main\n  - master\n  - "release/*"\n', encoding="utf-8")
    plan = root / "plans" / "260612-0900-fixture"
    plan.mkdir(parents=True)
    (plan / "plan.md").write_text(
        "---\ntitle: f\nstatus: in_progress\n---\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "t@t"],
                   check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "t"],
                   check=True)
    subprocess.run(["git", "-C", str(root), "commit", "--allow-empty",
                    "-qm", "seed"], check=True)
    return root


def _push(root: Path, extra_env=None, refs=""):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    for k in [k for k in env if k.startswith("HARNESS_")]:
        env.pop(k)
    for k, v in (extra_env or {}).items():
        env[k] = v
    # The hook now captures git's ref-update stdin (PUSH_REFS="$(cat)"); pass
    # it explicitly so `cat` sees EOF instead of inheriting pytest's stdin.
    return subprocess.run(
        ["sh", str(root / "harness" / "install" / "git-pre-push-hook.sh")],
        input=refs, capture_output=True, text=True, env=env, cwd=str(root))


def _verification(plan_dir: Path):
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": plan_dir.name, "actor": "user:t",
        "ts": "2026-06-12T08:00:00+07:00",
        "checks": [{"name": "pytest", "status": "PASS"}], "verdict": "PASS",
    }), encoding="utf-8")


class TestScrub:
    def test_warns_without_artifact_baseline(self, repo):
        # Personal-first: a missing receipt WARNS (exit 0), not blocks.
        proc = _push(repo)
        assert proc.returncode == 0
        assert "[pre-push warn]" in proc.stderr and "verification" in proc.stderr

    def test_passes_with_artifact_baseline(self, repo):
        _verification(repo / "plans" / "260612-0900-fixture")
        proc = _push(repo)
        assert proc.returncode == 0, proc.stderr

    def test_feature_branch_push_is_push_grade(self, repo):
        # A push to a non-protected branch clears with push-grade artifacts.
        _verification(repo / "plans" / "260612-0900-fixture")
        proc = _push(repo, refs="refs/heads/feat x refs/heads/feat y\n")
        assert proc.returncode == 0, proc.stderr

    def test_delete_of_protected_branch_refused_at_transport(self, repo):
        # A delete of a protected branch (zero local sha) is refused outright,
        # independent of artifacts — the most irreversible transport op.
        _verification(repo / "plans" / "260612-0900-fixture")
        zero = "0" * 40
        refs = "(delete) %s refs/heads/main %s\n" % (zero, "a" * 40)
        proc = _push(repo, refs=refs)
        assert proc.returncode != 0
        assert "delete" in proc.stderr.lower()

    def test_protected_branch_push_demands_merge_artifacts(self, repo):
        # Pushing to main (protected by the repo's own tracked policy) is judged
        # at merge grade: push-grade verification alone is NOT enough — the
        # merge_gate floor demands review-decision / plan-approval / consensus.
        _verification(repo / "plans" / "260612-0900-fixture")
        proc = _push(repo, refs="refs/heads/main x refs/heads/main y\n")
        assert proc.returncode == 0  # non-destructive push to main WARNs on the receipt
        assert "[pre-push warn]" in proc.stderr and "review-decision" in proc.stderr

    def test_harness_root_pointing_at_permissive_clone_is_ignored(
            self, repo, tmp_path):
        # Strongest transport attack: a fully attacker-controlled root
        # whose artifact_check always passes. The scrub must keep the push
        # judged by THIS repo's tracked code, which blocks.
        fake = tmp_path / "permissive"
        (fake / "harness" / "scripts").mkdir(parents=True)
        (fake / "harness" / "scripts" / "artifact_check.py").write_text(
            "def check_stage(stage, root):\n    return None\n",
            encoding="utf-8")
        proc = _push(repo, extra_env={"HARNESS_ROOT": str(fake)})
        # The scrub still matters: the WARN comes from THIS repo's tracked code
        # (which reports the missing receipt), not the permissive fake root.
        assert proc.returncode == 0
        assert "[pre-push warn]" in proc.stderr and "verification" in proc.stderr

    def test_permissive_stage_policy_env_is_ignored(self, repo, tmp_path):
        soft = tmp_path / "soft-policy.yaml"
        soft.write_text(
            "stages:\n  push:\n    hard: false\n    requires: []\n",
            encoding="utf-8")
        proc = _push(repo, extra_env={"HARNESS_STAGE_POLICY": str(soft)})
        # scrub ignores the permissive policy env: tracked policy still WARNs.
        assert proc.returncode == 0 and "[pre-push warn]" in proc.stderr

    def test_unknown_future_harness_var_is_scrubbed_too(self, repo, tmp_path):
        # Denylists rot: a NEW env knob (unknown to any list) must also be
        # gone after the prefix scrub. HARNESS_ACTIVE_PLAN redirecting the
        # gate at a pre-approved plan dir stands in for "future knob" —
        # plus an entirely made-up name that must not crash the scrub.
        other = tmp_path / "elsewhere-plan"
        (other / "artifacts").mkdir(parents=True)
        (other / "plan.md").write_text(
            "---\nstatus: in_progress\n---\n", encoding="utf-8")
        _verification(other)
        proc = _push(repo, extra_env={
            "HARNESS_ACTIVE_PLAN": str(other),
            "HARNESS_FUTURE_KNOB_NOBODY_KNOWS": "x",
        })
        # still judged by the repo's own plan (scrubbed env) → WARN from tracked code
        assert proc.returncode == 0 and "[pre-push warn]" in proc.stderr

    def test_outside_a_git_repo_fails_closed(self, repo, tmp_path):
        loose = tmp_path / "loose"
        loose.mkdir()
        shutil.copytree(repo / "harness", loose / "harness")
        proc = subprocess.run(
            ["sh", str(loose / "harness" / "install" / "git-pre-push-hook.sh")],
            input="", capture_output=True, text=True,
            env={k: v for k, v in os.environ.items()
                 if not k.startswith("HARNESS_") and k != "PYTEST_CURRENT_TEST"},
            cwd=str(loose))
        assert proc.returncode != 0
        assert "git" in proc.stderr.lower()


class TestVerifyInstallPrePushCopy:
    def _verify(self, root, *flags):
        return subprocess.run(
            [sys.executable,
             str(root / "harness" / "scripts" / "verify_install.py"),
             "--root", str(root)] + list(flags),
            capture_output=True, text=True)

    def _manifest(self, root):
        subprocess.run(
            [sys.executable,
             str(root / "harness" / "scripts" / "build_manifest.py"),
             "--root", str(root)], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(root), "add", "-A"], check=True,
                       capture_output=True)
        subprocess.run(["git", "-C", str(root), "commit", "-qm", "track"],
                       check=True, capture_output=True)
        subprocess.run(
            [sys.executable,
             str(root / "harness" / "scripts" / "build_manifest.py"),
             "--root", str(root)], check=True, capture_output=True)

    def test_matching_installed_copy_is_quiet(self, repo):
        self._manifest(repo)
        hooks_dir = repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(repo / "harness" / "install" / "git-pre-push-hook.sh",
                    hooks_dir / "pre-push")
        proc = self._verify(repo, "--strict")
        assert proc.returncode == 0, proc.stderr
        assert "pre-push" not in proc.stderr

    def test_tampered_installed_copy_warns_named(self, repo):
        self._manifest(repo)
        hooks_dir = repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        (hooks_dir / "pre-push").write_text("#!/bin/sh\nexit 0\n",
                                            encoding="utf-8")
        proc = self._verify(repo, "--strict")
        assert ".git/hooks/pre-push" in proc.stderr
        assert "differ" in proc.stderr.lower()
        # Advisory by decision: the copy lives outside the manifest; the
        # check names the drift but does not fail a repo that never
        # installed the hook — and stays warn-only here too.
        assert proc.returncode == 0

    def test_missing_installed_copy_warns_but_does_not_fail(self, repo):
        self._manifest(repo)
        proc = self._verify(repo, "--strict")
        assert proc.returncode == 0, proc.stderr
        assert "pre-push" in proc.stderr
        assert "not installed" in proc.stderr.lower()
