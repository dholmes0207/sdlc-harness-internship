"""Tests for glossary_pointer_inject (SessionStart, telemetry-class, default ON).

A SEPARATE module from voice_inject (single responsibility): it emits a one-line
additionalContext pointing at the glossary so the settled vocabulary is live for
the session. Telemetry posture: default ON, fail-open — disabled / no-glossary /
any error degrades to a plain continue (no context), never a block. Because it is
SessionStart additionalContext (NOT a Stop re-inject), it cannot trigger the
Stop-hook runaway and is safe to default ON.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml

_HOOKS = Path(__file__).resolve().parent.parent / "hooks"
_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
for d in (_HOOKS, _SCRIPTS):
    if str(d) not in sys.path:
        sys.path.insert(0, str(d))

import hook_runtime  # noqa: E402,F401

HOOK_PATH = _HOOKS / "glossary_pointer_inject.py"


def _hook_runtime():
    import hook_runtime as hr
    return sys.modules.get("hook_runtime", hr)


def _load_hook():
    spec = importlib.util.spec_from_file_location("glossary_pointer_inject", HOOK_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_config(path: Path, enabled: bool):
    path.write_text(
        "hooks:\n"
        "  glossary_pointer_inject:\n"
        "    enabled: %s\n" % ("true" if enabled else "false"),
        encoding="utf-8")


def _seed_glossary(root: Path, n: int):
    docs = root / "docs"
    docs.mkdir(parents=True, exist_ok=True)
    (docs / "glossary.yaml").write_text(
        yaml.safe_dump([{"term": "t%d" % i, "definition": "d", "forbidden": [],
                         "backing": []} for i in range(n)], allow_unicode=True),
        encoding="utf-8")


@pytest.fixture(autouse=True)
def _env(tmp_path, monkeypatch):
    state = tmp_path / "state"
    logs = tmp_path / "logs"
    for d in (state, logs):
        d.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "harness-hooks.yaml"
    _write_config(cfg, enabled=True)
    monkeypatch.setenv("HARNESS_STATE_DIR", str(state))
    monkeypatch.setenv("HARNESS_HOOK_LOG_DIR", str(logs))
    monkeypatch.setenv("HARNESS_HOOK_CONFIG", str(cfg))
    monkeypatch.delenv("HARNESS_TELEMETRY_DISABLED", raising=False)
    _hook_runtime()._reset_config_cache()
    yield {"state": state, "cfg": cfg}
    _hook_runtime()._reset_config_cache()


def _run(mod, proj: Path, monkeypatch, source="startup"):
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(proj))
    payload = {"hook_event_name": "SessionStart", "source": source,
               "cwd": str(proj)}
    import io
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        mod.run(raw=json.dumps(payload))
    finally:
        sys.stdout = old
    return buf.getvalue()


def _additional_context(out: str):
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return None
    return data.get("hookSpecificOutput", {}).get("additionalContext")


# ---------------------------------------------------------------------------
# build_pointer (pure-ish unit)
# ---------------------------------------------------------------------------

def test_build_pointer_counts_terms(tmp_path):
    import glossary_pointer as gp
    _seed_glossary(tmp_path, 5)
    line = gp.build_pointer(tmp_path)
    assert line and "5 settled term" in line


def test_build_pointer_none_without_glossary(tmp_path):
    import glossary_pointer as gp
    assert gp.build_pointer(tmp_path) is None


# ---------------------------------------------------------------------------
# hook: telemetry default ON
# ---------------------------------------------------------------------------

def test_enabled_emits_pointer_with_count(_env, tmp_path, monkeypatch):
    mod = _load_hook()
    proj = tmp_path / "proj"
    _seed_glossary(proj, 7)
    ctx = _additional_context(_run(mod, proj, monkeypatch))
    assert ctx and "7 settled term" in ctx


def test_disabled_emits_no_context(_env, tmp_path, monkeypatch):
    mod = _load_hook()
    proj = tmp_path / "proj"
    _seed_glossary(proj, 7)
    _write_config(_env["cfg"], enabled=False)
    _hook_runtime()._reset_config_cache()
    assert _additional_context(_run(mod, proj, monkeypatch)) is None


def test_telemetry_master_kill_emits_no_context(_env, tmp_path, monkeypatch):
    mod = _load_hook()
    proj = tmp_path / "proj"
    _seed_glossary(proj, 7)
    monkeypatch.setenv("HARNESS_TELEMETRY_DISABLED", "1")
    _hook_runtime()._reset_config_cache()
    assert _additional_context(_run(mod, proj, monkeypatch)) is None


def test_no_glossary_emits_no_context(_env, tmp_path, monkeypatch):
    mod = _load_hook()
    proj = tmp_path / "proj"
    proj.mkdir()
    assert _additional_context(_run(mod, proj, monkeypatch)) is None


def test_corrupt_glossary_emits_no_context(_env, tmp_path, monkeypatch):
    mod = _load_hook()
    proj = tmp_path / "proj"
    docs = proj / "docs"
    docs.mkdir(parents=True)
    (docs / "glossary.yaml").write_text("{ this: : is broken", encoding="utf-8")
    # malformed SSOT → count 0 → no pointer, never a raise
    assert _additional_context(_run(mod, proj, monkeypatch)) is None


def test_refires_on_compact(_env, tmp_path, monkeypatch):
    mod = _load_hook()
    proj = tmp_path / "proj"
    _seed_glossary(proj, 3)
    ctx = _additional_context(_run(mod, proj, monkeypatch, source="compact"))
    assert ctx and "3 settled term" in ctx
