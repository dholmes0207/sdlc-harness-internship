"""engine_root_inject — SessionStart engine-root context (global-layout only)."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_HOOKS = _REPO_ROOT / "harness" / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))
if str(_REPO_ROOT / "harness" / "scripts") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "harness" / "scripts"))

import engine_root_inject as eri  # noqa: E402


def _fake_engine(tmp_path: Path) -> Path:
    root = tmp_path / "engine"
    (root / "harness" / "hooks").mkdir(parents=True)
    (root / "harness" / "manifest.json").write_text('{"files": {}}')
    return root


def test_self_host_no_inject(monkeypatch):
    monkeypatch.delenv("HARNESS_BIN_ROOT", raising=False)
    assert eri.core({}) is None


def test_global_resolved_injects_root(monkeypatch, tmp_path):
    root = _fake_engine(tmp_path)
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(root))
    out = eri.core({})
    assert out is not None
    assert "[engine] root=" in out
    assert str(root.resolve()) in out
    assert "resolve against this engine root" in out


def test_global_unresolved_no_inject(monkeypatch, tmp_path):
    # env set but the path does not exist / lacks the markers
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(tmp_path / "gone"))
    assert eri.core({}) is None


def test_global_no_marker_no_inject(monkeypatch, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    monkeypatch.setenv("HARNESS_BIN_ROOT", str(bare))
    assert eri.core({}) is None


def test_fail_open_on_garbage_stdin(tmp_path):
    # subprocess-level: garbage stdin must still yield a continue blob, never a crash.
    r = subprocess.run(
        [sys.executable, str(_HOOKS / "engine_root_inject.py")],
        input="not json", capture_output=True, text=True)
    assert r.returncode == 0
    # stdout is a JSON blob (continue or a hookSpecificOutput); must parse
    json.loads(r.stdout or '{"continue": true}')


def test_survives_telemetry_kill_switch_via_dispatcher(tmp_path):
    """Round-2 regression: the PRODUCTION path is the dispatcher, which gates each
    core by its dispatch-map class BEFORE calling it. A telemetry-class inject is
    dropped there under HARNESS_TELEMETRY_DISABLED regardless of any run()-level
    bypass — so this drives the REAL dispatcher, not core() directly (the phantom
    test the first fix shipped). engine_root_inject is now nudge-class, which the
    kill-switch does not touch, so the engine-root context must still surface."""
    import subprocess
    root = _fake_engine(tmp_path)
    disp = _HOOKS / "hook_dispatch.py"
    penv = dict(os.environ)
    penv["HARNESS_BIN_ROOT"] = str(root)
    penv["HARNESS_TELEMETRY_DISABLED"] = "1"
    penv["HARNESS_HOOK_CONFIG"] = str(_REPO_ROOT / "harness" / "data" / "harness-hooks.yaml")
    penv["HOME"] = str(tmp_path / "emptyhome")
    r = subprocess.run([sys.executable, str(disp), "SessionStart"],
                       input=json.dumps({"session_id": "s1", "source": "startup"}),
                       capture_output=True, text=True, env=penv)
    assert r.returncode == 0, r.stderr
    assert "[engine] root=" in r.stdout, \
        "engine-root context dropped under HARNESS_TELEMETRY_DISABLED: %r" % r.stdout[:300]
