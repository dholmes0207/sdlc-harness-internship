"""floor_bash_guard: the Bash-spelled-write floor. core() returns None (pass) or a
block reason (str). 2 arms (precise write-target + obfuscation-wrapper triple-
coincidence) + C3 ln-into-carve + M2 dynamic-tail; role-agnostic for the hard tiers.
Compliance wrapper (exit 2, dev-off, fail-open) driven through a real subprocess."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
HOOK_PATH = _HOOKS / "floor_bash_guard.py"
sys.path.insert(0, str(_HOOKS))
from floor_bash_guard import core  # noqa: E402


def _c(cmd, agent_type=None):
    d = {"tool_name": "Bash", "tool_input": {"command": cmd}, "session_id": "t"}
    if agent_type is not None:
        d["agent_type"] = agent_type
    return d


def _blocked(cmd, agent_type=None):
    return core(_c(cmd, agent_type))


# --- HARD block: core-immune (role-agnostic — blocks parent AND subagent) ---

def test_core_write_blocks_parent():
    assert _blocked("echo x > harness/hooks/agent_rbac_guard.py", agent_type="_parent")


def test_core_write_blocks_subagent():
    assert _blocked("echo x > harness/hooks/agent_rbac_guard.py", agent_type="hs:developer")


def test_core_config_blocks():
    assert _blocked("printf x > harness/data/agent-permissions.yaml")


def test_git_write_blocks():
    assert _blocked("echo x > .git/config")


def test_secrets_write_blocks():
    assert _blocked("echo x > .env")


def test_env_template_write_allowed():
    assert _blocked("echo x > .env.example") is None


def test_claude_settings_blocks():
    assert _blocked("echo x > .claude/settings.local.json")


# --- HARD block: hard-binary (role-agnostic) + carve + state ---

def test_hard_binary_blocks_parent():
    assert _blocked("echo x > harness/plugins/hs/y.py", agent_type="_parent")


def test_hard_binary_blocks_subagent():
    assert _blocked("echo x > harness/plugins/hs/y.py", agent_type="hs:developer")


def test_carve_tests_allowed():
    assert _blocked("printf x > harness/tests/tmp_probe.py") is None


def test_state_not_carved_blocks():
    assert _blocked("echo x >> harness/state/trace/x.jsonl")


# --- C1: obfuscation-wrapper triple-coincidence + precise tee/dd/sed/cp ---

def test_eval_wrapped_blocks():
    assert _blocked('eval "echo x > harness/hooks/agent_rbac_guard.py"')


def test_sh_c_wrapped_blocks():
    assert _blocked("sh -c 'printf x > harness/data/agent-permissions.yaml'")


def test_tee_quoted_blocks():
    assert _blocked("printf x | tee harness/hooks/x.py")


def test_python_c_write_blocks():
    assert _blocked("python3 -c \"open('harness/hooks/x.py','w').write('x')\"")


def test_dd_into_core_blocks():
    assert _blocked("dd if=/tmp/x of=harness/data/agent-permissions.yaml")


def test_sed_i_into_core_blocks():
    assert _blocked("sed -i s/a/b/ harness/hooks/agent_rbac_guard.py")


def test_cp_into_core_blocks():
    assert _blocked("cp /tmp/x harness/hooks/agent_rbac_guard.py")


# --- C3: ln-into-carve + write in same command ---

def test_symlink_into_carve_blocks():
    assert _blocked("ln -s ../hooks harness/tests/sneak && echo x > harness/tests/sneak/agent_rbac_guard.py")


# --- M2: dynamic prefix, directory-qualified literal tail ---

def test_dynamic_literal_tail_blocks():
    assert _blocked("echo x > $H/hooks/agent_rbac_guard.py")


def test_residual_no_literal_not_claimed():
    # segment `hooks/` hidden inside $D -> no dir-qualified literal -> NOT blocked
    # preventively (documented residual: detective + git-diff + OS-read-only carry it).
    assert _blocked("D=harness/hooks; echo x > $D/agent_rbac_guard.py") is None


def test_dynamic_unrelated_allowed():
    assert _blocked("echo x > $LOG") is None
    assert _blocked("echo x > /tmp/a.log") is None


# --- arm-2 must not over-block legit writes (review findings) ---

def test_wrapped_write_to_allow_zone_not_blocked():
    # a wrapped write to an ALLOW path (plans/reports) must NOT trip arm-2 just because
    # a broad prefix (plans/) appears in the command — the needle must be specific.
    assert _blocked('python3 -c "print(1)" > plans/reports/status.md') is None


def test_wrapped_read_of_protected_not_blocked():
    # open() with no write mode is a READ — a wrapped read of a protected path is not a
    # write and must not block (this is a WRITE floor). Precise arm-1 also allows a read.
    assert _blocked("python3 -c \"open('harness/x').read()\"") is None


def test_env_anchors_on_path_segment_not_substring():
    # `.env` must anchor on a path segment, not match a substring of `.environment`
    # or a `*.env.sample` template (which deny_matcher deliberately allows).
    assert _blocked('eval "echo hi > server.environment"') is None
    assert _blocked('python3 -c "print(1)" > app.env.sample') is None
    # the bare secret file still blocks
    assert _blocked("echo x > .env")


# --- ALLOW: read-redirect (write floor, not read-gate) + plain source ---

def test_read_redirect_allowed():
    assert _blocked("cat harness/hooks/agent_rbac_guard.py > /tmp/y") is None


def test_plain_source_allowed():
    assert _blocked("echo x > src/app.ts") is None


def test_junk_command_passes():
    assert _blocked("") is None
    assert _blocked("   ") is None


# --- compliance wrapper via a real subprocess (exit 2 / fail-open / dev-off) ---

def _cfg(tmp_path, body):
    p = tmp_path / "hooks.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def _run(payload, config=None, extra_env=None, raw=False):
    env = dict(os.environ)
    if config is not None:
        env["HARNESS_HOOK_CONFIG"] = str(config)
    if extra_env:
        env.update(extra_env)
    stdin = payload if raw else json.dumps(payload)
    return subprocess.run([sys.executable, str(HOOK_PATH)],
                          input=stdin, text=True, capture_output=True, env=env)


def test_subprocess_blocks_core_write(tmp_path):
    cfg = _cfg(tmp_path, "hooks: {}\n")
    r = _run(_c("echo x > harness/hooks/agent_rbac_guard.py"), config=cfg)
    assert r.returncode == 2, r.stderr


def test_subprocess_allows_benign(tmp_path):
    cfg = _cfg(tmp_path, "hooks: {}\n")
    r = _run(_c("echo x > src/app.ts"), config=cfg)
    assert r.returncode == 0, r.stderr


def test_subprocess_fail_open_empty_stdin(tmp_path):
    cfg = _cfg(tmp_path, "hooks: {}\n")
    r = _run("", config=cfg, raw=True)
    assert r.returncode == 0, r.stderr


def test_subprocess_dev_off_passes(tmp_path):
    cfg = _cfg(tmp_path, "hooks:\n  floor_bash_guard: {enabled: false}\n")
    r = _run(_c("echo x > harness/hooks/agent_rbac_guard.py"), config=cfg)
    assert r.returncode == 0, r.stderr


def test_subprocess_unresolvable_root_fail_closed(tmp_path):
    cfg = _cfg(tmp_path, "hooks: {}\n")
    r = _run(_c("echo x > src/app.ts"), config=cfg,
             extra_env={"HARNESS_BIN_ROOT": str(tmp_path / "nonexistent")})
    assert r.returncode == 2, r.stderr


# --- dispatch wiring ---

def test_dispatch_has_floor_bash_row():
    import yaml
    disp = yaml.safe_load((Path(__file__).resolve().parent.parent / "data" / "hook-dispatch.yaml").read_text(encoding="utf-8"))
    rows = disp["groups"]["PreToolUse:Bash"]
    names = [r["name"] for r in rows]
    assert "floor_bash_guard" in names
    # positioned after bash_safety_guard (the plan's placement)
    assert names.index("floor_bash_guard") > names.index("bash_safety_guard")
    row = next(r for r in rows if r["name"] == "floor_bash_guard")
    assert row["class"] == "compliance"
    assert not row.get("fail_open", False)  # hard floor: fail-closed on crash
