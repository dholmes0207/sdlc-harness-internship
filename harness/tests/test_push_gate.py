"""test_push_gate.py — transport-level routing for `git push`.

push_gate reads git's pre-push stdin (`<localref> <localsha> <remoteref>
<remotesha>` per line), extracts the destination branch names, and picks the
stage: pushing to a protected branch is a `merge` (merge-grade artifacts), any
other push is `push`. Empty stdin → `push` (back-compat with the legacy hook).
check() then routes the merge reason through the merge_gate FLOOR guard.
"""
import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import branch_policy  # noqa: E402
import push_gate  # noqa: E402


_PROTECTED = 'protected:\n  - main\n  - "release/*"\n'


@pytest.fixture
def protected(tmp_path, monkeypatch):
    p = tmp_path / "protected-branches.yaml"
    p.write_text(_PROTECTED, encoding="utf-8")
    monkeypatch.setenv("HARNESS_PROTECTED_BRANCHES", str(p))
    return p


def _line(remote_ref):
    return "refs/heads/local abc123 %s def456\n" % remote_ref


# ------------------------------------------------------------------ targets ---

def test_targets_extracts_remote_branch_names(protected):
    refs = _line("refs/heads/main") + _line("refs/heads/feature/x")
    assert push_gate.targets(refs) == {"main", "feature/x"}


def test_targets_ignores_tags(protected):
    refs = _line("refs/tags/v1.0")
    assert push_gate.targets(refs) == set()


def test_targets_empty_stdin_is_empty(protected):
    assert push_gate.targets("") == set()


# ----------------------------------------------------------------- stage_for ---

def test_stage_for_protected_is_merge(protected):
    assert push_gate.stage_for(_line("refs/heads/main")) == "merge"


def test_stage_for_release_family_is_merge(protected):
    assert push_gate.stage_for(_line("refs/heads/release/2.0")) == "merge"


def test_stage_for_feature_is_push(protected):
    assert push_gate.stage_for(_line("refs/heads/feature/x")) == "push"


def test_stage_for_empty_stdin_is_push(protected):
    # back-compat: the legacy hook always judged 'push'.
    assert push_gate.stage_for("") == "push"


# --------------------------------------------------------------------- check ---

def test_check_routes_stage_to_check_stage(protected, monkeypatch, tmp_path):
    seen = {}

    def fake_check_stage(stage, root):
        seen["stage"] = stage
        return None

    import artifact_check
    monkeypatch.setattr(artifact_check, "check_stage", fake_check_stage)
    assert push_gate.check(_line("refs/heads/feature/x"), str(tmp_path)) is None
    assert seen["stage"] == "push"


_ZERO = "0" * 40


def _refline(local_sha, remote_ref, remote_sha):
    return "refs/heads/local %s %s %s\n" % (local_sha, remote_ref, remote_sha)


def test_destructive_delete_protected_returns_reason(protected, tmp_path):
    # delete (zero LOCAL sha) of a protected branch — no git needed.
    refs = _refline(_ZERO, "refs/heads/main", "a" * 40)
    reason = push_gate.destructive_to_protected(refs, str(tmp_path))
    assert reason is not None and "main" in reason


def test_destructive_delete_feature_is_none(protected, tmp_path):
    refs = _refline(_ZERO, "refs/heads/feature/x", "a" * 40)
    assert push_gate.destructive_to_protected(refs, str(tmp_path)) is None


def test_destructive_new_protected_branch_is_none(protected, tmp_path):
    # creating a protected branch (zero REMOTE sha) is not a delete/force.
    refs = _refline("a" * 40, "refs/heads/main", _ZERO)
    assert push_gate.destructive_to_protected(refs, str(tmp_path)) is None


def _git(repo, *args):
    import subprocess
    return subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=True)


def _diverged_repo(tmp_path):
    """A repo where `remote_sha` is NOT an ancestor of `local_sha` (a force/
    non-ff), plus a fast-forward pair. Returns (repo, base, local, remote)."""
    repo = tmp_path / "r"
    repo.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        _git(repo, *a)
    (repo / "f").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "f"); _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "f").write_text("remote\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "remote-side")
    remote = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "reset", "-q", "--hard", base)
    (repo / "f").write_text("local\n", encoding="utf-8")
    _git(repo, "commit", "-qam", "local-side")
    local = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, base, local, remote


def test_destructive_non_ff_protected_returns_reason(protected, tmp_path):
    repo, base, local, remote = _diverged_repo(tmp_path)
    refs = _refline(local, "refs/heads/main", remote)  # remote not ancestor
    reason = push_gate.destructive_to_protected(refs, str(repo))
    assert reason is not None and "main" in reason


def test_destructive_fast_forward_protected_is_none(protected, tmp_path):
    repo, base, local, remote = _diverged_repo(tmp_path)
    # base IS an ancestor of local → fast-forward → not destructive.
    refs = _refline(local, "refs/heads/main", base)
    assert push_gate.destructive_to_protected(refs, str(repo)) is None


# --- indeterminate ancestry (shallow clone: remote object absent locally) -----

_BOGUS = "b" * 40  # a well-formed sha that is NOT an object in the local store


def test_indeterminate_without_remote_defers(protected, tmp_path):
    # No remote to fetch from → ancestry cannot be resolved → DEFER (return
    # None) so the merge-grade artifact floor still judges the push. This keeps
    # the gate from false-blocking when called without transport context.
    repo, base, local, remote = _diverged_repo(tmp_path)
    refs = _refline(local, "refs/heads/main", _BOGUS)  # remote obj absent
    assert push_gate.destructive_to_protected(refs, str(repo)) is None


def test_indeterminate_with_unreachable_remote_blocks(protected, tmp_path):
    # remote object absent AND the fetch cannot recover it (no such remote) →
    # ancestry stays unknown → BLOCK rather than fail open.
    repo, base, local, remote = _diverged_repo(tmp_path)
    refs = _refline(local, "refs/heads/main", _BOGUS)
    reason = push_gate.destructive_to_protected(refs, str(repo), remote="origin")
    assert reason is not None and "main" in reason


def _origin_with_diverged_history(tmp_path):
    """A bare `origin` holding a `main` commit (R) whose object is ABSENT from a
    separate local repo whose own `main` (L) shares no history with R. Returns
    (local_repo, remote_url, R_sha, L_sha) — pushing L over R is a non-ff that
    the local repo cannot judge until it fetches R."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "origin.git")
    up = tmp_path / "up"
    up.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "t@t"],
              ["config", "user.name", "t"]):
        _git(up, *a)
    (up / "f").write_text("upstream\n", encoding="utf-8")
    _git(up, "add", "f"); _git(up, "commit", "-qm", "upstream")
    _git(up, "branch", "-M", "main")
    _git(up, "remote", "add", "origin", str(origin))
    _git(up, "push", "-q", "origin", "main")
    r_sha = _git(up, "rev-parse", "HEAD").stdout.strip()

    dev = tmp_path / "dev"
    dev.mkdir()
    for a in (["init", "-q"], ["config", "user.email", "d@d"],
              ["config", "user.name", "d"]):
        _git(dev, *a)
    (dev / "g").write_text("local\n", encoding="utf-8")
    _git(dev, "add", "g"); _git(dev, "commit", "-qm", "local")
    _git(dev, "branch", "-M", "main")
    _git(dev, "remote", "add", "origin", str(origin))
    l_sha = _git(dev, "rev-parse", "HEAD").stdout.strip()
    return dev, str(origin), r_sha, l_sha


def test_indeterminate_fetch_recovers_then_blocks_non_ff(protected, tmp_path):
    # The remote object is absent locally (ancestry == indeterminate), but a
    # fetch from the real remote pulls it in; re-judged, it is a non-ff → BLOCK.
    dev, origin, r_sha, l_sha = _origin_with_diverged_history(tmp_path)
    # sanity: R is genuinely absent from dev before the gate runs
    assert push_gate._is_ancestor(str(dev), r_sha, l_sha) is None
    refs = _refline(l_sha, "refs/heads/main", r_sha)
    reason = push_gate.destructive_to_protected(refs, str(dev), remote=origin)
    assert reason is not None and "main" in reason
    # and the fetch actually made ancestry computable afterwards
    assert push_gate._is_ancestor(str(dev), r_sha, l_sha) == 1


def test_check_threads_remote_to_destructive(protected, monkeypatch, tmp_path):
    # check() must forward the remote so the fetch-recovery path is reachable
    # from the transport hook.
    seen = {}

    def fake_destructive(refs_text, root, remote=None):
        seen["remote"] = remote
        return None

    monkeypatch.setattr(push_gate, "destructive_to_protected", fake_destructive)
    import artifact_check
    monkeypatch.setattr(artifact_check, "check_stage", lambda s, r: None)
    push_gate.check(_line("refs/heads/feature/x"), str(tmp_path), remote="origin")
    assert seen["remote"] == "origin"


def test_check_refuses_delete_of_protected(protected, monkeypatch, tmp_path):
    # check() refuses a protected delete outright, before artifact routing.
    import artifact_check
    monkeypatch.setattr(artifact_check, "check_stage", lambda s, r: None)
    refs = _refline(_ZERO, "refs/heads/main", "a" * 40)
    assert push_gate.check(refs, str(tmp_path)) is not None


def test_check_missing_merge_artifact_warns_not_blocks(protected, monkeypatch, tmp_path, capsys):
    # Personal-first transport: a missing receipt on a (non-destructive) protected
    # push is a WARN, not a block — the remote receipts-gate enforces it. The two
    # Tier-A floors (destructive + secret) still block (tested separately).
    import artifact_check
    monkeypatch.setattr(artifact_check, "check_stage",
                        lambda stage, root: "missing artifact: review-decision"
                        if stage == "merge" else None)
    reason = push_gate.check(_line("refs/heads/main"), str(tmp_path))
    assert reason is None  # not blocked
    assert "[pre-push warn]" in capsys.readouterr().err


# --- F4: transport-level secret backstop (push_gate._secret_reason) -----------
# A push that defeated the in-session stage-detector still reaches pre-push, so
# push_gate re-scans exactly the about-to-push commits (each PUSH_REFS line's
# <remote_sha>..<local_sha> range). The unit corners mock _pushed_diff; one real-
# git test proves the range derivation catches a secret on the pushed ref.

_HOOKS_DIR = Path(__file__).resolve().parents[2] / "harness" / "hooks"
if str(_HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(_HOOKS_DIR))

# a new-branch push line: <local_ref> <local_sha> <remote_ref> <zero remote_sha>
_REFS = "refs/heads/local %s refs/heads/x %s" % ("1" * 40, "0" * 40)


def test_secret_backstop_blocks_secret(monkeypatch):
    import push_gate
    leak = "sk_" + "live_" + "0" * 30
    monkeypatch.setattr(push_gate, "_pushed_diff",
                        lambda root, refs: "+++ b/app/cfg.py\n+token = '%s'\n" % leak)
    reason = push_gate._secret_reason(".", _REFS)
    assert reason and "secret" in reason.lower()


def test_secret_backstop_clean_passes(monkeypatch):
    import push_gate
    monkeypatch.setattr(push_gate, "_pushed_diff",
                        lambda root, refs: "+++ b/app/cfg.py\n+x = 1 + 2\n")
    assert push_gate._secret_reason(".", _REFS) is None


def test_secret_backstop_fail_open_on_unreadable(monkeypatch):
    # additive backstop: an unreadable range must NOT block (the in-session gate is
    # the primary fail-closed line; bricking pushes on a git error gets the hook
    # disabled, losing all coverage).
    import push_gate
    monkeypatch.setattr(push_gate, "_pushed_diff", lambda root, refs: None)
    assert push_gate._secret_reason(".", _REFS) is None


def test_secret_backstop_excluded_path_passes(monkeypatch):
    # a fake secret in an excluded path (test_*) must not block the push
    import push_gate
    leak = "sk_" + "live_" + "0" * 30
    monkeypatch.setattr(push_gate, "_pushed_diff",
                        lambda root, refs: "--- a/harness/tests/test_x.py\n"
                                           "+++ b/harness/tests/test_x.py\n+t = '%s'\n" % leak)
    assert push_gate._secret_reason(".", _REFS) is None


def test_check_blocks_on_secret_before_artifact(monkeypatch, tmp_path):
    # the secret reason short-circuits check() BEFORE artifact routing — pins the
    # call site, which the direct _secret_reason tests do not exercise.
    import push_gate
    import artifact_check
    monkeypatch.setattr(push_gate, "_secret_reason", lambda root, refs: "push blocked: secret")
    called = {"n": 0}
    monkeypatch.setattr(artifact_check, "check_stage",
                        lambda s, r: called.__setitem__("n", called["n"] + 1) or None)
    reason = push_gate.check(_line("refs/heads/feature/x"), str(tmp_path))
    assert reason == "push blocked: secret"
    assert called["n"] == 0  # never reached artifact routing


def test_pushed_diff_scans_explicit_range(tmp_path):
    # red-team F4-1/F4-2 regression: the scan is driven by the PUSH_REFS
    # <remote_sha>..<local_sha> range, so a secret in a pushed commit is caught
    # (and a no-op range that delivers nothing does not block). Real git repo.
    import subprocess
    import push_gate
    repo = tmp_path / "r"
    repo.mkdir()

    def git(*a):
        return subprocess.run(["git", "-C", str(repo), *a], capture_output=True, text=True)

    git("init", "-q")
    git("config", "user.email", "t@t")
    git("config", "user.name", "t")
    (repo / "a.txt").write_text("base\n")
    git("add", "a.txt")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD").stdout.strip()
    leak = "sk_" + "live_" + "0" * 30
    (repo / "cfg.py").write_text("KEY = '%s'\n" % leak)
    git("add", "cfg.py")
    git("commit", "-qm", "add config")
    head = git("rev-parse", "HEAD").stdout.strip()

    pushing = "refs/heads/main %s refs/heads/main %s" % (head, base)  # remote at base
    reason = push_gate._secret_reason(str(repo), pushing)
    assert reason and "secret" in reason.lower(), "range scan must catch the pushed secret"

    noop = "refs/heads/main %s refs/heads/main %s" % (base, base)  # nothing new
    assert push_gate._secret_reason(str(repo), noop) is None
