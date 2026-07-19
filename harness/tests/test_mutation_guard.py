"""test_mutation_guard.py — tests for mutation_guard.py (Phase 3).

mutation_guard is a DETECTION hook (nudge class, fail-open) that:
- At SubagentStart (--start): snapshots content-hashes of tracked files for
  advisory agents; skips builder/non-advisory agents.
- At SubagentStop (--stop): diffs the snapshot against current content; any
  changed non-allowlisted file triggers a trace + stderr advisory (never blocks).

Tests are self-contained and use temp directories throughout.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path


_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(_HOOKS))
sys.path.insert(0, str(_SCRIPTS))

_GUARD = _HOOKS / "mutation_guard.py"


def _run(mode, payload, state_dir, repo_root=None, extra_env=None):
    """Run mutation_guard.py --start or --stop via subprocess."""
    env = os.environ.copy()
    env["HARNESS_STATE_DIR"] = str(state_dir)
    env["HARNESS_USER"] = "tester"
    env.pop("CI", None)
    env.pop("GITLAB_CI", None)
    env.pop("GITHUB_ACTIONS", None)
    if repo_root:
        env["MUTATION_GUARD_REPO_ROOT"] = str(repo_root)
    if extra_env:
        env.update(extra_env)
    # Create a minimal config that enables mutation_guard for testing
    import tempfile
    cfg_dir = Path(tempfile.mkdtemp())
    cfg_file = cfg_dir / "harness-hooks.yaml"
    cfg_file.write_text("hooks:\n  mutation_guard:\n    enabled: true\n")
    env["HARNESS_HOOK_CONFIG"] = str(cfg_file)
    cmd = [sys.executable, str(_GUARD), mode]
    result = subprocess.run(
        cmd,
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        env=env,
    )
    return result.returncode, result.stdout, result.stderr


def _make_repo(tmp_path):
    """Create a minimal git repo structure with some tracked files."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "harness").mkdir()
    (repo / "harness" / "hooks").mkdir(parents=True)
    f1 = repo / "harness" / "hooks" / "gate_stage.py"
    f2 = repo / "harness" / "hooks" / "trace_log.py"
    f1.write_text("# gate_stage")
    f2.write_text("# trace_log")
    # Create a simple git repo so git ls-files works
    subprocess.run(["git", "init", str(repo)], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.com"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "T"], capture_output=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True)
    return repo


def _advisory_payload(session_id="sess1", agent_id="ag1", agent_type="red-teamer"):
    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_type": agent_type,
    }


def _builder_payload(session_id="sess2", agent_id="ag2", agent_type="developer"):
    return {
        "session_id": session_id,
        "agent_id": agent_id,
        "agent_type": agent_type,
    }


class TestStartSnapshot:
    def test_start_snapshots_only_advisory_agents(self, tmp_path):
        repo = _make_repo(tmp_path)
        state_dir = tmp_path / "state"

        # Advisory agent → snapshot created
        rc, out, err = _run("--start", _advisory_payload(), state_dir, repo_root=repo)
        assert rc == 0, "must exit 0 (nudge fail-open)\nerr: %s" % err
        snap_dir = state_dir / "mutation-guard"
        assert snap_dir.exists()
        snaps = list(snap_dir.glob("*.json"))
        assert len(snaps) == 1, "one snapshot for advisory agent"

        # Builder agent → no snapshot
        rc2, out2, err2 = _run("--start", _builder_payload(), state_dir, repo_root=repo)
        assert rc2 == 0
        snaps2 = list(snap_dir.glob("*.json"))
        assert len(snaps2) == 1, "builder agent must not create a snapshot"

    def test_fail_open_on_error(self, tmp_path):
        """Bad payload / unwritable state dir → must exit 0 (nudge fail-open)."""
        state_dir = tmp_path / "state"
        # Block state creation: create file where dir would go
        blocker = tmp_path / "state"
        blocker.write_text("blocker")  # file, not dir

        rc, out, err = _run("--start", _advisory_payload(), state_dir, repo_root=tmp_path)
        assert rc == 0, "must exit 0 even on IO error"


class TestStopDetect:
    def test_stop_detects_modified_tracked_file(self, tmp_path):
        repo = _make_repo(tmp_path)
        state_dir = tmp_path / "state"

        payload = _advisory_payload()
        rc, _, _ = _run("--start", payload, state_dir, repo_root=repo)
        assert rc == 0

        # Modify a tracked file
        (repo / "harness" / "hooks" / "gate_stage.py").write_text("# MODIFIED")

        rc2, out2, err2 = _run("--stop", payload, state_dir, repo_root=repo)
        assert rc2 == 0, "advisory: must exit 0 even on detection"
        # HIGH priority (H2-resolved): the advisory is a systemMessage on stdout,
        # not stderr-on-exit-0 (spec-invisible, INV-3 F-2).
        out_json = json.loads(out2)
        msg = out_json.get("systemMessage", "")
        assert out_json.get("continue") is True
        assert "mutation" in msg.lower() or "modified" in msg.lower() \
               or "gate_stage" in msg, \
            "systemMessage must report the detected mutation\nerr: %s\nout: %s" % (err2, out2)

    def test_stop_ignores_allowlisted_path(self, tmp_path):
        repo = _make_repo(tmp_path)
        state_dir = tmp_path / "state"

        # Add a __pycache__ dir (in allowlist)
        pycache = repo / "harness" / "hooks" / "__pycache__"
        pycache.mkdir()
        (pycache / "gate_stage.cpython-312.pyc").write_text("bytecode")

        payload = _advisory_payload()
        _run("--start", payload, state_dir, repo_root=repo)

        # Modify allowlisted file
        (pycache / "gate_stage.cpython-312.pyc").write_text("changed-bytecode")

        rc, out, err = _run("--stop", payload, state_dir, repo_root=repo)
        assert rc == 0
        # Should NOT report mutation for the pycache file
        assert "__pycache__" not in err or "mutation_detected" not in err, \
            "allowlisted __pycache__ must not be flagged\nerr: %s" % err

    def test_stop_ignores_unchanged_touch(self, tmp_path):
        repo = _make_repo(tmp_path)
        state_dir = tmp_path / "state"

        payload = _advisory_payload()
        _run("--start", payload, state_dir, repo_root=repo)

        # Touch mtime without changing content
        target = repo / "harness" / "hooks" / "gate_stage.py"
        time.sleep(0.01)
        target.touch()

        rc, out, err = _run("--stop", payload, state_dir, repo_root=repo)
        assert rc == 0
        # No mutation detected (content unchanged)
        assert "mutation_detected" not in err and "modified" not in err.lower(), \
            "mtime-only touch must not flag mutation\nerr: %s" % err

    def test_detects_mtime_restored_edit(self, tmp_path):
        """Content changed but mtime restored — must still flag (content-hash based)."""
        repo = _make_repo(tmp_path)
        state_dir = tmp_path / "state"

        payload = _advisory_payload()
        _run("--start", payload, state_dir, repo_root=repo)

        target = repo / "harness" / "hooks" / "gate_stage.py"
        original_stat = (target.stat().st_atime, target.stat().st_mtime)
        target.write_text("# gate_stage MODIFIED CONTENT")
        os.utime(str(target), original_stat)  # restore mtime

        rc, out, err = _run("--stop", payload, state_dir, repo_root=repo)
        assert rc == 0, "advisory must exit 0"
        msg = json.loads(out).get("systemMessage", "")
        assert "modified" in msg.lower() or "gate_stage" in msg, \
            "content hash change with mtime-restore must still flag\nmsg: %r out: %s" % (msg, out)

    def test_no_snapshot_no_flag(self, tmp_path):
        """Stop with no snapshot (builder agent never started) → no-op."""
        repo = _make_repo(tmp_path)
        state_dir = tmp_path / "state"

        payload = _builder_payload()
        # Do NOT call --start for builder
        rc, out, err = _run("--stop", payload, state_dir, repo_root=repo)
        assert rc == 0
        assert "mutation_detected" not in err

    def test_pairs_by_key(self, tmp_path):
        """Start key A creates snapshot; stop key B finds no snapshot → no-op."""
        repo = _make_repo(tmp_path)
        state_dir = tmp_path / "state"

        payload_a = _advisory_payload(session_id="sessA", agent_id="agA")
        payload_b = _advisory_payload(session_id="sessA", agent_id="agB")

        _run("--start", payload_a, state_dir, repo_root=repo)
        (repo / "harness" / "hooks" / "gate_stage.py").write_text("# MODIFIED")

        # Stop with mismatched key → should not flag
        rc, out, err = _run("--stop", payload_b, state_dir, repo_root=repo)
        assert rc == 0
        assert "mutation_detected" not in err, \
            "key mismatch must not flag\nerr: %s" % err

    def test_fail_open_on_error(self, tmp_path):
        """Stop with garbage payload → must exit 0."""
        state_dir = tmp_path / "state"
        rc, out, err = _run("--stop", {}, state_dir, repo_root=tmp_path)
        assert rc == 0


class TestReSnapshotEvasion:
    def test_second_start_same_key_does_not_overwrite(self, tmp_path):
        """A second --start with the same key must not overwrite the original
        snapshot (write-once per session), so an agent cannot re-snapshot after
        mutating to erase the diff."""
        repo = _make_repo(tmp_path)
        state_dir = tmp_path / "state"
        payload = _advisory_payload()

        # First --start: snapshot written
        rc, _, _ = _run("--start", payload, state_dir, repo_root=repo)
        assert rc == 0

        snap_dir = state_dir / "mutation-guard"
        snaps = list(snap_dir.glob("*.json"))
        assert len(snaps) == 1
        # Record the original snapshot content
        snap_file = snaps[0]
        original_data = snap_file.read_text(encoding="utf-8")

        # Mutate a tracked file
        (repo / "harness" / "hooks" / "gate_stage.py").write_text("# AFTER MUTATION")

        # Second --start with same key must NOT overwrite (re-snapshot would hide the change)
        rc2, _, _ = _run("--start", payload, state_dir, repo_root=repo)
        assert rc2 == 0

        # Snapshot must still contain original hashes (not updated)
        current_data = snap_file.read_text(encoding="utf-8")
        assert current_data == original_data, (
            "second --start must not overwrite snapshot: re-snapshot erases mutation evidence"
        )

        # --stop should still detect the mutation (chain is preserved)
        rc3, out3, err3 = _run("--stop", payload, state_dir, repo_root=repo)
        assert rc3 == 0
        msg3 = json.loads(out3).get("systemMessage", "")
        assert "gate_stage" in msg3 or "modified" in msg3.lower(), \
            "mutation must still be detected after refused re-snapshot\nmsg: %r out: %s" % (msg3, out3)
