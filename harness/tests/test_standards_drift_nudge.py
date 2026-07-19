"""Tests for the standards_drift_nudge hook.

A thin NUDGE-class wrapper around the `standards_drift` detector. Never blocks:
when this session edited architecture/standards code without touching the two
auto-loaded prose standards it surfaces a one-line advisory (pointing at /hs:docs)
and records one observation in the audit trace, then allows. Properties under test:

  - no-op guard: PostToolUse appends each edited file_path to an ephemeral,
    session-keyed flag; Stop judges ONLY the paths this session wrote.
  - silence when the author kept docs in sync (a context doc was touched too).
  - enable resolution: HARNESS_STANDARDS_DRIFT_NUDGE env override wins; else config.
  - visible degradation: a broken detector chain records a *_degraded event.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hook_runtime  # noqa: E402,F401

HOOK_PATH = _HOOKS / "standards_drift_nudge.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("standards_drift_nudge", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # Reset the config cache on the SAME hook_runtime instance the freshly-loaded
    # hook binds (another test may have swapped sys.modules["hook_runtime"]); else
    # a stale cache of the real harness-hooks.yaml leaks the live enabled flag in.
    sys.modules["hook_runtime"]._reset_config_cache()
    return mod


def _write_config(path: Path, enabled: bool = True):
    path.write_text(
        "hooks:\n  standards_drift_nudge:\n    enabled: %s\n"
        % ("true" if enabled else "false"),
        encoding="utf-8",
    )


def _trace_events(state: Path):
    out = []
    for f in (state / "trace").glob("trace-*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    tdir = tmp_path / "tmp"
    logs = tmp_path / "logs"
    for d in (state, tdir, logs):
        d.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    _write_config(cfg, enabled=True)
    # Pin the watched trees to the harness dogfood set so these integration tests
    # judge against a known config, not the ambient repo standards.yaml (whose
    # shipped default is now generic). The detector reads $HARNESS_STANDARDS_CONFIG.
    std_cfg = tmp_path / "standards.yaml"
    std_cfg.write_text(
        "drift:\n"
        "  watch_trees:\n"
        "    - harness/hooks/\n    - harness/scripts/\n    - harness/plugins/\n"
        "    - harness/data/\n    - harness/schemas/\n"
        "  context_docs:\n"
        "    - docs/system-architecture.md\n    - docs/harness/system-architecture.md\n"
        "    - docs/code-standards.md\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("TMPDIR", str(tdir))
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(logs))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.setenv("HARNESS_STANDARDS_CONFIG", str(std_cfg))
    monkeypatch.setenv("HARNESS_USER", "tester")
    monkeypatch.delenv("HARNESS_STANDARDS_DRIFT_NUDGE", raising=False)
    hook_runtime._reset_config_cache()
    yield {"state": state, "tmp": tdir, "cfg": cfg}
    # Teardown: this fixture points HARNESS_HOOK_CONFIG at a tmp cfg and populates
    # the module-level _config_cache; monkeypatch restores the env but not the cache.
    # Reset it so the tmp config never leaks into a later test FILE that reads the
    # shipped default (e.g. a *_default_enabled check).
    hook_runtime._reset_config_cache()


def _post(mod, path, sid):
    return mod.handle_post_tool_use(
        {"session_id": sid, "tool_input": {"file_path": path}})


# Each test uses a distinct session_id so the ephemeral $TMPDIR flag of one test
# can never be read by another, independent of per-test TMPDIR isolation.

def test_post_appends_path_to_flag():
    mod = _load_hook()
    _post(mod, "harness/hooks/foo.py", "sd-post")
    assert "harness/hooks/foo.py" in mod.read_touched_paths("sd-post")


def test_stop_signals_on_code_without_docs(_env, capsys):
    mod = _load_hook()
    _post(mod, "harness/hooks/foo.py", "sd-sig")
    rc = mod.handle_stop({"session_id": "sd-sig"})
    assert rc == mod.ALLOW_EXIT
    assert "standards-drift" in capsys.readouterr().err.lower()
    evs = [e for e in _trace_events(_env["state"]) if e.get("event") == "standards_drift_observation"]
    assert len(evs) == 1


def test_stop_silent_when_doc_also_touched(_env, capsys):
    mod = _load_hook()
    _post(mod, "harness/scripts/bar.py", "sd-sync")
    _post(mod, "docs/system-architecture.md", "sd-sync")
    mod.handle_stop({"session_id": "sd-sync"})
    evs = [e for e in _trace_events(_env["state"]) if e.get("event") == "standards_drift_observation"]
    assert evs == []


def test_stop_noop_without_flag(_env):
    mod = _load_hook()
    mod.handle_stop({"session_id": "sd-never-wrote"})
    assert _trace_events(_env["state"]) == []


def test_disabled_config_writes_no_flag(_env):
    _write_config(_env["cfg"], enabled=False)
    hook_runtime._reset_config_cache()
    mod = _load_hook()
    _post(mod, "harness/hooks/foo.py", "sd-disabled")
    assert mod.read_touched_paths("sd-disabled") == []


def test_env_override_off_beats_config_on(_env, monkeypatch):
    monkeypatch.setenv("HARNESS_STANDARDS_DRIFT_NUDGE", "0")
    mod = _load_hook()
    _post(mod, "harness/hooks/foo.py", "sd-envoff")
    assert mod.read_touched_paths("sd-envoff") == []


def test_second_stop_is_silent_after_clear(_env):
    # the flag is cleared after a turn's judgment: one batch of code edits nudges
    # once, and an idle follow-up turn (no new edits) stays quiet.
    mod = _load_hook()
    _post(mod, "harness/hooks/foo.py", "sd-clear")
    mod.handle_stop({"session_id": "sd-clear"})
    mod.handle_stop({"session_id": "sd-clear"})
    evs = [e for e in _trace_events(_env["state"]) if e.get("event") == "standards_drift_observation"]
    assert len(evs) == 1
    assert mod.read_touched_paths("sd-clear") == []


def test_degraded_keeps_flag_for_retry(_env, monkeypatch):
    mod = _load_hook()
    _post(mod, "harness/hooks/foo.py", "sd-retry")
    monkeypatch.setattr(mod, "_import_detector", lambda: (_ for _ in ()).throw(ImportError("x")))
    mod.handle_stop({"session_id": "sd-retry"})
    # degraded must NOT clear — the judgment retries once the detector recovers
    assert mod.read_touched_paths("sd-retry") == ["harness/hooks/foo.py"]


def test_degraded_when_detector_broken(_env, monkeypatch):
    mod = _load_hook()
    _post(mod, "harness/hooks/foo.py", "sd-degraded")

    def _boom():
        raise ImportError("no detector")

    monkeypatch.setattr(mod, "_import_detector", _boom)
    rc = mod.handle_stop({"session_id": "sd-degraded"})
    assert rc == mod.ALLOW_EXIT
    evs = [e for e in _trace_events(_env["state"]) if e.get("event") == "standards_drift_degraded"]
    assert len(evs) == 1


# --- commit leg (final net: git-truth at a real checkpoint) -------------------

def _commit_payload(cmd="git commit -m x", sid="sd-commit"):
    return {"session_id": sid, "tool_input": {"command": cmd}}


def test_commit_signals_on_staged_code_without_docs(_env, monkeypatch, capsys):
    mod = _load_hook()
    monkeypatch.setattr(mod, "staged_paths", lambda pd: ["harness/scripts/x.py"])
    rc = mod.handle_commit(_commit_payload())
    assert rc == mod.ALLOW_EXIT
    assert "standards-drift" in capsys.readouterr().err.lower()
    evs = [e for e in _trace_events(_env["state"]) if e.get("event") == "standards_drift_commit_observation"]
    assert len(evs) == 1


def test_commit_silent_when_staged_includes_doc(_env, monkeypatch):
    # the git-truth net still silences when the SAME commit carries a context doc —
    # but unlike the session flag it cannot be masked by an unrelated earlier edit.
    mod = _load_hook()
    monkeypatch.setattr(mod, "staged_paths",
                        lambda pd: ["harness/scripts/x.py", "docs/code-standards.md"])
    mod.handle_commit(_commit_payload())
    evs = [e for e in _trace_events(_env["state"]) if e.get("event") == "standards_drift_commit_observation"]
    assert evs == []


def test_commit_noop_on_non_commit_command(_env, monkeypatch):
    mod = _load_hook()
    called = {"n": 0}

    def _spy(pd):
        called["n"] += 1
        return ["harness/scripts/x.py"]

    monkeypatch.setattr(mod, "staged_paths", _spy)
    mod.handle_commit({"session_id": "s", "tool_input": {"command": "ls -la"}})
    assert called["n"] == 0  # non-commit command -> never reaches the git read
    evs = [e for e in _trace_events(_env["state"]) if e.get("event") == "standards_drift_commit_observation"]
    assert evs == []


def test_commit_disabled_is_inert(_env, monkeypatch):
    _write_config(_env["cfg"], enabled=False)
    mod = _load_hook()
    monkeypatch.setattr(mod, "staged_paths", lambda pd: ["harness/scripts/x.py"])
    mod.handle_commit(_commit_payload())
    evs = [e for e in _trace_events(_env["state"]) if e.get("event") == "standards_drift_commit_observation"]
    assert evs == []
