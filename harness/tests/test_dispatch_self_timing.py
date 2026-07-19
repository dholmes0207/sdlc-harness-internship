"""test_dispatch_self_timing.py — the dispatcher records per-core elapsed_ms.

Every core the dispatcher runs is timed and the number written to the diag stream,
so any machine gets its own hook-cost profile (the input to the perf dashboard).
Timing is always-on (INFO); HARNESS_DEBUG adds per-core verbose detail.
"""
import json
import os
import sys
import textwrap
from pathlib import Path

import pytest

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
if str(_HOOKS) not in sys.path:
    sys.path.insert(0, str(_HOOKS))

import hook_runtime  # noqa: E402
import hook_dispatch  # noqa: E402

_FIX = textwrap.dedent('''
    def a(data): return None
    def b(data): return None
''')


def _timing_records(state_dir):
    p = Path(state_dir) / "diag" / "diag.jsonl"
    if not p.is_file():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines()
            if l.strip() and json.loads(l).get("event") == "core_timing"]


@pytest.fixture
def env(tmp_path, monkeypatch):
    import importlib
    sys.modules["hook_runtime"] = hook_runtime
    importlib.reload(hook_dispatch)
    fixdir = tmp_path / "fix"
    fixdir.mkdir()
    (fixdir / "timing_fix.py").write_text(_FIX, encoding="utf-8")
    monkeypatch.syspath_prepend(str(fixdir))
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    p = tmp_path / "reg.yaml"
    import yaml
    p.write_text(yaml.safe_dump({"groups": {"PreToolUse:Bash": [
        {"name": "a", "module": "timing_fix", "entry": "a", "class": "telemetry"},
        {"name": "b", "module": "timing_fix", "entry": "b", "class": "telemetry"},
    ]}}), encoding="utf-8")
    monkeypatch.setenv("HARNESS_HOOK_DISPATCH_CONFIG", str(p))
    return tmp_path


def test_each_core_gets_elapsed_ms(env, monkeypatch):
    monkeypatch.delenv("HARNESS_DEBUG", raising=False)
    code = hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
    assert code == 0
    recs = _timing_records(env / "state")
    assert len(recs) == 2, "one timing record per core"
    for r in recs:
        assert r["hook"] in ("a", "b")
        assert isinstance(r["elapsed_ms"], (int, float)) and r["elapsed_ms"] >= 0
        assert r["class"] == "telemetry" and r["event"] == "core_timing"


def test_timing_is_always_on_not_debug_gated(env, monkeypatch):
    # timing is INFO — present even with HARNESS_DEBUG unset
    monkeypatch.delenv("HARNESS_DEBUG", raising=False)
    hook_dispatch.run(["PreToolUse", "Bash"], stdin_text='{"session_id":"S"}')
    assert _timing_records(env / "state"), "self-timing must be always-on (every machine)"
