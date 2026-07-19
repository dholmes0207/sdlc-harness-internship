"""test_bash_write_guard.py — PostToolUse:Bash advisory that SURFACES a write to
a guarded config path spelled through the shell (telemetry-class, fail-open).

write_guard (PreToolUse:Write|Edit) blocks the agent's file tools from editing
gate config, but it is blind to a Bash redirect / tee / sed -i / python open()
aimed at the same path. This hook can't block (PostToolUse runs AFTER the
command), so it does the one honest thing left: detect the bypass, record it to
the telemetry ledger, and nudge — the edited file is tracked, so it also shows
as a git diff. Defense-in-depth surfacing, never a gate.

The blessed workaround (write to /tmp then cp/mv into place) is deliberately NOT
flagged: cp/mv is the sanctioned, visible path. Reading a guarded file and
redirecting ELSEWHERE is not a write to it and must stay silent.
"""
import json
import sys
from pathlib import Path

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
sys.path.insert(0, str(_HOOKS))

import bash_write_guard as g  # noqa: E402


def _rels(cmd):
    return {rel for rel, _pat in g.bypass_targets(cmd)}


# --- positive: stealth writes to a guarded path are flagged -------------------

def test_redirect_overwrite_into_guarded_hook_flagged():
    assert "harness/hooks/gate_stage.py" in _rels("echo x > harness/hooks/gate_stage.py")


def test_append_redirect_into_guarded_data_flagged():
    assert "harness/data/stage-policy.yaml" in _rels("printf 'r: []' >> harness/data/stage-policy.yaml")


def test_tee_into_guarded_data_flagged():
    assert "harness/data/stage-policy.yaml" in _rels(
        "echo policy | tee harness/data/stage-policy.yaml")


def test_tee_append_flag_into_guarded_flagged():
    assert "harness/data/ownership.yaml" in _rels(
        "echo x | tee -a harness/data/ownership.yaml")


def test_sed_inplace_on_guarded_script_flagged():
    assert "harness/scripts/fs_guard.py" in _rels(
        "sed -i 's/a/b/' harness/scripts/fs_guard.py")


def test_python_open_write_on_guarded_flagged():
    assert "harness/data/task-store.yaml" in _rels(
        "python3 -c \"open('harness/data/task-store.yaml','w').write('x')\"")


def test_absolute_path_into_guarded_flagged():
    root = g._root()
    abs_target = str(root / "harness" / "hooks" / "write_guard.py")
    assert "harness/hooks/write_guard.py" in _rels("echo x > %s" % abs_target)


# --- negative: legitimate / non-write shapes stay silent ----------------------

def test_reading_guarded_and_redirecting_elsewhere_not_flagged():
    # the guarded file is the SOURCE; the write lands in /tmp — not a write to it
    assert _rels("cat harness/hooks/gate_stage.py > /tmp/out") == set()


def test_cp_into_guarded_is_blessed_workaround_not_flagged():
    assert _rels("cp /tmp/stage-policy.yaml harness/data/stage-policy.yaml") == set()


def test_mv_into_guarded_is_blessed_workaround_not_flagged():
    assert _rels("mv /tmp/x.py harness/hooks/gate_stage.py") == set()


def test_redirect_inside_a_quoted_string_not_flagged():
    # a '>' living inside a quoted literal is NOT a redirect — must not false-fire
    assert _rels('echo "note: run foo > harness/data/stage-policy.yaml"') == set()
    assert _rels('git commit -m "edit > harness/hooks/gate_stage.py"') == set()
    assert _rels("grep 'a > harness/data/stage-policy.yaml' notes.txt") == set()


def test_quoted_then_real_redirect_still_flagged():
    # a quoted arg followed by a REAL unquoted redirect to a guarded path → flag
    assert "harness/data/stage-policy.yaml" in _rels(
        'echo "hello world" > harness/data/stage-policy.yaml')


def test_clobber_force_redirect_flagged():
    assert "harness/data/stage-policy.yaml" in _rels("echo x >| harness/data/stage-policy.yaml")


def test_dd_of_into_guarded_flagged():
    assert "harness/data/stage-policy.yaml" in _rels(
        "dd if=/dev/zero of=harness/data/stage-policy.yaml")


def test_redirect_to_unguarded_path_not_flagged():
    assert _rels("echo x > /tmp/foo.txt") == set()
    assert _rels("echo x > harness/state/scratch.txt") == set()


def test_stderr_redirect_is_not_a_write_target():
    assert _rels("python3 harness/scripts/preflight_deps.py 2>&1 > /tmp/log") == \
        {p for p in _rels("python3 harness/scripts/preflight_deps.py 2>&1 > /tmp/log")
         if p.startswith("harness/data") or p.startswith("harness/hooks")}
    # concretely: nothing guarded is flagged here
    assert _rels("foo 2>&1 | tee /tmp/x") == set()


# --- core() contract: detect → trace + stderr advisory, never blocks ----------

def test_core_writes_advisory_on_bypass(capsys):
    # HIGH priority (H2-resolved): a write_guard bypass is security-relevant, so
    # core() emits it via the spec-guaranteed systemMessage field on stdout, not
    # stderr-on-exit-0 (spec-invisible, INV-3 F-2).
    g.core({"tool_name": "Bash",
            "tool_input": {"command": "echo x > harness/hooks/gate_stage.py"}})
    g.hook_runtime.drain_or_continue()  # core now queues; main()/dispatcher drains the blob
    captured = capsys.readouterr()
    assert captured.err == ""
    out = json.loads(captured.out)
    assert out.get("continue") is True
    msg = out.get("systemMessage", "")
    assert "gate_stage.py" in msg
    assert "git diff" in msg or "tracked" in msg


def test_core_silent_when_no_bypass(capsys):
    g.core({"tool_name": "Bash", "tool_input": {"command": "ls -la"}})
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_core_ignores_non_bash_tool(capsys):
    g.core({"tool_name": "Write",
            "tool_input": {"file_path": "harness/hooks/gate_stage.py"}})
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out == ""


def test_bypass_targets_fail_open_on_junk():
    assert g.bypass_targets("") == []
    assert g.bypass_targets(None) == []


def _hits_artifact(cmd):
    # the forgery gate matches shell_write_targets(...,include_copy_move=True)
    # against plans/*/artifacts/*.json — assert the parser surfaces that path.
    return any("plans/p1/artifacts/verification.json" == t
               for t in g.shell_write_targets(cmd, include_copy_move=True))


def test_forgery_quoted_redirect_target_recovered():
    # AF-1: a quoted redirect target was masked to underscores -> recovered from raw
    assert _hits_artifact("echo '{}' > 'plans/p1/artifacts/verification.json'")


def test_forgery_python_write_text_with_semicolon():
    # AF-3: the ; lives inside the quoted -c payload; mask-before-split keeps it whole
    cmd = ("python3 -c \"import pathlib; "
           "pathlib.Path('plans/p1/artifacts/verification.json').write_text('{}')\"")
    assert _hits_artifact(cmd)


def test_forgery_symlink_at_artifact_path():
    # AF-4: ln creates a path at its link name
    assert _hits_artifact("ln -s /tmp/evil plans/p1/artifacts/verification.json")


def test_forgery_cd_then_relative_write():
    # AF-2: cwd tracking resolves the relative target against the cd'd dir
    assert _hits_artifact("cd plans/p1/artifacts && echo '{}' > verification.json")


# --- main() CLI: stdout stays ONE JSON blob even on the systemMessage path ----

def test_main_cli_writes_single_json_blob_on_bypass(tmp_path):
    import json as _json
    import subprocess
    payload = _json.dumps({
        "tool_name": "Bash",
        "tool_input": {"command": "echo x > harness/hooks/gate_stage.py"},
        "session_id": "bwg-cli",
    })
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(_HOOKS),
        "HARNESS_STATE_DIR": str(tmp_path / "state"),
        "TMPDIR": str(tmp_path),
    }
    r = subprocess.run([sys.executable, str(_HOOKS / "bash_write_guard.py")],
                       input=payload, capture_output=True, text=True,
                       env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    out = _json.loads(r.stdout)  # raises if a second JSON blob trails the first
    assert out.get("continue") is True
    assert "gate_stage.py" in out.get("systemMessage", "")
