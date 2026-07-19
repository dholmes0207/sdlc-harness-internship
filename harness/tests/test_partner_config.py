"""Loader contract for the provider-agnostic partner lane POLICY config (phase 2).

Twin of gemini_partner_config.py but with different axes (allow_live replaces
engine/agy/route_all) — provider discovery is out of scope here (P3), this is
policy only. The loader MUST NOT import the gemini module: that coupling would
coincidentally deadlock the two lanes together (asserted below via AST, mirroring
test_partner_core.py's does-not-import-gemini guard).
"""
import ast
from pathlib import Path

import pytest

import partner_config as pcfg


# Canonical, contradiction-free base. Tests copy + flip one axis at a time.
_BASE = {
    "master": "off",
    "write": "read_only",
    "allow_live": "off",
    "secret_scrub": "warn",
    "purposes": {
        "review": "review", "adversarial-review": "redteam",
        "research": "research", "critique": "critique",
    },
    "timeouts": {"default": 180, "research": 300},
    "retry": {"max_attempts": 2,
              "on_markers": ["rate_limit", "overloaded", "deadline_exceeded"]},
    "cost_warn_usd": 0.50,
}


def _write(tmp_path, **overrides):
    import yaml
    cfg = {**_BASE, **overrides}
    p = tmp_path / "partner.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    return p


def test_resolve_defaults():
    cfg = pcfg.resolve()  # no path/env → shipped harness/data/partner.yaml
    assert cfg["master"] == "off"
    assert cfg["write"] == "read_only"
    assert cfg["allow_live"] == "off"


def test_effective_master_off_inert(tmp_path):
    # master off wins: file says allow_live on, but master off ⇒ effective forces it off
    # (a killed lane can never live-write, regardless of what the file says).
    cfg = pcfg.resolve(_write(tmp_path, master="off", write="worktree_staged",
                              allow_live="on"))
    eff = pcfg.effective(cfg)
    assert eff["allow_live"] == "off"
    assert eff["write"] == "read_only"


def test_readonly_plus_live_rejected(tmp_path):
    # read_only + allow_live:on is a contradictory config — reject at load,
    # do not silently normalize (live-write is meaningless under read_only).
    with pytest.raises(SystemExit) as e:
        pcfg.resolve(_write(tmp_path, write="read_only", allow_live="on"))
    msg = str(e.value)
    assert "forbidden config" in msg
    assert "read_only" in msg and "allow_live=on" in msg


@pytest.mark.parametrize("value", ["on", "off"])
def test_allow_live_roundtrip(tmp_path, value):
    # Both bool branches, non-default exercised: 'on' needs worktree_staged
    # (else it trips the read_only+live contradiction) so the write-path-drops-the-field failure mode
    # (LESSONS) is actually exercised, not masked by the read_only default.
    write = "worktree_staged" if value == "on" else "read_only"
    cfg = pcfg.resolve(_write(tmp_path, allow_live=value, write=write))
    assert cfg["allow_live"] == value


def test_bad_yaml_fail_closed(tmp_path):
    import yaml
    cfg = dict(_BASE)
    del cfg["master"]
    p = tmp_path / "partner.yaml"
    p.write_text(yaml.safe_dump(cfg, sort_keys=False), encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        pcfg.resolve(p)
    msg = str(e.value)
    assert str(p) in msg  # actionable reason names the offending path
    assert "master" in msg


def test_cost_warn_non_number_rejected(tmp_path):
    with pytest.raises(SystemExit) as e:
        pcfg.resolve(_write(tmp_path, cost_warn_usd="abc"))
    assert "cost_warn_usd" in str(e.value)


def test_cost_warn_nan_rejected(tmp_path):
    # NaN < 0 is False, so a naive `< 0` check alone would silently accept it.
    with pytest.raises(SystemExit) as e:
        pcfg.resolve(_write(tmp_path, cost_warn_usd=float("nan")))
    assert "cost_warn_usd" in str(e.value)


def test_partner_config_does_not_import_gemini():
    src = Path(__file__).resolve().parent.parent / "scripts" / "partner_config.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert "gemini" not in alias.name, alias.name
        if isinstance(node, ast.ImportFrom) and node.module:
            assert "gemini" not in node.module, node.module
