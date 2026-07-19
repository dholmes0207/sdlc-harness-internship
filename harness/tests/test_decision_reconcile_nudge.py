"""decision_reconcile_nudge — Stop-event NUDGE: when the register has drifted
>= N new DECs or >= M flips since the last reconcile marker, surface a one-line
advisory pointing at /hs:remember -> decision-reconciler. Never blocks.

Unlike decision_capture_nudge this has NO PostToolUse touched-flag: the signal is
a register COUNT, not "this session edited a file" (brief 6.2). Throttled once per
session; fail-open everywhere; visible degradation on a broken counter lib.
"""
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hook_runtime  # noqa: E402

HOOK_PATH = _HOOKS / "decision_reconcile_nudge.py"


def _load_hook():
    spec = importlib.util.spec_from_file_location("decision_reconcile_nudge", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.modules["hook_runtime"]._reset_config_cache()
    return mod


def _write_config(path, enabled=True):
    path.write_text(
        "hooks:\n  decision_reconcile_nudge:\n    enabled: %s\n"
        % ("true" if enabled else "false"), encoding="utf-8")


def _trace_events(state):
    out = []
    for f in (state / "trace").glob("trace-*.jsonl"):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                out.append(json.loads(line))
    return out


def _rec(n, status="active"):
    return {"id": "DEC-%d" % n, "status": status, "date": "2026-06-29",
            "actor": "u", "ts": "t", "title": "t%d" % n, "rationale": "why"}


def _register(root, records, new_thr=15, flip_thr=8):
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "docs" / "decisions.yaml").write_text(
        yaml.safe_dump(records, sort_keys=False), encoding="utf-8")
    data = root / "harness" / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "decision-governance.yaml").write_text(
        "reconcile_threshold_new_decs: %d\nreconcile_threshold_flips: %d\n"
        % (new_thr, flip_thr), encoding="utf-8")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    tdir = tmp_path / "tmp"
    logs = tmp_path / "logs"
    proj = tmp_path / "proj"
    for d in (state, tdir, logs, proj):
        d.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    _write_config(cfg, enabled=True)
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("TMPDIR", str(tdir))
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(logs))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.setenv("HARNESS_USER", "tester")
    monkeypatch.delenv("HARNESS_DECISION_RECONCILE_NUDGE", raising=False)
    hook_runtime._reset_config_cache()
    yield {"state": state, "cfg": cfg, "proj": proj}


def _over_register(proj):
    # baseline at max=1, then +2 new with threshold 2 -> over
    _register(proj, [_rec(1)], new_thr=2)
    import decision_reconcile as dr
    dr.mark(str(proj))
    _register(proj, [_rec(1), _rec(2), _rec(3)], new_thr=2)


def test_nudge_fires_when_over(_env, capsys):
    # HIGH-priority channel (H2-resolved): the advisory is a systemMessage on
    # stdout, not a stderr line -- stderr-on-exit-0 is spec-invisible (INV-3 F-2).
    mod = _load_hook()
    _over_register(_env["proj"])
    rc = mod.handle_stop({"session_id": "rn-fire", "cwd": str(_env["proj"])})
    assert rc == mod.ALLOW_EXIT
    mod.hook_runtime.drain_or_continue()  # handle_stop now queues; main()/dispatcher drains
    out = json.loads(capsys.readouterr().out)
    assert out.get("continue") is True
    assert "reconcile" in out.get("systemMessage", "").lower()
    evs = [e for e in _trace_events(_env["state"])
           if e.get("event") == "decision_reconcile_observation"]
    assert len(evs) == 1


def test_nudge_silent_when_under(_env, capsys):
    mod = _load_hook()
    _register(_env["proj"], [_rec(1), _rec(2)])     # no marker -> baseline, under
    mod.handle_stop({"session_id": "rn-under", "cwd": str(_env["proj"])})
    captured = capsys.readouterr()
    assert "reconcile" not in captured.err.lower()
    assert "reconcile" not in captured.out.lower()
    evs = [e for e in _trace_events(_env["state"])
           if e.get("event") == "decision_reconcile_observation"]
    assert evs == []


def test_nudge_disabled_silent(_env, capsys):
    _write_config(_env["cfg"], enabled=False)
    mod = _load_hook()
    _over_register(_env["proj"])
    mod.handle_stop({"session_id": "rn-off", "cwd": str(_env["proj"])})
    captured = capsys.readouterr()
    assert "reconcile" not in captured.err.lower()
    assert "reconcile" not in captured.out.lower()


def test_nudge_throttled_once_per_session(_env, capsys):
    mod = _load_hook()
    _over_register(_env["proj"])
    mod.handle_stop({"session_id": "rn-throttle", "cwd": str(_env["proj"])})
    capsys.readouterr()
    mod.handle_stop({"session_id": "rn-throttle", "cwd": str(_env["proj"])})
    captured = capsys.readouterr()
    assert "reconcile" not in captured.err.lower()
    assert "reconcile" not in captured.out.lower()


def test_nudge_degraded_visible(_env, monkeypatch):
    mod = _load_hook()
    _over_register(_env["proj"])
    monkeypatch.setattr(mod, "_import_counter",
                        lambda: (_ for _ in ()).throw(ImportError("x")))
    rc = mod.handle_stop({"session_id": "rn-deg", "cwd": str(_env["proj"])})
    assert rc == mod.ALLOW_EXIT
    evs = [e for e in _trace_events(_env["state"])
           if e.get("event") == "decision_reconcile_degraded"]
    assert len(evs) == 1


def test_main_cli_writes_single_json_blob_with_system_message(_env):
    # main() must write stdout EXACTLY once (a hook's stdout is one JSON blob) --
    # json.loads() failing on trailing data would mean emit_system_message() AND
    # emit_continue() both fired.
    import subprocess
    _over_register(_env["proj"])
    payload = json.dumps({"session_id": "rn-cli", "cwd": str(_env["proj"])})
    env = {
        "HARNESS_STATE_DIR": str(_env["state"]),
        "HARNESS_HOOK_CONFIG": str(_env["cfg"]),
        "TMPDIR": str(_env["state"].parent / "tmp"),  # isolate the throttle flag
        "HARNESS_USER": "tester",
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(_HOOKS),
    }
    (_env["state"].parent / "tmp").mkdir(parents=True, exist_ok=True)
    r = subprocess.run([sys.executable, str(HOOK_PATH)], input=payload,
                       capture_output=True, text=True, env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout)  # raises if more than one JSON value is present
    assert out.get("continue") is True
    assert "reconcile" in out.get("systemMessage", "").lower()


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
