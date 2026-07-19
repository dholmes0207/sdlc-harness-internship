"""A missing/unrunnable `ccs` binary must degrade LOUD — never a raw crash,
never a job stranded at status "running". Three angles:

1. CcsPrintTransport.run: subprocess.run against a binary that does not
   resolve raises FileNotFoundError (an OSError) today — uncaught, that is a
   raw crash reaching the chokepoint instead of the transport's own
   PartnerError contract.
2. partner_call: a provider that passes LIVE discovery but whose `ccs`
   binary is absent must be refused BEFORE a spawn is even attempted —
   partner_call must return a stamped Degraded, never raise.
3. _run_job (the CLI's job-registry wrapper): the run must always reach a
   terminal record — no job left at status "running" forever.
"""
import sys
from pathlib import Path

import pytest

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import partner_companion as pc  # noqa: E402
import partner_preflight as pf  # noqa: E402
import partner_transport as pt  # noqa: E402

_BASE_CFG = {
    "master": "on", "write": "read_only", "allow_live": "off", "secret_scrub": "warn",
    "purposes": {"review": "review", "adversarial-review": "redteam",
                 "research": "research", "critique": "critique"},
    "timeouts": {"default": 5}, "retry": {"max_attempts": 1, "on_markers": []},
    "cost_warn_usd": 0.50,
}


def _cfg(**over):
    return {**_BASE_CFG, **over}


@pytest.fixture(autouse=True)
def _missing_ccs(monkeypatch, tmp_path):
    # A single-token path that does not resolve to anything runnable — mirrors
    # test_partner_preflight.test_missing_ccs_reports's shape (no shlex tokens).
    monkeypatch.setenv("HARNESS_CCS_CMD", str(tmp_path / "does-not-exist-binary"))
    monkeypatch.setattr(pf, "discover_providers", lambda: ["minimax"])


def test_transport_run_raises_partner_error_not_oserror():
    with pytest.raises(pt.PartnerError):
        pt.CcsPrintTransport().run(composed="hi", mode="plan", session=None,
                                   cwd=None, timeout=5, provider="minimax")


def test_partner_call_degrades_before_spawn_no_raise():
    out = pc.partner_call("review", "hello", "minimax", cfg=_cfg())
    assert out.status == "degraded"
    assert "ccs" in out.reason.lower()
    assert out.provenance["egress_scope"] == "whole-repo-readable"
    assert out.provenance["provider"] == "minimax"


def test_run_job_never_strands_running(tmp_path, monkeypatch):
    import yaml
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))
    cfg_path = tmp_path / "partner.yaml"
    cfg_path.write_text(yaml.safe_dump(_BASE_CFG, sort_keys=False), encoding="utf-8")
    reg = pc.JobRegistry(subdir="partner")
    job_id, out = pc._run_job(reg, "review", "review", "hello", "minimax", "plan",
                              config_path=str(cfg_path))
    rec = reg.latest(job_id)
    assert rec["status"] != "running"
    assert out.status == "degraded"
