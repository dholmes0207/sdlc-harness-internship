"""test_artifact_guard.py — gate_stage PRE-blocks a shell-spelled write to a
plan artifact (compliance, fail-closed).

The four gate artifacts (verification / review-decision / critique-consensus /
plan-approval .json under plans/*/artifacts/) are produced ONLY by their skill
controller via the Write tool. No legit flow shell-writes them, so a redirect /
tee / dd / cp / mv into one is forgery — an agent fabricating a PASS to clear
the stage gate. gate_stage blocks it PRE (exit 2). This closes the documented
`cat > verification.json` shortcut the PostToolUse advisory could only surface.

Two layers tested: the shared shell-write-target parser (copy/move INCLUDED for
artifacts, still EXCLUDED for config) and the gate_stage subprocess contract.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_GATE = _HOOKS / "gate_stage.py"
sys.path.insert(0, str(_HOOKS))

import bash_write_guard as bwg  # noqa: E402


# --- shared parser: artifact targets are found, including cp/mv ---------------

def _targets(cmd, copy_move=True):
    return set(bwg.shell_write_targets(cmd, include_copy_move=copy_move))


def test_redirect_into_artifact_is_a_target():
    assert "plans/p/artifacts/verification.json" in _targets(
        "echo '{}' > plans/p/artifacts/verification.json")


def test_heredoc_redirect_into_artifact_is_a_target():
    assert "plans/x/artifacts/review-decision.json" in _targets(
        "cat > plans/x/artifacts/review-decision.json <<EOF\n{}\nEOF")


def test_tee_into_artifact_is_a_target():
    assert "plans/p/artifacts/critique-consensus.json" in _targets(
        "echo '{}' | tee plans/p/artifacts/critique-consensus.json")


def test_cp_into_artifact_is_a_target_when_copy_move_on():
    assert "plans/p/artifacts/verification.json" in _targets(
        "cp /tmp/forged.json plans/p/artifacts/verification.json")


def test_mv_into_artifact_is_a_target_when_copy_move_on():
    assert "plans/p/artifacts/plan-approval.json" in _targets(
        "mv /tmp/x.json plans/p/artifacts/plan-approval.json")


def test_cp_NOT_a_target_when_copy_move_off():
    # config observer keeps its blessed cp/mv workaround: flag must be honored
    assert _targets("cp /tmp/x.json plans/p/artifacts/verification.json",
                    copy_move=False) == set()


def test_reading_artifact_and_redirecting_elsewhere_not_a_target():
    # artifact is the SOURCE, /tmp is the write — never a write to the artifact
    t = _targets("cat plans/p/artifacts/verification.json > /tmp/out")
    assert "plans/p/artifacts/verification.json" not in t


def test_bypass_targets_config_unchanged_excludes_cp_mv():
    # the refactor must NOT change bash_write_guard's config contract
    rels = {r for r, _ in bwg.bypass_targets(
        "cp /tmp/stage-policy.yaml harness/data/stage-policy.yaml")}
    assert rels == set()
    rels2 = {r for r, _ in bwg.bypass_targets(
        "echo x > harness/data/stage-policy.yaml")}
    assert "harness/data/stage-policy.yaml" in rels2


# --- gate_stage subprocess: a shell write to an artifact blocks (exit 2) ------

def _run(tmp_path, command):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_HOOK_CONFIG", None)
    env["HARNESS_ROOT"] = str(tmp_path)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_USER"] = "alice"
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(_GATE)], input=payload,
                          capture_output=True, text=True, env=env)


def test_gate_blocks_redirect_forgery_exit_two(tmp_path):
    (tmp_path / "plans" / "p" / "artifacts").mkdir(parents=True)
    proc = _run(tmp_path,
                "echo '{\"verdict\":\"PASS\"}' > plans/p/artifacts/verification.json")
    assert proc.returncode == 2
    assert "artifact" in proc.stderr.lower()
    assert "Write tool" in proc.stderr or "write tool" in proc.stderr.lower()


def test_gate_blocks_cp_forgery_exit_two(tmp_path):
    (tmp_path / "plans" / "p" / "artifacts").mkdir(parents=True)
    proc = _run(tmp_path,
                "cp /tmp/forged.json plans/p/artifacts/review-decision.json")
    assert proc.returncode == 2


def test_gate_blocks_cp_yaml_forgery_exit_two(tmp_path):
    # SSOT-YAML: a .yaml gate artifact is forgeable the same way as .json. The cp
    # vector goes through the fnmatch glob, which now includes *.yaml — without it
    # `cp forged.yaml ...verification.yaml` would slip past (RT-1/F1 blocker).
    (tmp_path / "plans" / "p" / "artifacts").mkdir(parents=True)
    proc = _run(tmp_path,
                "cp /tmp/forged.yaml plans/p/artifacts/review-decision.yaml")
    assert proc.returncode == 2
    assert "artifact" in proc.stderr.lower()


def test_gate_blocks_redirect_yaml_forgery_exit_two(tmp_path):
    (tmp_path / "plans" / "p" / "artifacts").mkdir(parents=True)
    proc = _run(tmp_path,
                "echo 'verdict: PASS' > plans/p/artifacts/verification.yaml")
    assert proc.returncode == 2


def test_gate_allows_reading_artifact(tmp_path):
    (tmp_path / "plans" / "p" / "artifacts").mkdir(parents=True)
    proc = _run(tmp_path, "cat plans/p/artifacts/verification.json")
    assert proc.returncode == 0


def test_gate_allows_plan_approval_cli(tmp_path):
    # the legit writer (plan_approval.py) writes inside the script, not via an
    # inline open() in the command string — the detector must not flag it
    proc = _run(tmp_path,
                "python3 harness/scripts/plan_approval.py --approve --plan p")
    assert proc.returncode == 0


def test_gate_allows_benign_command(tmp_path):
    proc = _run(tmp_path, "ls -la plans/")
    assert proc.returncode == 0
