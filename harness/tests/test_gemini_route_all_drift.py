"""Route-all wiring + drift guard (phase 8).

The routing DECISION is a real function (`should_route`) so it is unit-tested
(T1–T4). The wiring itself is prose (research/scout SKILL.md) + JS workflows
(base-*.js) — the JS branch is NOT pytest-coverable ("no Node in CI"), so T5 is a
drift-LINT: every already-wired site must still reference the single chokepoint
`gemini_companion`. It catches a REGRESSION (a wired site dropping the ref); it does
NOT catch a brand-new fan-out that forgets to route (F4) — that gap is stated here
and covered by dogfood / real_gemini, not silently hidden. T6 pins the ship-narrow
factory surface.
"""
import sys
from pathlib import Path

import pytest
import yaml

_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "scripts"))
import gemini_partner_config as gpc  # noqa: E402

_WIRED_SITES = [
    _ROOT / "plugins" / "hs" / "skills" / "research" / "SKILL.md",
    _ROOT / "plugins" / "hs" / "skills" / "scout" / "SKILL.md",
    _ROOT / "plugins" / "hs" / "workflows" / "base-fanout-consolidate.js",
    _ROOT / "plugins" / "hs" / "workflows" / "base-pipeline-verify.js",
]

_BASE = {
    "master": "on", "mode": "route-all", "write": "read_only", "stop_review_gate": "off",
    "purposes": {"research": "flash", "scout": "flash", "review": "pro",
                 "critique": "pro", "redteam": "pro", "delegate": "pro", "fix": "pro"},
    "route_all_surface": ["research", "scout"], "overrides": {},
    "timeouts": {"default": 5}, "retry": {"max_attempts": 1, "on_markers": []},
    "secret_scrub": "warn",
}


def _cfg(tmp_path, **over):
    p = tmp_path / "gemini-partner.yaml"
    p.write_text(yaml.safe_dump({**_BASE, **over}, sort_keys=False), encoding="utf-8")
    return gpc.effective(gpc.resolve(p))


# --- T1: route-all + skill in surface → route ------------------------------
def test_t1_route_all_in_surface_routes(tmp_path):
    cfg = _cfg(tmp_path)
    assert gpc.should_route(cfg, "research") is True


# --- T2: partner mode → dormant (never route) ------------------------------
def test_t2_partner_mode_dormant(tmp_path):
    cfg = _cfg(tmp_path, mode="partner")
    assert gpc.should_route(cfg, "research") is False


# --- T3: route-all but skill NOT in surface → Claude ------------------------
def test_t3_out_of_surface_stays_claude(tmp_path):
    cfg = _cfg(tmp_path, route_all_surface=["research"])
    assert gpc.should_route(cfg, "scout") is False


def test_t3b_dogfood_surface_all_routes_any(tmp_path):
    cfg = _cfg(tmp_path, route_all_surface="all")
    assert gpc.should_route(cfg, "scout") is True
    assert gpc.should_route(cfg, "anything") is True


def test_t3c_master_off_never_routes(tmp_path):
    cfg = _cfg(tmp_path, master="off")  # effective() → inert
    assert gpc.should_route(cfg, "research") is False


# --- T4: a routed call to a down gemini degrades loudly (S4) ----------------
def test_t4_routed_down_degrades_not_silent(tmp_path, monkeypatch, capsys):
    sys.path.insert(0, str(_ROOT / "plugins" / "hs" / "scripts"))
    import gemini_companion as gc

    class _Down:
        def __init__(self): pass
        def run(self, **kw): raise gc.AcpError("refused")
    monkeypatch.setattr(gc, "GeminiPrintTransport", _Down)
    p = tmp_path / "gemini-partner.yaml"
    # pin gemini-print so the down primary degrades WITHOUT falling back to a real
    # agy spawn (no HARNESS_AGY_CMD seam here); the test asserts the degrade, not fallback.
    p.write_text(yaml.safe_dump({**_BASE, "engine": "gemini-print"}, sort_keys=False),
                 encoding="utf-8")
    out = gc.partner_call("research", "survey X", config_path=str(p))
    assert out.status == "degraded"
    assert out.provenance["reviewer_engine"] == "gemini"  # stamped, not silent
    assert "DEGRADED" in capsys.readouterr().err


# --- T5: drift-lint — every wired site still refs the chokepoint ------------
def test_t5_wired_sites_reference_chokepoint():
    missing = [p.name for p in _WIRED_SITES
               if "gemini_companion" not in p.read_text(encoding="utf-8")]
    assert not missing, "wired route-all sites dropped the chokepoint ref: %s" % missing


def test_t5b_js_sites_branch_on_args_route():
    for js in _WIRED_SITES[2:]:
        text = js.read_text(encoding="utf-8")
        assert "route" in text, "%s must branch on args.route" % js.name


# --- T6: factory ships narrow ----------------------------------------------
def test_t6_factory_surface_is_narrow():
    cfg = gpc.resolve()  # shipped gemini-partner.yaml
    assert cfg["route_all_surface"] == ["research", "scout"]
