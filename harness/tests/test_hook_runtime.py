"""test_hook_runtime.py — shared runtime for harness hooks (crash audit +
3-class config gate + telemetry/nudge/compliance wrappers + resolve_actor).

Ported near-verbatim from product-spec test_hook_runtime.py (crash audit,
telemetry wrapper, malformed-config) and extended for the harness deltas:
3 hook classes with per-class defaults (compliance default ON+blocking),
YAML config, fail-closed compliance wrapper, actor resolution.

Hermetic: HARNESS_HOOK_LOG_DIR redirects the crash log, HARNESS_HOOK_CONFIG
redirects the per-hook config, HARNESS_STATE_DIR redirects session state.
PYTEST_CURRENT_TEST is cleared where a write must actually be asserted.
"""
import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

import yaml

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(_HOOKS))


def _write_guard_policy(path: Path, preset: str, overrides=None) -> None:
    """Render a guard-policy.yaml with quoted scalars (so the mode `off` is a
    string, not a YAML 1.1 boolean) — the test twin of guard_config._render."""
    lines = ['schema_version: "1.0"', 'preset: "%s"' % preset]
    if overrides:
        lines.append("overrides:")
        for gid in sorted(overrides):
            lines.append('  %s: "%s"' % (gid, overrides[gid]))
    else:
        lines.append("overrides: {}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _fresh(monkeypatch, tmp_path, *, config=None, extra_env=None,
           preset="balanced", overrides=None):
    """Reload hook_runtime with tmp log dir + (optional) tmp YAML config,
    PYTEST_CURRENT_TEST cleared so log_hook_error actually writes.

    A hermetic guard-policy.yaml is pinned via HARNESS_GUARD_POLICY (balanced
    by default) so the policy bridge in hook_enabled/hook_mode reads a known
    posture instead of the repo's shipped file. Pass preset=None to leave the
    env untouched."""
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    monkeypatch.delenv("HARNESS_HOOK_AUDIT_DISABLED", raising=False)
    if preset is not None:
        gp = tmp_path / "guard-policy.yaml"
        _write_guard_policy(gp, preset, overrides)
        monkeypatch.setenv("HARNESS_GUARD_POLICY", str(gp))
    else:
        monkeypatch.delenv("HARNESS_GUARD_POLICY", raising=False)
    if config is not None:
        cfg_path = tmp_path / "harness-hooks.yaml"
        cfg_path.write_text(yaml.safe_dump(config), encoding="utf-8")
        monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg_path))
    else:
        monkeypatch.delenv("HARNESS_HOOK_CONFIG", raising=False)
    for k, v in (extra_env or {}).items():
        monkeypatch.setenv(k, v)
    sys.modules.pop("hook_runtime", None)
    import hook_runtime
    importlib.reload(hook_runtime)
    hook_runtime._reset_config_cache()
    return hook_runtime


def _crash_lines(tmp_path):
    p = tmp_path / "logs" / "hook-crashes.log"
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


# ---------------------------------------------------------------------------
# crash audit (ported PS)
# ---------------------------------------------------------------------------

class TestCrashAudit:
    def test_forced_exception_logs_exactly_one_line(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path)
        try:
            raise ValueError("boom-marker")
        except ValueError as e:
            hr.log_hook_error("some_hook", e)
        lines = _crash_lines(tmp_path)
        assert len(lines) == 1
        rec = lines[0]
        assert rec["hook"] == "some_hook"
        assert rec["type"] == "ValueError"
        assert "boom-marker" in rec["msg"]

    def test_unwritable_logdir_never_raises(self, tmp_path, monkeypatch):
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(blocker / "logs"))
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        sys.modules.pop("hook_runtime", None)
        import hook_runtime
        importlib.reload(hook_runtime)
        hook_runtime.log_hook_error("h", RuntimeError("z"))  # must not raise

    def test_audit_disabled_writes_nothing(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    extra_env={"HARNESS_HOOK_AUDIT_DISABLED": "1"})
        hr.log_hook_error("h", RuntimeError("nope"))
        assert _crash_lines(tmp_path) == []

    def test_over_cap_rotates_to_dot_one(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path)
        logdir = tmp_path / "logs"
        logdir.mkdir(parents=True, exist_ok=True)
        (logdir / "hook-crashes.log").write_text("x" * (300 * 1024))
        hr.log_hook_error("h", RuntimeError("after-rotate"))
        assert (logdir / "hook-crashes.log.1").exists()
        lines = _crash_lines(tmp_path)
        assert len(lines) == 1 and "after-rotate" in lines[0]["msg"]


# ---------------------------------------------------------------------------
# 3-class config gate — HOOK_CLASS lives in hook code, config only overrides
# enabled/mode
# ---------------------------------------------------------------------------

class TestHookEnabled:
    def test_telemetry_missing_key_defaults_enabled(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}})
        assert hr.hook_enabled("track_something", "telemetry") is True

    def test_nudge_missing_key_defaults_disabled(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}})
        assert hr.hook_enabled("some_nudge", "nudge") is False

    def test_compliance_missing_key_defaults_ENABLED(self, tmp_path, monkeypatch):
        # The inversion vs the default: a compliance gate ships ON.
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}})
        assert hr.hook_enabled("gate_stage", "compliance") is True

    def test_explicit_false_disables_compliance(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"gate_stage": {"enabled": False}}})
        assert hr.hook_enabled("gate_stage", "compliance") is False

    def test_explicit_true_enables_nudge(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"some_nudge": {"enabled": True}}})
        assert hr.hook_enabled("some_nudge", "nudge") is True

    def test_kill_switch_offs_telemetry_not_compliance(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}},
                    extra_env={"HARNESS_TELEMETRY_DISABLED": "1"})
        assert hr.hook_enabled("track_something", "telemetry") is False
        assert hr.hook_enabled("gate_stage", "compliance") is True

    def test_config_cannot_change_class(self, tmp_path, monkeypatch):
        # A config "class" key is ignored: class is a code constant.
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"gate_stage": {"class": "telemetry"}}})
        assert hr.hook_enabled("gate_stage", "compliance") is True
        assert hr.hook_mode("gate_stage", "compliance") == "blocking"

    def test_malformed_config_class_defaults_and_crash_logged(self, tmp_path, monkeypatch):
        cfg_path = tmp_path / "harness-hooks.yaml"
        cfg_path.write_text("hooks: [this is: not a mapping", encoding="utf-8")
        monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg_path))
        monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(tmp_path / "logs"))
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
        sys.modules.pop("hook_runtime", None)
        import hook_runtime
        importlib.reload(hook_runtime)
        hook_runtime._reset_config_cache()
        # Safe per-class defaults survive a broken config file.
        assert hook_runtime.hook_enabled("track_something", "telemetry") is True
        assert hook_runtime.hook_enabled("some_nudge", "nudge") is False
        assert hook_runtime.hook_enabled("gate_stage", "compliance") is True
        assert len(_crash_lines(tmp_path)) >= 1


class TestHookMode:
    def test_compliance_default_blocking(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}})
        assert hr.hook_mode("gate_stage", "compliance") == "blocking"

    def test_advisory_requires_explicit_opt_in(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"gate_stage": {"mode": "advisory"}}})
        assert hr.hook_mode("gate_stage", "compliance") == "advisory"

    def test_weird_mode_value_falls_to_blocking_for_compliance(self, tmp_path, monkeypatch):
        # For a compliance hook the SAFE default is blocking (inverse of PS
        # memory_gap_mode, where safe meant advisory).
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"gate_stage": {"mode": "BLOCKING-ish"}}})
        assert hr.hook_mode("gate_stage", "compliance") == "blocking"

    def test_nudge_mode_always_advisory(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"some_nudge": {"mode": "blocking"}}})
        # A nudge can never escalate itself to blocking via config.
        assert hr.hook_mode("some_nudge", "nudge") == "advisory"


# ---------------------------------------------------------------------------
# telemetry wrapper (ported PS)
# ---------------------------------------------------------------------------

class TestRunTelemetryHook:
    def test_enabled_runs_core_and_continues(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}})
        seen, out = {}, []
        monkeypatch.setattr(hr.sys.stdout, "write", lambda s: out.append(s))
        hr.run_telemetry_hook("track_x", lambda data: seen.update(data),
                              raw='{"k": 1}')
        assert seen == {"k": 1}
        assert '"continue"' in "".join(out)

    def test_disabled_skips_core_still_continues(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"track_x": {"enabled": False}}})
        called, out = [], []
        monkeypatch.setattr(hr.sys.stdout, "write", lambda s: out.append(s))
        hr.run_telemetry_hook("track_x", lambda data: called.append(1),
                              raw='{"k": 1}')
        assert called == []
        assert '"continue"' in "".join(out)

    def test_core_exception_crash_logged_and_still_continues(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}})
        out = []
        monkeypatch.setattr(hr.sys.stdout, "write", lambda s: out.append(s))

        def boom(_data):
            raise RuntimeError("core-broke")

        hr.run_telemetry_hook("track_x", boom, raw="{}")
        assert '"continue"' in "".join(out)
        assert any("core-broke" in r["msg"] for r in _crash_lines(tmp_path))


# ---------------------------------------------------------------------------
# compliance wrapper — fail-closed, its own top-level guard
# ---------------------------------------------------------------------------

def _mini_gate(tmp_path, body: str) -> Path:
    """Write a minimal compliance hook that uses run_compliance_hook."""
    p = tmp_path / "mini_gate.py"
    p.write_text(
        "import sys\n"
        f"sys.path.insert(0, {str(_HOOKS)!r})\n"
        "import hook_runtime\n"
        "HOOK_CLASS = 'compliance'\n"
        f"def core(data):\n{body}\n"
        "if __name__ == '__main__':\n"
        "    hook_runtime.run_compliance_hook('mini_gate', core)\n",
        encoding="utf-8",
    )
    return p


def _run_gate(gate: Path, tmp_path, stdin='{}', env_extra=None):
    env = dict(os.environ)
    env["HARNESS_HOOK_LOG_DIR"] = str(tmp_path / "logs")
    env.pop("PYTEST_CURRENT_TEST", None)
    env.pop("HARNESS_HOOK_CONFIG", None)
    for k, v in (env_extra or {}).items():
        env[k] = v
    return subprocess.run([sys.executable, str(gate)], input=stdin,
                          capture_output=True, text=True, env=env)


class TestRunComplianceHook:
    def test_pass_exits_zero_and_continues(self, tmp_path, monkeypatch):
        gate = _mini_gate(tmp_path, "    return None\n")
        proc = _run_gate(gate, tmp_path)
        assert proc.returncode == 0
        assert '"continue"' in proc.stdout

    def test_queued_system_message_drained_into_single_stdout_write(self, tmp_path):
        # A soft/advisory core() (gate_stage's DoD-soft path is the real caller,
        # H2-resolved) may QUEUE a systemMessage instead of writing stderr; the
        # wrapper drains it into the SAME terminal write it would have made
        # anyway -- stdout stays one JSON blob (json.loads succeeding proves no
        # trailing emit_continue() double-write happened).
        gate = _mini_gate(
            tmp_path,
            "    hook_runtime.queue_system_message('soft: not blocking')\n"
            "    return None\n",
        )
        proc = _run_gate(gate, tmp_path)
        assert proc.returncode == 0
        assert proc.stderr == ""
        out = json.loads(proc.stdout)
        assert out.get("continue") is True
        assert out.get("systemMessage") == "soft: not blocking"

    def test_no_queued_message_behaves_exactly_as_before(self, tmp_path):
        # Every OTHER compliance hook never calls queue_system_message(): the
        # queue is empty, so the wrapper falls through to plain emit_continue()
        # -- byte-identical to pre-H2 behavior.
        gate = _mini_gate(tmp_path, "    return None\n")
        proc = _run_gate(gate, tmp_path)
        assert proc.returncode == 0
        out = json.loads(proc.stdout)
        assert out == {"continue": True}

    def test_block_exits_two_with_reason(self, tmp_path):
        gate = _mini_gate(tmp_path, "    return 'missing artifact: verification'\n")
        proc = _run_gate(gate, tmp_path)
        assert proc.returncode == 2
        assert "missing artifact: verification" in proc.stderr

    def test_core_crash_fails_CLOSED_exit_two(self, tmp_path):
        # The PS-corpus failure mode was exit 1 / silent sleep; compliance demands exit 2.
        gate = _mini_gate(tmp_path, "    raise RuntimeError('gate-internals-broke')\n")
        proc = _run_gate(gate, tmp_path)
        assert proc.returncode == 2
        assert "gate-internals-broke" in proc.stderr
        assert _crash_lines(tmp_path)  # crash audit captured it

    def test_missing_dependency_exits_two_with_install_command(self, tmp_path):
        # Simulate a machine that skipped preflight: core import fails.
        gate = _mini_gate(
            tmp_path,
            "    import a_dependency_that_is_not_installed\n",
        )
        proc = _run_gate(gate, tmp_path)
        assert proc.returncode == 2
        assert "pip install" in proc.stderr or "preflight" in proc.stderr

    def test_disabled_skips_core_exits_zero(self, tmp_path):
        cfg = tmp_path / "harness-hooks.yaml"
        cfg.write_text(
            yaml.safe_dump({"hooks": {"mini_gate": {"enabled": False}}}),
            encoding="utf-8",
        )
        gate = _mini_gate(tmp_path, "    raise RuntimeError('must not run')\n")
        proc = _run_gate(gate, tmp_path,
                         env_extra={"HARNESS_HOOK_CONFIG": str(cfg)})
        assert proc.returncode == 0
        assert '"continue"' in proc.stdout
        assert "must not run" not in proc.stderr

    def test_advisory_mode_warns_but_exits_zero(self, tmp_path):
        cfg = tmp_path / "harness-hooks.yaml"
        cfg.write_text(
            yaml.safe_dump({"hooks": {"mini_gate": {"mode": "advisory"}}}),
            encoding="utf-8",
        )
        gate = _mini_gate(tmp_path, "    return 'would block: missing artifact'\n")
        proc = _run_gate(gate, tmp_path,
                         env_extra={"HARNESS_HOOK_CONFIG": str(cfg)})
        assert proc.returncode == 0
        assert "would block" in proc.stderr
        assert '"continue"' in proc.stdout


# ---------------------------------------------------------------------------
# nudge wrapper
# ---------------------------------------------------------------------------

class TestRunNudgeHook:
    def test_disabled_by_default_core_never_runs(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}})
        called, out = [], []
        monkeypatch.setattr(hr.sys.stdout, "write", lambda s: out.append(s))
        hr.run_nudge_hook("some_nudge", lambda d: called.append(1), raw="{}")
        assert called == []
        assert '"continue"' in "".join(out)

    def test_enabled_message_goes_to_stderr_exit_zero(self, tmp_path, monkeypatch, capsys):
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"some_nudge": {"enabled": True}}})
        hr.run_nudge_hook("some_nudge", lambda d: "consider running hs:test",
                          raw="{}")
        captured = capsys.readouterr()
        assert "consider running hs:test" in captured.err
        assert '"continue"' in captured.out


# ---------------------------------------------------------------------------
# resolve_actor — every hook can resolve independently
# ---------------------------------------------------------------------------

class TestResolveActor:
    def _clean(self, monkeypatch):
        for var in ("CI", "GITLAB_CI", "GITHUB_ACTIONS", "HARNESS_USER",
                    "HARNESS_AGENT", "HARNESS_STATE_DIR"):
            monkeypatch.delenv(var, raising=False)

    def test_ci_env_wins(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path)
        self._clean(monkeypatch)
        monkeypatch.setenv("CI", "true")
        monkeypatch.setenv("HARNESS_USER", "alice")
        assert hr.resolve_actor() == "ci"

    def test_harness_user_env(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path)
        self._clean(monkeypatch)
        monkeypatch.setenv("HARNESS_USER", "alice")
        assert hr.resolve_actor() == "user:alice"

    def test_agent_suffix(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path)
        self._clean(monkeypatch)
        monkeypatch.setenv("HARNESS_USER", "alice")
        monkeypatch.setenv("HARNESS_AGENT", "researcher")
        assert hr.resolve_actor() == "user:alice/agent:researcher"

    def test_session_file_cache_read(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path)
        self._clean(monkeypatch)
        state = tmp_path / "state"
        (state / "sessions").mkdir(parents=True)
        (state / "sessions" / "s1.json").write_text(
            json.dumps({"actor": "user:bob"}), encoding="utf-8")
        monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
        assert hr.resolve_actor(session_id="s1") == "user:bob"

    def test_missing_session_file_falls_through_to_env_chain(self, tmp_path, monkeypatch):
        # a hook must not assume session_init ever ran.
        hr = _fresh(monkeypatch, tmp_path)
        self._clean(monkeypatch)
        monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "nowhere"))
        monkeypatch.setenv("HARNESS_USER", "carol")
        assert hr.resolve_actor(session_id="ghost") == "user:carol"

    def test_git_email_fallback_then_user(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path)
        self._clean(monkeypatch)
        monkeypatch.setattr(hr, "_git_user_email", lambda: "dev@corp.io")
        assert hr.resolve_actor() == "user:dev@corp.io"
        monkeypatch.setattr(hr, "_git_user_email", lambda: "")
        monkeypatch.setenv("USER", "hieubt")
        assert hr.resolve_actor() == "user:hieubt"


# ---------------------------------------------------------------------------
# guard-policy bridge — a REGISTERED guard's posture flows through
# hook_enabled/hook_mode; explicit per-hook config still wins; unregistered
# names fall to their class default.
# ---------------------------------------------------------------------------

class TestGuardPolicyBridge:
    def test_registered_enforcement_blocks_at_balanced(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}}, preset="balanced")
        assert hr.hook_enabled("gate_stage", "compliance") is True
        assert hr.hook_mode("gate_stage", "compliance") == "blocking"

    def test_lenient_downgrades_enforcement_to_advisory(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}}, preset="lenient")
        assert hr.hook_enabled("gate_stage", "compliance") is True
        assert hr.hook_mode("gate_stage", "compliance") == "advisory"

    def test_override_off_disables_registered_guard(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}},
                    preset="balanced", overrides={"gate_stage": "off"})
        assert hr.hook_enabled("gate_stage", "compliance") is False

    def test_legacy_enabled_false_wins_over_policy(self, tmp_path, monkeypatch):
        # Policy resolves block (enabled), but an explicit config bool wins.
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"gate_stage": {"enabled": False}}},
                    preset="balanced")
        assert hr.hook_enabled("gate_stage", "compliance") is False

    def test_legacy_mode_wins_over_policy(self, tmp_path, monkeypatch):
        # Policy resolves blocking, but an explicit advisory opt-in wins.
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"gate_stage": {"mode": "advisory"}}},
                    preset="balanced")
        assert hr.hook_mode("gate_stage", "compliance") == "advisory"

    def test_nudge_not_guard_bridged(self, tmp_path, monkeypatch):
        # Nudges are no longer a guard-policy category: the preset never controls
        # them. With no explicit harness-hooks entry a nudge follows its class
        # default (OFF) at every preset; its on/off lives on the harness-hooks
        # plane, not the guard posture.
        for preset in ("balanced", "strict", "solo"):
            hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}}, preset=preset)
            assert hr.hook_enabled("cook_isolation_nudge", "nudge") is False, preset

    def test_nudge_enabled_via_harness_hooks_plane(self, tmp_path, monkeypatch):
        # The single nudge surface: an explicit enabled flag turns it on
        # regardless of preset (the guard posture has no say).
        hr = _fresh(monkeypatch, tmp_path,
                    config={"hooks": {"cook_isolation_nudge": {"enabled": True}}},
                    preset="solo")
        assert hr.hook_enabled("cook_isolation_nudge", "nudge") is True

    def test_unregistered_name_falls_to_class_default(self, tmp_path, monkeypatch):
        hr = _fresh(monkeypatch, tmp_path, config={"hooks": {}}, preset="balanced")
        assert hr.hook_enabled("track_something", "telemetry") is True
        assert hr.hook_enabled("an_unregistered_nudge", "nudge") is False


def test_bash_command_coerces_payload():
    # DRY helper shared by the PreToolUse(Bash) gates: command coerced to str,
    # "" for every off-shape payload (absent / non-dict tool_input / non-string).
    import hook_runtime
    assert hook_runtime.bash_command({"tool_input": {"command": "ls -la"}}) == "ls -la"
    assert hook_runtime.bash_command({"tool_input": {"command": 123}}) == ""
    assert hook_runtime.bash_command({"tool_input": {}}) == ""
    assert hook_runtime.bash_command({}) == ""
    assert hook_runtime.bash_command({"tool_input": "notadict"}) == ""


def test_project_dir_env_wins_then_stdin_then_none(monkeypatch):
    # SSOT for the nudge hooks' project-root resolution: env CLAUDE_PROJECT_DIR
    # wins; the stdin `cwd` is the fallback; empty/absent on both -> None.
    import hook_runtime
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "/from/env")
    assert hook_runtime.project_dir("/from/stdin") == "/from/env"
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    assert hook_runtime.project_dir("/from/stdin") == "/from/stdin"
    assert hook_runtime.project_dir() is None
    assert hook_runtime.project_dir("") is None  # empty stdin cwd is not usable
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", "")
    assert hook_runtime.project_dir("/from/stdin") == "/from/stdin"  # empty env defers
