"""test_gate_stage.py — the PreToolUse(Bash) compliance gate, end to end.

Subprocess tests pin the REAL contract: exit 2 + actionable stderr on block
(missing artifact, no plan, crash, missing dep — fail-closed); exit 0 +
{"continue": true} on pass/skip/soft/guess. Every decision leaves a trace
event with actor: gate_block | gate_pass | gate_skip (with reason) |
stage_guess; config load emits gate_config_loaded with the config hash
(tamper-visible).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_GATE = _HOOKS / "gate_stage.py"


def _mk_plan(root: Path, name: str = "260612-0800-feature-x") -> Path:
    d = root / "plans" / name
    d.mkdir(parents=True)
    (d / "plan.md").write_text(
        "---\ntitle: x\nstatus: in_progress\n---\n", encoding="utf-8")
    return d


def _verification(plan_dir: Path):
    a = plan_dir / "artifacts"
    a.mkdir(exist_ok=True)
    (a / "verification.json").write_text(json.dumps({
        "stage": "push", "plan": plan_dir.name, "actor": "user:alice",
        "ts": "2026-06-12T08:00:00+07:00",
        "checks": [{"name": "pytest", "status": "PASS"}], "verdict": "PASS",
    }), encoding="utf-8")


def _run(tmp_path, command, env_extra=None):
    env = dict(os.environ)
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_HOOK_CONFIG", None)
    env.pop("HARNESS_ACTIVE_PLAN", None)
    env.pop("HARNESS_STAGE_POLICY", None)
    for ci in ("CI", "GITLAB_CI", "GITHUB_ACTIONS"):
        env.pop(ci, None)
    env["HARNESS_ROOT"] = str(tmp_path)
    env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env["HARNESS_USER"] = "alice"
    for k, v in (env_extra or {}).items():
        env[k] = v
    payload = json.dumps({"tool_name": "Bash",
                          "tool_input": {"command": command}})
    return subprocess.run([sys.executable, str(_GATE)], input=payload,
                          capture_output=True, text=True, env=env)


def _trace_events(tmp_path):
    out = []
    trace = tmp_path / "state" / "trace"
    if trace.is_dir():
        for f in sorted(trace.glob("trace-*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                out.append(json.loads(line))
    return out


class TestBlocking:
    def test_push_without_plan_advisory_not_block(self, tmp_path):
        # Personal-first: a missing receipt is advisory, not a block. Exit 0 +
        # [advisory] stderr + a gate_advisory trace (enforcement moves to remote CI).
        proc = _run(tmp_path, "git push")
        assert proc.returncode == 0
        assert "[advisory]" in proc.stderr
        events = [e for e in _trace_events(tmp_path) if e["event"] == "gate_advisory"]
        assert events and events[0]["actor"] == "user:alice"

    def test_push_missing_artifact_advisory_naming_it(self, tmp_path):
        _mk_plan(tmp_path)
        proc = _run(tmp_path, "git push")
        assert proc.returncode == 0
        assert "[advisory]" in proc.stderr and "verification" in proc.stderr

    def test_gate_crash_fails_closed_exit_two(self, tmp_path):
        # A malformed policy file = internal gate failure → exit 2, not pass.
        bad = tmp_path / "bad-policy.yaml"
        bad.write_text("stages: [broken", encoding="utf-8")
        proc = _run(tmp_path, "git push",
                    env_extra={"HARNESS_STAGE_POLICY": str(bad)})
        assert proc.returncode == 2
        assert "BLOCKED" in proc.stderr

    def test_missing_pyyaml_fails_closed_with_install_command(self, tmp_path):
        # Simulate a machine that skipped preflight by hiding site-packages
        # (-S) so PyYAML cannot import inside the gate.
        env = dict(os.environ)
        env.pop("PYTEST_CURRENT_TEST", None)
        env["HARNESS_ROOT"] = str(tmp_path)
        env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
        env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
        payload = json.dumps({"tool_input": {"command": "git push"}})
        proc = subprocess.run([sys.executable, "-S", str(_GATE)],
                              input=payload, capture_output=True, text=True,
                              env=env)
        assert proc.returncode == 2
        assert "pip install" in proc.stderr or "preflight" in proc.stderr


class TestPassing:
    def test_push_with_complete_artifact_passes(self, tmp_path):
        d = _mk_plan(tmp_path)
        _verification(d)
        proc = _run(tmp_path, "git push")
        assert proc.returncode == 0, proc.stderr
        assert '"continue"' in proc.stdout
        assert any(e["event"] == "gate_pass" for e in _trace_events(tmp_path))

    def test_non_stage_command_passes_without_gate_events(self, tmp_path):
        proc = _run(tmp_path, "ls -la")
        assert proc.returncode == 0
        events = _trace_events(tmp_path)
        assert not any(e["event"].startswith("gate_") and
                       e["event"] != "gate_config_loaded" for e in events)

    def test_soft_stage_commit_warns_but_continues(self, tmp_path):
        # commit is hard:false — never blocks even with nothing in place.
        proc = _run(tmp_path, "git commit -m wip")
        assert proc.returncode == 0


class TestSoftStageAdvisoryToggle:
    """soft_stage_advisory in stage-policy controls the [advisory] soft-stage
    reminder — the 'notify vs quiet' knob a solo posture flips. The gate still
    proceeds + traces either way; only the stderr reminder is suppressed."""

    def _solo_policy(self, tmp_path, *, soft_advisory):
        p = tmp_path / "solo-policy.yaml"
        doc = {
            "stages": {
                "commit": {"hard": False, "requires": [], "require_plan": False},
                "push": {"hard": False, "requires": [], "require_plan": False},
            },
            "soft_stage_advisory": soft_advisory,
        }
        p.write_text(yaml.safe_dump(doc), encoding="utf-8")
        return p

    def test_notify_emits_soft_advisory(self, tmp_path):
        pol = self._solo_policy(tmp_path, soft_advisory=True)
        proc = _run(tmp_path, "git push",
                    env_extra={"HARNESS_STAGE_POLICY": str(pol)})
        assert proc.returncode == 0
        assert "soft stage" in proc.stderr  # the reminder fires

    def test_quiet_suppresses_soft_advisory_but_still_proceeds(self, tmp_path):
        pol = self._solo_policy(tmp_path, soft_advisory=False)
        proc = _run(tmp_path, "git push",
                    env_extra={"HARNESS_STAGE_POLICY": str(pol)})
        assert proc.returncode == 0          # no friction
        assert "soft stage" not in proc.stderr  # silence: no reminder
        # ...yet the decision is still traced (audit intact under quiet).
        assert any(e["event"] == "gate_config_loaded" for e in _trace_events(tmp_path))


class TestPrStagePlanApproval:
    """pr demands a valid plan-approval artifact; push behavior unchanged."""

    def _full_plan(self, tmp_path):
        d = _mk_plan(tmp_path)
        _verification(d)
        (d / "artifacts" / "review-decision.json").write_text(json.dumps({
            "verdict": "PASS", "reviewer": "user:bob", "role": "reviewer",
            "rationale": "ok"}), encoding="utf-8")
        (d / "artifacts" / "critique-consensus.json").write_text(json.dumps({
            "verdict": "PASS", "reviewer": "user:critique", "role": "critique",
            "rationale": "no blocker", "ts": "2026-06-12T08:00:00+07:00"}),
            encoding="utf-8")
        data = tmp_path / "harness" / "data"
        data.mkdir(parents=True, exist_ok=True)
        (data / "team.yaml").write_text(
            'reviewers: ["user:bob"]\nallow_self_review: false\n'
            "claims: {lease_s: 14400}\n", encoding="utf-8")
        return d

    def test_pr_without_plan_approval_advisory_not_block(self, tmp_path):
        # Personal-first: a missing plan-approval on pr is advisory locally; the
        # remote receipts-gate enforces it.
        self._full_plan(tmp_path)
        proc = _run(tmp_path, "gh pr create --fill")
        assert proc.returncode == 0
        assert "[advisory]" in proc.stderr and "plan-approval" in proc.stderr

    def test_pr_with_valid_approval_passes(self, tmp_path):
        d = self._full_plan(tmp_path)
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
        import plan_approval as pa
        (d / "artifacts" / "plan-approval.json").write_text(json.dumps({
            "schema": "plan-approval/v1", "plan": d.name,
            "plan_hash": pa.plan_hash(d), "file_hashes": pa.file_hashes(d),
            "author": "user:alice", "reviewer": "user:bob",
            "verdict": "APPROVED", "rationale": "reviewed",
            "ts": "2026-06-12T08:00:00+07:00"}), encoding="utf-8")
        proc = _run(tmp_path, "gh pr create --fill")
        assert proc.returncode == 0, proc.stderr

    def test_push_still_needs_only_verification(self, tmp_path):
        d = _mk_plan(tmp_path)
        _verification(d)
        proc = _run(tmp_path, "git push")
        assert proc.returncode == 0, proc.stderr


class TestSkipAndGuess:
    def test_disabled_via_config_skips_with_traced_reason(self, tmp_path):
        cfg = tmp_path / "harness-hooks.yaml"
        cfg.write_text(yaml.safe_dump(
            {"hooks": {"gate_stage": {"enabled": False}}}), encoding="utf-8")
        proc = _run(tmp_path, "git push",
                    env_extra={"HARNESS_HOOK_CONFIG": str(cfg)})
        assert proc.returncode == 0
        skips = [e for e in _trace_events(tmp_path) if e["event"] == "gate_skip"]
        assert skips, "gate_skip must be traced"
        assert skips[0]["actor"] == "user:alice"
        assert skips[0].get("note")  # reason is mandatory on skip
        # The note must point an auditor at the config file that actually
        # disabled the gate — here the env-overridden path, not the tracked
        # default (which still says enabled).
        assert str(cfg) in skips[0]["note"]

    def test_guess_word_traces_stage_guess_and_continues(self, tmp_path):
        proc = _run(tmp_path, "cat docs/release-notes.md")
        assert proc.returncode == 0
        guesses = [e for e in _trace_events(tmp_path)
                   if e["event"] == "stage_guess"]
        assert guesses and guesses[0]["target"] == "ship"

    def test_config_load_emits_hash_for_tamper_visibility(self, tmp_path):
        d = _mk_plan(tmp_path)
        _verification(d)
        _run(tmp_path, "git push")
        loaded = [e for e in _trace_events(tmp_path)
                  if e["event"] == "gate_config_loaded"]
        assert loaded and loaded[0].get("note")  # carries the policy hash


class TestPayloadShape:
    def test_malformed_stdin_passes_as_no_command(self, tmp_path):
        # Deliberate fail-open edge (run_compliance_hook docstring): broken
        # stdin yields {} → no command → pass. Blocking every Bash call on a
        # transport hiccup would DoS the session; an unparseable payload also
        # carries no command to gate. The gate fails closed on its OWN
        # errors, open on absent input.
        env = dict(os.environ)
        env.pop("PYTEST_CURRENT_TEST", None)
        env["HARNESS_ROOT"] = str(tmp_path)
        env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
        env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
        proc = subprocess.run([sys.executable, str(_GATE)], input="{not json",
                              capture_output=True, text=True, env=env)
        assert proc.returncode == 0
        assert '"continue"' in proc.stdout

    def test_tool_input_non_dict_passes_as_no_command(self, tmp_path):
        # An unexpected payload shape means "nothing to gate", never a
        # misleading "gate crashed" block.
        env = dict(os.environ)
        env.pop("PYTEST_CURRENT_TEST", None)
        env["HARNESS_ROOT"] = str(tmp_path)
        env["HARNESS_STATE_DIR"] = str(tmp_path / "state")
        env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
        payload = json.dumps({"tool_name": "Bash", "tool_input": "git push"})
        proc = subprocess.run([sys.executable, str(_GATE)], input=payload,
                              capture_output=True, text=True, env=env)
        assert proc.returncode == 0
        assert "crashed" not in proc.stderr

    def test_command_non_string_passes_as_no_command(self, tmp_path):
        proc = _run(tmp_path, ["git", "push"])  # list, not str
        assert proc.returncode == 0
