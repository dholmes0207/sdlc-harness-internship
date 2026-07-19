"""ONE-chokepoint contract for the ccs partner lane (phase 4): partner_call
stamps provenance with a `ccs:` prefix (never blurred with gemini/main
Claude), validates the provider BEFORE ever spawning, warns (never blocks)
on a secret-looking prompt, reads cost off the ccs result record, and
degrades LOUD on a down transport — never a silent main-Claude fallback.

fixtures/fake_ccs.py mirrors the real polluted-stdout ccs shape (probed live); wired
via HARNESS_CCS_CMD so no real ccs is ever invoked here (dogfood is main's
job, not this fake-driven suite — dogfood-with-real-tool-beats-fake).
"""
import sys
from pathlib import Path

import pytest

_PLUGIN_SCRIPTS = Path(__file__).resolve().parent.parent / "plugins" / "hs" / "scripts"
if str(_PLUGIN_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_PLUGIN_SCRIPTS))

import partner_companion as pc  # noqa: E402
import partner_preflight as pf  # noqa: E402

_FAKE = Path(__file__).resolve().parent / "fixtures" / "fake_ccs.py"

# Canonical, contradiction-free base — mirrors test_partner_config._BASE. Tests copy +
# flip one axis at a time via _cfg(**over).
_BASE_CFG = {
    "master": "on", "write": "read_only", "allow_live": "off", "secret_scrub": "warn",
    "purposes": {"review": "review", "adversarial-review": "redteam",
                 "research": "research", "critique": "critique"},
    "timeouts": {"default": 5}, "retry": {"max_attempts": 1, "on_markers": []},
    "cost_warn_usd": 0.50,
}


def _cfg(**over):
    return {**_BASE_CFG, **over}


class _RefusingTransport:
    """A transport that must never be constructed on a no-spawn path — its
    __init__ alone proves the chokepoint spawned when it should not have."""
    def __init__(self):
        raise AssertionError("CcsPrintTransport must not spawn on this path")


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    monkeypatch.setenv("HARNESS_CCS_CMD", "%s %s" % (sys.executable, _FAKE))
    monkeypatch.delenv("FAKE_CCS_MODE", raising=False)
    # Known discovery list independent of the host's real ~/.ccs.
    monkeypatch.setattr(pf, "discover_providers", lambda: ["minimax"])
    monkeypatch.setenv("HARNESS_STATE_DIR", str(tmp_path / "state"))


def test_stamp_provider_and_model():
    out = pc.partner_call("review", "hello", "minimax", cfg=_cfg())
    assert out.status == "ok"
    assert out.provenance["reviewer_engine"] == "ccs:minimax"
    assert out.provenance["reviewer_model"] == "fake-model-1"


def test_master_off_inert(monkeypatch):
    monkeypatch.setattr(pc, "CcsPrintTransport", _RefusingTransport)
    out = pc.partner_call("review", "hello", "minimax", cfg=_cfg(master="off"))
    assert out.status == "inert"


def test_unknown_provider_degraded_no_spawn(monkeypatch):
    monkeypatch.setattr(pc, "CcsPrintTransport", _RefusingTransport)
    out = pc.partner_call("review", "hello", "bogus-provider", cfg=_cfg())
    assert out.status == "degraded"
    assert "provider unknown" in out.reason


def test_secret_warns_not_block(capsys):
    out = pc.partner_call(
        "review", "creds: sk-ABCDEFGHIJ1234567890KLMNOP", "minimax", cfg=_cfg())
    err = capsys.readouterr().err
    assert "secret" in err.lower()
    assert out.status == "ok"  # warn-only — the call still completes


def test_cost_over_threshold_warns(capsys):
    over = pc.partner_call("review", "x", "minimax", cfg=_cfg(cost_warn_usd=0.001))
    assert over.provenance["cost_over"] is True
    err = capsys.readouterr().err
    assert "cost" in err.lower()

    under = pc.partner_call("review", "x", "minimax", cfg=_cfg(cost_warn_usd=10.0))
    assert under.provenance["cost_over"] is False


def test_down_degraded_loud_no_claude_fallback(monkeypatch):
    monkeypatch.setenv("FAKE_CCS_MODE", "nonzero")
    out = pc.partner_call("review", "x", "minimax", cfg=_cfg())
    assert out.status == "degraded"
    assert not hasattr(out, "content")  # Degraded never carries a fallback answer
    assert out.provenance["reviewer_engine"] == "ccs:minimax"


def test_egress_scope_stamped():
    out = pc.partner_call("review", "x", "minimax", cfg=_cfg())
    assert out.provenance["egress_scope"] == "whole-repo-readable"
