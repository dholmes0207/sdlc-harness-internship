"""test_resolve_model.py — single-source model taxonomy for the LLM-CLI surface.

Model ids for the gemini/agy print-mode calls were duplicated across use-mcp,
scout, and ai-multimodal. They now live in ONE yaml (`harness/plugins/hs/data/
models.yaml`) resolved by `scripts/resolve_model.py`. The resolution order the
skills document is: default -> fallback -> hand the `available` list to the LLM.

Contract locked here:
  1. bare call prints the yaml `default` (env $GEMINI_MODEL overrides it);
  2. `--fallback` prints the yaml `fallback`;
  3. `--tier pro|flash|flash_lite` prints the mapped model;
  4. `--list` prints every `available` model (one per line);
  5. config integrity: default + fallback + every tier value are all in `available`;
  6. an unknown `--tier` exits non-zero (fail-closed, not a silent default).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
_PLUGIN = _REPO / "harness" / "plugins" / "hs"
_SCRIPT = _PLUGIN / "scripts" / "resolve_model.py"
_YAML = _PLUGIN / "data" / "models.yaml"


def _run(*args, env=None):
    e = dict(os.environ)
    e.pop("GEMINI_MODEL", None)
    if env:
        e.update(env)
    return subprocess.run([sys.executable, str(_SCRIPT), *args],
                          capture_output=True, text=True, env=e)


def _cfg():
    import yaml
    return yaml.safe_load(_YAML.read_text(encoding="utf-8"))


def test_files_exist():
    assert _SCRIPT.exists(), "resolve_model.py missing"
    assert _YAML.exists(), "models.yaml missing"


def test_bare_prints_default():
    cfg = _cfg()
    r = _run()
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == cfg["default"]


def test_env_overrides_default():
    r = _run(env={"GEMINI_MODEL": "gemini-custom-xyz"})
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "gemini-custom-xyz"


def test_fallback():
    cfg = _cfg()
    r = _run("--fallback")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == cfg["fallback"]


@pytest.mark.parametrize("tier", ["pro", "flash", "flash_lite"])
def test_tier(tier):
    cfg = _cfg()
    r = _run("--tier", tier)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == cfg["tiers"][tier]


def test_list_returns_available():
    cfg = _cfg()
    r = _run("--list")
    assert r.returncode == 0, r.stderr
    printed = [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]
    assert printed == list(cfg["available"])


def test_config_values_are_all_available():
    cfg = _cfg()
    avail = set(cfg["available"])
    assert cfg["default"] in avail, "default not in available"
    assert cfg["fallback"] in avail, "fallback not in available"
    for tier, model in cfg["tiers"].items():
        assert model in avail, f"tier {tier}={model} not in available"


def test_unknown_tier_fails_closed():
    r = _run("--tier", "nope")
    assert r.returncode != 0


def test_json_dump_shape():
    r = _run("--json")
    assert r.returncode == 0, r.stderr
    data = json.loads(r.stdout)
    for key in ("available", "tiers", "default", "fallback"):
        assert key in data
