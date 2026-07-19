"""test_protected_ref_guard.py — PreToolUse(Bash) refusal hook for protected refs.

Two refusals, defense-in-depth ahead of the transport pre-push hook:
  * force-push / history-rewrite to a protected ref → FLOOR block
    (protected_ref_force_push; never lowered by a preset).
  * a direct `git commit` while the current branch is protected → enforcement
    block (protected_ref_commit; lowers to warn under lenient).
A missing protected-branch policy protects nothing (additive). The contract is
the real process: exit 2 = block, exit 0 = pass/advisory.
"""
import json
import os
import subprocess
import sys
from pathlib import Path


from conftest import _git  # noqa: E402

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_GUARD = _HOOKS / "protected_ref_guard.py"


def _repo(tmp_path, branch="main"):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@local")
    _git(repo, "config", "user.name", "t")
    (repo / "seed.txt").write_text("x\n", encoding="utf-8")
    _git(repo, "add", "seed.txt")
    _git(repo, "commit", "-qm", "seed")
    # -B (not -b): the default init branch may already be `main`, so reset-or-
    # create is robust across git versions.
    _git(repo, "checkout", "-q", "-B", branch)
    return repo


def _protected(tmp_path, body='protected:\n  - main\n  - "release/*"\n'):
    p = tmp_path / "protected-branches.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _policy(tmp_path, *, preset="balanced", overrides=None):
    lines = ['schema_version: "1.0"', 'preset: "%s"' % preset]
    if overrides:
        lines.append("overrides:")
        for k, v in overrides.items():
            lines.append('  %s: "%s"' % (k, v))
    else:
        lines.append("overrides: {}")
    p = tmp_path / "guard-policy.yaml"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


def _run(repo, tmp_path, command, *, protected=True, policy=None):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_HOOK_CONFIG", None)
    for ci in ("CI", "GITHUB_ACTIONS", "GITLAB_CI"):
        env.pop(ci, None)
    env["HARNESS_USER"] = "alice"
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_GUARD_POLICY"] = str(policy or _policy(tmp_path))
    if protected:
        env["HARNESS_PROTECTED_BRANCHES"] = str(_protected(tmp_path))
    else:
        env["HARNESS_PROTECTED_BRANCHES"] = str(tmp_path / "absent.yaml")
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": command},
                          "session_id": "s1"})
    return subprocess.run([sys.executable, str(_GUARD)], input=payload,
                          capture_output=True, text=True, env=env, cwd=repo)


# ------------------------------------------------------------- force-push ---

def test_force_push_to_protected_blocks(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push --force origin main")
    assert out.returncode == 2, out.stderr
    assert "main" in out.stderr


def test_force_push_to_feature_passes(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push --force origin feature/x")
    assert out.returncode == 0, out.stderr


def test_wrapped_force_push_to_protected_blocks(tmp_path):
    # round-10 F2: a force-push hidden in an sh -c wrapper must still be analyzed
    # (the guard now inspects the UNWRAPPED inner command, not the outer string)
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "sh -c 'git push --force origin main'")
    assert out.returncode == 2, out.stderr
    assert "main" in out.stderr


def test_wrapped_non_force_push_to_feature_still_passes(tmp_path):
    # the unwrap must not over-block a wrapped push to a non-protected ref
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "sh -c 'git push origin feature/x'")
    assert out.returncode == 0, out.stderr


def test_plus_refspec_force_to_protected_blocks(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push origin +main")
    assert out.returncode == 2, out.stderr


def test_force_push_floor_holds_at_lenient(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push --force origin main",
               policy=_policy(tmp_path, preset="lenient"))
    assert out.returncode == 2, out.stderr  # FLOOR: cannot be lowered


def test_normal_push_to_protected_passes(tmp_path):
    # This hook refuses force-push + commit; a normal push to a protected ref
    # is the transport merge gate's job, not this one.
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push origin main")
    assert out.returncode == 0, out.stderr


# ------------------------------------------------- parser evasion matrix ---

def test_env_prefixed_force_push_to_protected_blocks(tmp_path):
    # GIT_SSH_COMMAND=... git push -f origin main must still be caught: the
    # command head is not literally `git`. stage_detector handles the prefix.
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "GIT_SSH_COMMAND=x git push --force origin main")
    assert out.returncode == 2, out.stderr


def test_sudo_prefixed_force_push_to_protected_blocks(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "sudo git push -f origin main")
    assert out.returncode == 2, out.stderr


def test_compound_command_force_push_to_protected_blocks(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "echo hi && git push --force origin main")
    assert out.returncode == 2, out.stderr


def test_repo_option_force_push_to_protected_blocks(tmp_path):
    # --repo supplies the remote, so `main` is the refspec, not the remote.
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push --force --repo=origin main")
    assert out.returncode == 2, out.stderr


def test_force_push_feature_while_on_protected_does_not_false_positive(tmp_path):
    # On main, but explicitly force-pushing a feature ref → must NOT block.
    repo = _repo(tmp_path, branch="main")
    out = _run(repo, tmp_path, "git push --force origin feature/x")
    assert out.returncode == 0, out.stderr


def test_delete_protected_ref_blocks(tmp_path):
    # `git push origin :main` deletes the protected branch — as destructive as
    # a force-push, refused through the same FLOOR guard.
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push origin :main")
    assert out.returncode == 2, out.stderr


def test_delete_flag_protected_ref_blocks(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push --delete origin main")
    assert out.returncode == 2, out.stderr


def test_delete_feature_ref_passes(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push origin :feature/x")
    assert out.returncode == 0, out.stderr


def test_bundled_short_force_flag_blocks(tmp_path):
    # git accepts bundled short flags: -fu = --force --set-upstream.
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push -fu origin main")
    assert out.returncode == 2, out.stderr


def test_bundled_short_delete_flag_blocks(tmp_path):
    # -df = --delete --force; deleting a protected ref must be refused.
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push -df origin main")
    assert out.returncode == 2, out.stderr


def test_bundled_short_flag_feature_passes(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push -fu origin feature/x")
    assert out.returncode == 0, out.stderr


def test_push_option_value_not_decoded_as_flag(tmp_path):
    # `-odeploy` is `-o deploy` (--push-option), NOT --delete; the `d`/`f` in an
    # attached -o value must not be decoded as a force/delete flag.
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git push -odeploy origin main")
    assert out.returncode == 0, out.stderr
    out2 = _run(repo, tmp_path, "git push -oforce origin main")
    assert out2.returncode == 0, out2.stderr


# ---------------------------------------------------------------- commit ---

def test_commit_on_protected_branch_blocks(tmp_path):
    repo = _repo(tmp_path, branch="main")
    out = _run(repo, tmp_path, "git commit -m change")
    assert out.returncode == 2, out.stderr
    assert "main" in out.stderr


def test_commit_on_feature_branch_passes(tmp_path):
    repo = _repo(tmp_path, branch="feature/x")
    out = _run(repo, tmp_path, "git commit -m change")
    assert out.returncode == 0, out.stderr


def test_commit_on_protected_warn_downgrades(tmp_path):
    repo = _repo(tmp_path, branch="main")
    out = _run(repo, tmp_path, "git commit -m change",
               policy=_policy(tmp_path, overrides={"protected_ref_commit": "warn"}))
    assert out.returncode == 0, out.stderr
    assert "main" in out.stderr  # advisory still names the branch


# ----------------------------------------------------------------- absent ---

def test_no_protected_policy_is_additive(tmp_path):
    repo = _repo(tmp_path, branch="main")
    out = _run(repo, tmp_path, "git push --force origin main", protected=False)
    assert out.returncode == 0, out.stderr
