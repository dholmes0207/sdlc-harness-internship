"""Tests for the decision_capture_nudge hook (the A-leg of memory-v2).

A thin NUDGE-class wrapper around the `decision_capture` detector. Like
memory_gap_hook it NEVER blocks: when an unrecorded decision-shaped change is
present it surfaces a one-line advisory (pointing at /hs:remember) and records one
observation in the audit trace, then allows. Properties under test:

  - nudge posture: default OFF (config-gated); even ON it only warns + records.
  - no-op guard: a PostToolUse write sets an ephemeral session-keyed touched-flag;
    Stop runs the detector ONLY when that flag is set.
  - visible degradation: a broken detector chain emits a `decision_capture_degraded`
    audit event instead of silently looking alive.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hook_runtime  # noqa: E402,F401 — ensure importable on the path
from conftest import _git  # noqa: E402

HOOK_PATH = _HOOKS / "decision_capture_nudge.py"


def _hook_runtime():
    import hook_runtime as hr  # noqa: E402
    return sys.modules.get("hook_runtime", hr)


def _load_hook():
    spec = importlib.util.spec_from_file_location("decision_capture_nudge", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_config(path: Path, enabled: bool = True):
    path.write_text(
        "hooks:\n"
        "  decision_capture_nudge:\n"
        "    enabled: %s\n" % ("true" if enabled else "false"),
        encoding="utf-8",
    )


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    tdir = tmp_path / "tmp"
    logs = tmp_path / "logs"
    for d in (state, tdir, logs):
        d.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    _write_config(cfg, enabled=True)
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("TMPDIR", str(tdir))
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(logs))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.setenv("HARNESS_USER", "tester")
    _hook_runtime()._reset_config_cache()
    yield {"state": state, "tmp": tdir, "cfg": cfg}
    _hook_runtime()._reset_config_cache()


def _reconfig(cfg: Path, enabled: bool):
    _write_config(cfg, enabled=enabled)
    _hook_runtime()._reset_config_cache()


def _repo(tmp_path, with_new_module: bool):
    """A committed git repo; when `with_new_module`, leave an untracked NEW hook
    module in the tree so the detector returns an unrecorded_decision signal."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.t")
    _git(repo, "config", "user.name", "t")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    if with_new_module:
        hooks = repo / "harness" / "hooks"
        hooks.mkdir(parents=True)
        (hooks / "probe_new.py").write_text("# probe\n", encoding="utf-8")
    return repo


def _stop_payload(proj: Path, session_id="sess-1"):
    return {"session_id": session_id, "cwd": str(proj), "hook_event_name": "Stop"}


def _post_payload(proj: Path, file_path: str, session_id="sess-1"):
    return {"session_id": session_id, "cwd": str(proj),
            "hook_event_name": "PostToolUse", "tool_name": "Write",
            "tool_input": {"file_path": file_path}}


def _trace_events(state: Path):
    tdir = state / "trace"
    out = []
    if tdir.is_dir():
        for f in sorted(tdir.glob("trace-*.jsonl")):
            for line in f.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    out.append(json.loads(line))
    return out


def _events_of(state: Path, name: str):
    return [e for e in _trace_events(state) if e.get("event") == name]


# ---------------------------------------------------------------------------
# no-op guard
# ---------------------------------------------------------------------------

def test_noop_when_flag_unset(_env, tmp_path):
    mod = _load_hook()
    proj = _repo(tmp_path, with_new_module=True)
    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0
    assert _events_of(_env["state"], "decision_capture_observation") == []


def test_post_tool_use_sets_flag(_env, tmp_path):
    mod = _load_hook()
    proj = _repo(tmp_path, with_new_module=False)
    rc = mod.handle_post_tool_use(
        _post_payload(proj, str(proj / "harness" / "scripts" / "x.py"),
                      session_id="sx"), str(proj))
    assert rc == 0
    assert mod.touched_flag_set("sx")


# ---------------------------------------------------------------------------
# nudge: surface advisory + record observation, never block
# ---------------------------------------------------------------------------

def test_unrecorded_decision_surfaced_and_recorded(_env, tmp_path, capsys):
    mod = _load_hook()
    proj = _repo(tmp_path, with_new_module=True)
    mod.set_touched_flag("sess-1")

    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0  # nudge NEVER blocks
    err = capsys.readouterr().err
    assert "probe_new.py" in err
    assert "/hs:remember" in err          # points at the C-leg
    obs = _events_of(_env["state"], "decision_capture_observation")
    assert obs, "an observation must be recorded in the audit trace"


def test_clean_tree_records_nothing(_env, tmp_path, capsys):
    mod = _load_hook()
    proj = _repo(tmp_path, with_new_module=False)
    mod.set_touched_flag("sess-1")
    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0
    assert capsys.readouterr().err.strip() == ""
    assert _events_of(_env["state"], "decision_capture_observation") == []


# ---------------------------------------------------------------------------
# visible degradation
# ---------------------------------------------------------------------------

def test_degraded_trace_when_detector_missing(_env, tmp_path, monkeypatch):
    mod = _load_hook()
    proj = _repo(tmp_path, with_new_module=True)
    mod.set_touched_flag("sess-1")

    def _boom():
        raise ImportError("decision_capture hidden for the test")
    monkeypatch.setattr(mod, "_import_detector", _boom)

    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0
    assert _events_of(_env["state"], "decision_capture_degraded"), \
        "a missing detector must surface a decision_capture_degraded event"
    assert _events_of(_env["state"], "decision_capture_observation") == []


# ---------------------------------------------------------------------------
# nudge default OFF: disabled hook is inert
# ---------------------------------------------------------------------------

def test_disabled_hook_is_inert(_env, tmp_path, capsys):
    mod = _load_hook()
    proj = _repo(tmp_path, with_new_module=True)
    mod.set_touched_flag("sess-1")
    _reconfig(_env["cfg"], enabled=False)

    rc = mod.handle_stop(_stop_payload(proj), str(proj))
    assert rc == 0
    assert capsys.readouterr().err.strip() == ""
    assert _events_of(_env["state"], "decision_capture_observation") == []


def test_hook_writes_nothing_into_the_project(_env, tmp_path):
    mod = _load_hook()
    proj = _repo(tmp_path, with_new_module=True)
    mod.set_touched_flag("sess-1")
    before = {p.relative_to(proj) for p in proj.rglob("*")}
    mod.handle_stop(_stop_payload(proj), str(proj))
    after = {p.relative_to(proj) for p in proj.rglob("*")}
    assert before == after, "the hook must not write into the project tree"


# ---------------------------------------------------------------------------
# CLI shape — invokable standalone in both modes
# ---------------------------------------------------------------------------

def _run(mode, payload, env):
    args = [sys.executable, str(HOOK_PATH)]
    if mode == "post":
        args.append("--post-tool-use")
    return subprocess.run(args, input=json.dumps(payload),
                          capture_output=True, text=True, env=env)


def test_cli_post_then_stop_records_observation(_env, tmp_path):
    proj = _repo(tmp_path, with_new_module=True)
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(proj)
    rc_post = _run("post", _post_payload(
        proj, str(proj / "harness" / "hooks" / "probe_new.py")), env)
    assert rc_post.returncode == 0, rc_post.stderr
    rc_stop = _run("stop", _stop_payload(proj), env)
    assert rc_stop.returncode == 0, rc_stop.stderr
    assert _events_of(_env["state"], "decision_capture_observation"), rc_stop.stderr


def test_cli_stop_noop_exit_zero(_env, tmp_path):
    proj = _repo(tmp_path, with_new_module=False)
    env = dict(os.environ)
    env["CLAUDE_PROJECT_DIR"] = str(proj)
    rc = _run("stop", _stop_payload(proj), env)
    assert rc.returncode == 0, rc.stderr
