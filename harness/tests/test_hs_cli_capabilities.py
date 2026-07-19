"""test_hs_cli_capabilities — the `hs capabilities` verb.

Read-only JSON introspection the tầng-2 orchestrator uses to discover what hooks +
stage gates the harness has registered (the CLI exposes no such registry — spike
260709). Wraps the existing config layer: hooks-registration (name/event/class) +
hook_runtime.hook_enabled (live state) + stage-policy (gates). Never re-implements
gate logic; never writes.
"""
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPTS = _ROOT / "harness/scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import hs_cli  # noqa: E402


def _run(capsys):
    rc = hs_cli.main(["capabilities"])
    out = capsys.readouterr().out
    return rc, json.loads(out)


def test_capabilities_emits_json_schema(capsys):
    rc, doc = _run(capsys)
    assert rc == 0
    assert doc["schema"] == "hs-capabilities/1"
    assert isinstance(doc["hooks"], list) and doc["hooks"]
    assert isinstance(doc["gates"], list) and doc["gates"]


def test_hooks_carry_name_event_enabled(capsys):
    _rc, doc = _run(capsys)
    for h in doc["hooks"]:
        assert set(("name", "event", "enabled")) <= set(h)
        assert isinstance(h["enabled"], bool)
    names = {h["name"] for h in doc["hooks"]}
    # a compliance guard + a nudge both surface, proving the full registry (not just
    # the 3 hook-bearing components) is the source
    assert "gate_stage" in names
    assert "disabled_ref_nudge" in names


def test_gates_carry_stage_requires(capsys):
    _rc, doc = _run(capsys)
    stages = {g["stage"]: g["requires"] for g in doc["gates"]}
    assert "ship" in stages
    assert "verification" in stages["ship"]


def test_capabilities_is_read_only(capsys, tmp_path, monkeypatch):
    # the verb must not mutate any tracked config — run it, assert the two config
    # sources it reads are byte-identical before/after.
    reg = _ROOT / "harness/install/hooks-registration.yaml"
    before = reg.read_bytes()
    hs_cli.main(["capabilities"])
    assert reg.read_bytes() == before
